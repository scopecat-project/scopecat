from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import httpx2
import pytest
from pydantic import ValidationError
from scopecat_testkit.workflow_fixtures import load_config, load_invocation

import scopecat.api.procedures as procedures_module
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api._config import LabConfigOperations
from scopecat.api._runner import _DaemonRunner
from scopecat.api.analysis import Analysis, AnalysisContext, AnalysisStep
from scopecat.api.procedures import (
    LabProcedureContext,
    ProcedureLabSession,
    _validate_run_analysis,
)
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ConfigActivationOutputRef,
    ConfigPublishOutputRef,
    InterpretationOutputRef,
    InterpretationRequest,
    InterpretationResponse,
    ProcedureContext,
    ProcedureStepOperation,
    ProcedureStepOutputRef,
    RunOutputRef,
)
from scopecat.automation.worker import ProcedureNeedsAttention
from scopecat.config.candidates import CandidateConfig
from scopecat.config.changes import parameter_change_proposal_from_updates
from scopecat.config.drafts import ConfigDraft
from scopecat.config.registry import (
    CandidateConfigRegistrySource,
    ConfigActivationOperation,
    ConfigPublishOperation,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    CrossRunCandidateAcceptance,
    config_activation_intent_hash,
)
from scopecat.daemon.client import (
    DaemonConflictError,
    DaemonNotFoundError,
    DaemonUnavailableError,
)
from scopecat.daemon.views import RunAnalysisView
from scopecat.daemon.wire import (
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigPublishCommand,
    ConfigPublishReceipt,
)
from scopecat.kernel.quantity import Quantity
from scopecat.records.analysis import (
    AnalysisFact,
    AnalysisRecord,
    AnalysisSubject,
    MeasurementAnalysisRecordInput,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
    analysis_record_id,
)
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.content import ContentEntry, Sha256ContentHash
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.plan_ref import ProcedureChildSubmission
from scopecat.records.run import RunConfigSource, RunSnapshot
from scopecat.runs.selectors import RunSelector


class _ImmediateProcedureContext:
    plan_ref = None
    procedure_run_id = "procedure-test"
    samples: tuple[object, ...] = ()

    def step(
        self,
        step_key: str,
        *,
        operation: ProcedureStepOperation,
        intent_hash: Sha256ContentHash,
        effect: Callable[[str], ProcedureStepOutputRef],
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> ProcedureStepOutputRef:
        del step_key, operation, intent_hash, inputs
        return effect("operation-test")


@dataclass(frozen=True, slots=True)
class _StepCall:
    step_key: str
    operation: ProcedureStepOperation
    intent_hash: Sha256ContentHash
    inputs: tuple[ProcedureStepOutputRef, ...]


class _RecordingProcedureContext:
    procedure_run_id = "procedure-test"

    def __init__(self) -> None:
        self.calls: list[_StepCall] = []

    def step(
        self,
        step_key: str,
        *,
        operation: ProcedureStepOperation,
        intent_hash: Sha256ContentHash,
        effect: Callable[[str], ProcedureStepOutputRef],
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> ProcedureStepOutputRef:
        self.calls.append(
            _StepCall(
                step_key=step_key,
                operation=operation,
                intent_hash=intent_hash,
                inputs=inputs,
            )
        )
        return effect("operation-test")


class _ExistingStepProcedureContext:
    procedure_run_id = "procedure-test"

    def __init__(self, existing: _StepCall) -> None:
        self.existing = existing

    def step(
        self,
        step_key: str,
        *,
        operation: ProcedureStepOperation,
        intent_hash: Sha256ContentHash,
        effect: Callable[[str], ProcedureStepOutputRef],
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> ProcedureStepOutputRef:
        del effect
        requested = _StepCall(step_key, operation, intent_hash, inputs)
        if requested != self.existing:
            raise RuntimeError("procedure step key already has different intent")
        raise AssertionError("test requires a changed step identity")


class _SucceededStepProcedureContext:
    procedure_run_id = "procedure-test"

    def __init__(
        self,
        existing: _StepCall,
        output: ProcedureStepOutputRef,
    ) -> None:
        self.existing = existing
        self.output = output

    def step(
        self,
        step_key: str,
        *,
        operation: ProcedureStepOperation,
        intent_hash: Sha256ContentHash,
        effect: Callable[[str], ProcedureStepOutputRef],
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> ProcedureStepOutputRef:
        del effect
        assert _StepCall(step_key, operation, intent_hash, inputs) == self.existing
        return self.output


class _InterpretationProcedureContext:
    procedure_run_id = "procedure-test"

    def __init__(self) -> None:
        self.request: InterpretationRequest | None = None
        self.inputs: tuple[ProcedureStepOutputRef, ...] = ()

    def interpret(
        self,
        step_key: str,
        *,
        request: InterpretationRequest,
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> InterpretationOutputRef:
        self.request = request
        self.inputs = inputs
        return InterpretationOutputRef(
            procedure_run_id=self.procedure_run_id,
            step_key=step_key,
            request_hash=request.request_hash,
            response=InterpretationResponse(
                actor="analysis-agent",
                actor_kind="ai",
                value={"resonator": "r2", "confidence": 0.84},
                note="isolated dip and expected bias response",
                submitted_at=datetime.now(UTC),
            ),
        )


@dataclass(frozen=True)
class _ResonatorSelection:
    resonator: str
    confidence: float


class _UnavailableAnalysisSession:
    def get_run(self, run: RunSelector | RunHandle) -> RunHandle:
        del run
        raise httpx2.ReadError("analysis evidence is temporarily unavailable")

    def analyze(
        self,
        step: AnalysisStep,
        *,
        key: str | None = None,
    ) -> PublishedAnalysis:
        del step, key
        raise AssertionError("analysis publication is outside this acceptance test")

    def published_analysis(
        self,
        selector: str,
        *,
        sample: str | None = None,
    ) -> PublishedAnalysis:
        del selector, sample
        raise httpx2.ReadError("analysis evidence is temporarily unavailable")


class _ActivationConfig:
    operator = "lab-operator"

    def __init__(
        self,
        receipt: ConfigActivationReceipt,
        *,
        lookup_receipt: ConfigActivationReceipt | None = None,
        activate_error: Exception | None = None,
        lookup_error: Exception | None = None,
    ) -> None:
        self.receipt = receipt
        self.lookup_receipt = lookup_receipt
        self.activate_error = activate_error
        self.lookup_error = lookup_error
        self.activate_calls: list[tuple[str, str, int, str | None, str]] = []
        self.lookup_calls: list[str] = []

    def activate_entry(
        self,
        entry_id: str,
        *,
        operation_id: str,
        expected_generation: int,
        actor: str | None = None,
        note: str = "",
    ) -> ConfigActivationReceipt:
        self.activate_calls.append(
            (entry_id, operation_id, expected_generation, actor, note)
        )
        if self.activate_error is not None:
            raise self.activate_error
        return self.receipt

    def activation_operation(self, operation_id: str) -> ConfigActivationReceipt:
        self.lookup_calls.append(operation_id)
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.lookup_receipt or self.receipt


@dataclass(frozen=True, slots=True)
class _FakeAnalysisRecord:
    subject: AnalysisSubject


@dataclass(frozen=True, slots=True)
class _FakeAnalysisView:
    analysis: _FakeAnalysisRecord


class _ExactPublishedAnalysis:
    def __init__(
        self,
        *,
        record_id: str,
        subject: AnalysisSubject,
        proposals: tuple[ParameterChangeProposal, ...] = (),
        decision: AnalysisFact | None = None,
    ) -> None:
        self.id = record_id
        self.view = _FakeAnalysisView(_FakeAnalysisRecord(subject))
        self.parameter_proposals = proposals
        self._decision = decision

    def fact(self, output_id: str) -> AnalysisFact:
        if output_id != "decision" or self._decision is None:
            raise KeyError(f"analysis has no fact {output_id!r}")
        return self._decision


@dataclass(frozen=True, slots=True)
class _ExactRunAnalyses:
    run_id: str
    publication: _ExactPublishedAnalysis

    def published_analysis(self, record_id: str) -> PublishedAnalysis:
        if record_id != self.publication.id:
            raise KeyError(record_id)
        return cast("PublishedAnalysis", cast("object", self.publication))


class _ExactAnalysisSession:
    def __init__(
        self,
        *,
        candidate: _ExactPublishedAnalysis,
        verification: _ExactPublishedAnalysis,
    ) -> None:
        subject = candidate.view.analysis.subject
        assert isinstance(subject, RunAnalysisSubject)
        self._candidate_run = _ExactRunAnalyses(subject.run_id, candidate)
        self._verification = verification

    def get_run(self, run: RunSelector | RunHandle) -> RunHandle:
        run_id = run.id if isinstance(run, RunHandle) else run
        if run_id != self._candidate_run.run_id:
            raise KeyError(run_id)
        return cast("RunHandle", cast("object", self._candidate_run))

    def analyze(
        self,
        step: AnalysisStep,
        *,
        key: str | None = None,
    ) -> PublishedAnalysis:
        del step, key
        raise AssertionError("analysis publication is outside this acceptance test")

    def published_analysis(
        self,
        selector: str,
        *,
        sample: str | None = None,
    ) -> PublishedAnalysis:
        del sample
        if selector != self._verification.id:
            raise KeyError(selector)
        return cast("PublishedAnalysis", cast("object", self._verification))


class _PublishConfig:
    operator = "lab-operator"

    def __init__(
        self,
        *,
        publish_error: Exception | None = None,
        lookup_error: Exception | None = None,
        receipt_operation_id: str | None = None,
        receipt_base_config_content_hash: Sha256ContentHash | None = None,
    ) -> None:
        self.publish_error = publish_error
        self.lookup_error = lookup_error
        self.receipt_operation_id = receipt_operation_id
        self.receipt_base_config_content_hash = receipt_base_config_content_hash
        self.commands: list[ConfigPublishCommand] = []
        self.lookup_calls: list[str] = []

    def publish_config(self, command: ConfigPublishCommand) -> ConfigPublishReceipt:
        self.commands.append(command)
        if self.publish_error is not None:
            raise self.publish_error
        return _publish_receipt(
            command,
            operation_id=self.receipt_operation_id or command.operation_id,
            base_config_content_hash=self.receipt_base_config_content_hash,
        )

    def publish_operation(self, operation_id: str) -> ConfigPublishReceipt:
        self.lookup_calls.append(operation_id)
        if self.lookup_error is not None:
            raise self.lookup_error
        [command] = self.commands
        return _publish_receipt(
            command,
            operation_id=self.receipt_operation_id or operation_id,
            base_config_content_hash=self.receipt_base_config_content_hash,
        )


@dataclass(frozen=True, slots=True)
class _DirectConfig:
    def resolve_with_source(
        self,
        config: ConfigProfileSnapshot | CandidateConfig,
    ) -> tuple[ConfigProfileSnapshot, RunConfigSource | None]:
        if isinstance(config, CandidateConfig):
            raise AssertionError("test expects a direct config snapshot")
        return config, None


@dataclass(frozen=True, slots=True)
class _TransportFailingRunner:
    planned: object

    def _plan(self, _experiment: object, **_kwargs: object) -> object:
        return self.planned

    def execute(
        self,
        _planned: object,
        *,
        executor_id: str = "notebook",
        submission_id: str | None = None,
        wait_for_resources: bool = False,
        procedure_child: ProcedureChildSubmission | None = None,
    ) -> RunSnapshot:
        del executor_id, submission_id, wait_for_resources
        assert procedure_child is None
        raise httpx2.ReadError("child run response was lost")


def _stub_prepare_run_submission(
    _planned: object,
    *,
    submission_id: str,
) -> tuple[object, str]:
    del submission_id
    return object(), "sha256:" + "a" * 64


@dataclass(frozen=True, slots=True)
class _TestAnalysisStep:
    id: str = "test-analysis"

    def run(self, context: AnalysisContext) -> Analysis:
        del context
        raise AssertionError("analysis execution is outside this transport test")


class _UnknownProjectAnalysisSession:
    def get_run(self, run: RunSelector | RunHandle) -> RunHandle:
        del run
        raise AssertionError("run lookup is outside this project-analysis test")

    def analyze(
        self,
        step: AnalysisStep,
        *,
        key: str | None = None,
    ) -> PublishedAnalysis:
        del step, key
        raise httpx2.ReadError("analysis response was lost")

    def published_analysis(
        self,
        selector: str,
        *,
        sample: str | None = None,
    ) -> PublishedAnalysis:
        del selector, sample
        response = httpx2.Response(
            404,
            request=httpx2.Request("GET", "http://daemon.local/analysis"),
        )
        raise DaemonNotFoundError("analysis does not exist", response=response)


def test_child_run_transport_error_requires_procedure_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    monkeypatch.setattr(
        procedures_module,
        "_prepare_run_submission",
        _stub_prepare_run_submission,
    )
    context = LabProcedureContext(
        cast("ProcedureContext", cast("object", _ImmediateProcedureContext())),
        runner=cast(
            "_DaemonRunner",
            cast("object", _TransportFailingRunner(object())),
        ),
        config=cast("LabConfigOperations", cast("object", _DirectConfig())),
        session=cast("ProcedureLabSession", object()),
    )

    with pytest.raises(ProcedureNeedsAttention, match="transport outcome"):
        context.run("child", load_invocation(), config=config)


def test_analysis_transport_with_no_confirmed_r1_requires_attention() -> None:
    context = LabProcedureContext(
        cast("ProcedureContext", cast("object", _ImmediateProcedureContext())),
        runner=cast("_DaemonRunner", object()),
        config=cast("LabConfigOperations", object()),
        session=_UnknownProjectAnalysisSession(),
    )

    with pytest.raises(ProcedureNeedsAttention, match="unknown outcome"):
        context.analyze_project(
            "fit",
            _TestAnalysisStep(),
            inputs=(),
        )


def test_interpretation_decodes_typed_response_and_retains_exact_reference() -> None:
    durable = _InterpretationProcedureContext()
    context = LabProcedureContext(
        cast("ProcedureContext", cast("object", durable)),
        runner=cast("_DaemonRunner", object()),
        config=cast("LabConfigOperations", object()),
        session=cast("ProcedureLabSession", object()),
    )
    schema = AnalysisFactSchema(
        "tests.resonator-selection.v1",
        _ResonatorSelection,
    )
    survey = RunOutputRef(run_id="readout-s21-survey")

    result = context.interpret(
        "select-resonator",
        title="Select readout resonator",
        instructions="Use the S21 trace and bias response.",
        schema=schema,
        inputs=(survey,),
        response_template=_ResonatorSelection(
            resonator="replace after reviewing the trace",
            confidence=0.0,
        ),
        metadata={"figure": "readout-s21"},
    )

    assert result.value == _ResonatorSelection(resonator="r2", confidence=0.84)
    assert result.ref.response.actor_kind == "ai"
    assert result.ref.response.note.startswith("isolated dip")
    assert durable.inputs == (survey,)
    assert durable.request is not None
    assert durable.request.schema_hash == schema.schema_hash
    assert durable.request.response_template == {
        "resonator": "replace after reviewing the trace",
        "confidence": 0.0,
    }
    assert durable.request.metadata == {"figure": "readout-s21"}


def test_run_analysis_rejects_durable_upstream_mismatch() -> None:
    run_id = "run-subject"
    step_id = "fit-step"
    key = "procedure-fit-key"
    record_id = analysis_record_id(key, 1)
    published = PublishedAnalysis(
        source=object(),  # pyright: ignore[reportArgumentType]
        view=RunAnalysisView(
            run_id=run_id,
            entry=ContentEntry(
                role="record",
                id=record_id,
                kind="analysis",
                content_hash="sha256:analysis-record",
            ),
            analysis=AnalysisRecord(
                subject=RunAnalysisSubject(run_id=run_id),
                title="Fit",
                key=key,
                revision=1,
                publication_hash="sha256:publication",
                step_id=step_id,
                inputs=[
                    MeasurementAnalysisRecordInput(
                        id="measurement-input",
                        target="measurement-dataset",
                        content_hash="sha256:measurements",
                        codec="scopecat.measurement-dataset.v1",
                        role="dataset",
                        run_id=run_id,
                    )
                ],
                outputs=[],
            ),
            published_at=datetime(2026, 8, 18, tzinfo=UTC),
        ),
    )

    with pytest.raises(ValueError, match="upstreams"):
        _validate_run_analysis(
            published,
            run_id=run_id,
            step_id=step_id,
            key=key,
            inputs=(
                RunOutputRef(run_id=run_id),
                RunOutputRef(run_id="run-unpublished-upstream"),
            ),
        )


def test_verified_candidate_publish_uses_exact_refs_and_stable_operation() -> None:
    durable = _RecordingProcedureContext()
    config = _PublishConfig()
    context, candidate_ref, verification, proposal = _verified_candidate_context(
        durable,
        config,
    )

    output = context.accept_verified_candidate(
        "accept",
        candidate_ref,
        proposal_id=proposal.id,
        verification=verification,
        decision_output_id="decision",
        expected_generation=4,
        actor="automation",
        entry_id="drag-beta-procedure-test",
        note="accept verified DRAG beta",
    )

    [command] = config.commands
    assert output == ConfigPublishOutputRef(
        generation=5,
        entry_id=command.entry_id,
        entry_content_hash=config_content_hash(load_config()),
    )
    assert command.operation_id == "operation-test"
    assert isinstance(command.source, CandidateConfigRevisionSource)
    assert isinstance(candidate_ref.subject, RunAnalysisSubject)
    assert command.source.run_id == candidate_ref.subject.run_id
    assert command.source.proposal_id == proposal.id
    acceptance = command.source.acceptance
    assert isinstance(acceptance, CrossRunCandidateAcceptance)
    assert acceptance.decision.analysis_record_id == (verification.analysis_record_id)
    assert acceptance.decision.output_id == "decision"
    [step] = durable.calls
    assert step.step_key == "accept"
    assert step.operation == "config_publish"
    assert step.intent_hash.startswith("sha256:")
    assert step.inputs == (candidate_ref, verification)
    assert config.lookup_calls == []


def test_succeeded_candidate_publish_replays_without_reopening_evidence() -> None:
    first_durable = _RecordingProcedureContext()
    first_config = _PublishConfig()
    first, candidate_ref, verification, proposal = _verified_candidate_context(
        first_durable,
        first_config,
    )
    output = first.accept_verified_candidate(
        "accept",
        candidate_ref,
        proposal_id=proposal.id,
        verification=verification,
        decision_output_id="decision",
        expected_generation=4,
        actor="automation",
        entry_id="drag-beta-procedure-test",
    )
    [existing] = first_durable.calls
    replay_config = _PublishConfig()
    replay = LabProcedureContext(
        cast(
            "ProcedureContext",
            cast(
                "object",
                _SucceededStepProcedureContext(existing, output),
            ),
        ),
        runner=cast("_DaemonRunner", object()),
        config=cast("LabConfigOperations", cast("object", replay_config)),
        session=_UnavailableAnalysisSession(),
    )

    assert (
        replay.accept_verified_candidate(
            "accept",
            candidate_ref,
            proposal_id=proposal.id,
            verification=verification,
            decision_output_id="decision",
            expected_generation=4,
            actor="automation",
            entry_id="drag-beta-procedure-test",
        )
        == output
    )
    assert replay_config.commands == []


def test_candidate_publish_unavailable_evidence_requires_attention() -> None:
    durable = _RecordingProcedureContext()
    config = _PublishConfig()
    _, candidate_ref, verification, proposal = _verified_candidate_context(
        durable,
        config,
    )
    context = LabProcedureContext(
        cast("ProcedureContext", cast("object", durable)),
        runner=cast("_DaemonRunner", object()),
        config=cast("LabConfigOperations", cast("object", config)),
        session=_UnavailableAnalysisSession(),
    )

    with pytest.raises(
        ProcedureNeedsAttention,
        match=r"analysis publication.*reopened exactly",
    ):
        context.accept_verified_candidate(
            "accept",
            candidate_ref,
            proposal_id=proposal.id,
            verification=verification,
            decision_output_id="decision",
            expected_generation=4,
            actor="automation",
            entry_id="drag-beta-procedure-test",
        )

    [step] = durable.calls
    assert step.operation == "config_publish"
    assert config.commands == []


def test_rejected_candidate_verification_records_failed_effect_without_publish() -> (
    None
):
    durable = _RecordingProcedureContext()
    config = _PublishConfig()
    context, candidate_ref, verification, proposal = _verified_candidate_context(
        durable,
        config,
        accepted=False,
    )

    with pytest.raises(ValueError, match="accepted=true"):
        context.accept_verified_candidate(
            "accept",
            candidate_ref,
            proposal_id=proposal.id,
            verification=verification,
            decision_output_id="decision",
            expected_generation=4,
            actor="automation",
            entry_id="drag-beta-procedure-test",
        )

    [step] = durable.calls
    assert step.operation == "config_publish"
    assert step.inputs == (candidate_ref, verification)
    assert config.commands == []


def test_verified_candidate_publish_recovers_transport_loss_by_exact_operation() -> (
    None
):
    config = _PublishConfig(publish_error=httpx2.ReadError("publish response was lost"))
    context, candidate_ref, verification, proposal = _verified_candidate_context(
        _RecordingProcedureContext(),
        config,
    )

    output = context.accept_verified_candidate(
        "accept",
        candidate_ref,
        proposal_id=proposal.id,
        verification=verification,
        decision_output_id="decision",
        expected_generation=4,
        actor="automation",
        entry_id="drag-beta-procedure-test",
    )

    assert output.generation == 5
    assert config.lookup_calls == ["operation-test"]


def test_verified_candidate_publish_unknown_lookup_requires_attention() -> None:
    response = httpx2.Response(
        404,
        request=httpx2.Request(
            "GET",
            "http://daemon.local/config-registry/publish-operations/operation-test",
        ),
    )
    config = _PublishConfig(
        publish_error=httpx2.ReadError("publish response was lost"),
        lookup_error=DaemonNotFoundError(
            "publish operation does not exist",
            response=response,
        ),
    )
    context, candidate_ref, verification, proposal = _verified_candidate_context(
        _RecordingProcedureContext(),
        config,
    )

    with pytest.raises(ProcedureNeedsAttention, match=r"publish outcome.*unknown"):
        context.accept_verified_candidate(
            "accept",
            candidate_ref,
            proposal_id=proposal.id,
            verification=verification,
            decision_output_id="decision",
            expected_generation=4,
            actor="automation",
            entry_id="drag-beta-procedure-test",
        )


def test_verified_candidate_publish_known_conflict_does_not_lookup() -> None:
    response = httpx2.Response(
        409,
        request=httpx2.Request(
            "POST",
            "http://daemon.local/config-registry/publish-operations",
        ),
    )
    config = _PublishConfig(
        publish_error=DaemonConflictError("stale generation", response=response)
    )
    context, candidate_ref, verification, proposal = _verified_candidate_context(
        _RecordingProcedureContext(),
        config,
    )

    with pytest.raises(DaemonConflictError, match="stale generation"):
        context.accept_verified_candidate(
            "accept",
            candidate_ref,
            proposal_id=proposal.id,
            verification=verification,
            decision_output_id="decision",
            expected_generation=4,
            actor="automation",
            entry_id="drag-beta-procedure-test",
        )

    assert config.lookup_calls == []


def test_verified_candidate_publish_mismatched_exact_receipt_requires_attention() -> (
    None
):
    config = _PublishConfig(receipt_operation_id="different-operation")
    context, candidate_ref, verification, proposal = _verified_candidate_context(
        _RecordingProcedureContext(),
        config,
    )

    with pytest.raises(ProcedureNeedsAttention, match=r"publish outcome.*unknown"):
        context.accept_verified_candidate(
            "accept",
            candidate_ref,
            proposal_id=proposal.id,
            verification=verification,
            decision_output_id="decision",
            expected_generation=4,
            actor="automation",
            entry_id="drag-beta-procedure-test",
        )

    assert config.lookup_calls == ["operation-test"]


def test_verified_candidate_publish_rejects_mismatched_base_provenance() -> None:
    config = _PublishConfig(
        receipt_base_config_content_hash="sha256:" + "f" * 64,
    )
    context, candidate_ref, verification, proposal = _verified_candidate_context(
        _RecordingProcedureContext(),
        config,
    )

    with pytest.raises(ProcedureNeedsAttention, match=r"publish outcome.*unknown"):
        context.accept_verified_candidate(
            "accept",
            candidate_ref,
            proposal_id=proposal.id,
            verification=verification,
            decision_output_id="decision",
            expected_generation=4,
            actor="automation",
            entry_id="drag-beta-procedure-test",
        )

    assert config.lookup_calls == ["operation-test"]


def test_config_activation_uses_exact_step_intent_and_operation_id() -> None:
    entry_id = "candidate-entry"
    expected_generation = 4
    note = "activate a reviewed entry"
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id=entry_id,
        expected_generation=expected_generation,
        actor="lab-operator",
        note=note,
    )
    durable = _RecordingProcedureContext()
    config = _ActivationConfig(receipt)
    context = _activation_context(durable, config)
    verification = RunOutputRef(run_id="verification-run")

    output = context.activate_config_entry(
        "activate",
        entry_id,
        expected_generation=expected_generation,
        actor="lab-operator",
        note=note,
        inputs=(verification,),
    )

    assert output == ConfigActivationOutputRef(
        generation=receipt.activation.generation,
        entry_id=entry_id,
        entry_content_hash=receipt.activation.entry_content_hash,
    )
    assert durable.calls == [
        _StepCall(
            step_key="activate",
            operation="config_activation",
            intent_hash=config_activation_intent_hash(
                entry_id=entry_id,
                expected_generation=expected_generation,
                actor="lab-operator",
                note=note,
            ),
            inputs=(verification,),
        )
    ]
    assert config.activate_calls == [
        (
            entry_id,
            "operation-test",
            expected_generation,
            "lab-operator",
            note,
        )
    ]
    assert config.lookup_calls == []


def test_config_activation_recovers_transport_loss_by_exact_operation() -> None:
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    config = _ActivationConfig(
        receipt,
        activate_error=httpx2.ReadError("activation response was lost"),
    )
    context = _activation_context(_RecordingProcedureContext(), config)

    output = context.activate_config_entry(
        "activate",
        "candidate-entry",
        expected_generation=2,
        actor="automation",
    )

    assert output.generation == receipt.activation.generation
    assert config.lookup_calls == ["operation-test"]


@pytest.mark.parametrize(
    "failure",
    ["unavailable", "http-500", "invalid-receipt"],
)
def test_config_activation_recovers_ambiguous_post_failure(
    failure: str,
) -> None:
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    config = _ActivationConfig(
        receipt,
        activate_error=_ambiguous_activation_error(failure, method="POST"),
    )
    context = _activation_context(_RecordingProcedureContext(), config)

    output = context.activate_config_entry(
        "activate",
        "candidate-entry",
        expected_generation=2,
        actor="automation",
    )

    assert output.generation == receipt.activation.generation
    assert config.lookup_calls == ["operation-test"]


def test_config_activation_unknown_after_exact_lookup_requires_attention() -> None:
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    response = httpx2.Response(
        404,
        request=httpx2.Request(
            "GET",
            "http://daemon.local/config-registry/activation-operations/operation-test",
        ),
    )
    config = _ActivationConfig(
        receipt,
        activate_error=httpx2.ReadError("activation response was lost"),
        lookup_error=DaemonNotFoundError(
            "activation operation does not exist",
            response=response,
        ),
    )
    context = _activation_context(_RecordingProcedureContext(), config)

    with pytest.raises(ProcedureNeedsAttention, match=r"outcome.*unknown"):
        context.activate_config_entry(
            "activate",
            "candidate-entry",
            expected_generation=2,
            actor="automation",
        )


def test_config_activation_failed_exact_lookup_requires_attention() -> None:
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    config = _ActivationConfig(
        receipt,
        activate_error=httpx2.ReadError("activation response was lost"),
        lookup_error=httpx2.ReadError("activation lookup response was lost"),
    )
    context = _activation_context(_RecordingProcedureContext(), config)

    with pytest.raises(ProcedureNeedsAttention, match=r"outcome.*unknown"):
        context.activate_config_entry(
            "activate",
            "candidate-entry",
            expected_generation=2,
            actor="automation",
        )


@pytest.mark.parametrize("failure", ["unavailable", "http-500", "invalid-receipt"])
def test_config_activation_ambiguous_exact_lookup_requires_attention(
    failure: str,
) -> None:
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    config = _ActivationConfig(
        receipt,
        activate_error=httpx2.ReadError("activation response was lost"),
        lookup_error=_ambiguous_activation_error(failure, method="GET"),
    )
    context = _activation_context(_RecordingProcedureContext(), config)

    with pytest.raises(ProcedureNeedsAttention, match=r"outcome.*unknown"):
        context.activate_config_entry(
            "activate",
            "candidate-entry",
            expected_generation=2,
            actor="automation",
        )


def test_config_activation_known_conflict_does_not_enter_recovery() -> None:
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    response = httpx2.Response(
        409,
        request=httpx2.Request(
            "POST",
            "http://daemon.local/config-registry/activation-operations",
        ),
    )
    conflict = DaemonConflictError("stale generation", response=response)
    config = _ActivationConfig(receipt, activate_error=conflict)
    context = _activation_context(_RecordingProcedureContext(), config)

    with pytest.raises(DaemonConflictError, match="stale generation"):
        context.activate_config_entry(
            "activate",
            "candidate-entry",
            expected_generation=2,
            actor="automation",
        )

    assert config.lookup_calls == []


def test_config_activation_recovers_a_mismatched_post_receipt_by_exact_lookup() -> None:
    mismatched = _activation_receipt(
        operation_id="different-operation",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    exact = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    config = _ActivationConfig(mismatched, lookup_receipt=exact)
    context = _activation_context(_RecordingProcedureContext(), config)

    output = context.activate_config_entry(
        "activate",
        "candidate-entry",
        expected_generation=2,
        actor="automation",
    )

    assert output.generation == exact.activation.generation
    assert config.lookup_calls == ["operation-test"]


def test_config_activation_rejects_a_mismatched_exact_receipt() -> None:
    receipt = _activation_receipt(
        operation_id="different-operation",
        entry_id="candidate-entry",
        expected_generation=2,
        actor="automation",
    )
    config = _ActivationConfig(receipt)
    context = _activation_context(_RecordingProcedureContext(), config)

    with pytest.raises(ProcedureNeedsAttention, match=r"outcome.*unknown"):
        context.activate_config_entry(
            "activate",
            "candidate-entry",
            expected_generation=2,
            actor="automation",
        )

    assert config.lookup_calls == ["operation-test"]


@pytest.mark.parametrize("changed", ["intent", "inputs"])
def test_config_activation_step_conflict_does_not_execute_effect(
    changed: str,
) -> None:
    existing_input = RunOutputRef(run_id="verification-1")
    requested_input = (
        RunOutputRef(run_id="verification-2") if changed == "inputs" else existing_input
    )
    existing_generation = 2
    requested_generation = 3 if changed == "intent" else existing_generation
    durable = _ExistingStepProcedureContext(
        _StepCall(
            step_key="activate",
            operation="config_activation",
            intent_hash=config_activation_intent_hash(
                entry_id="candidate-entry",
                expected_generation=existing_generation,
                actor="automation",
            ),
            inputs=(existing_input,),
        )
    )
    receipt = _activation_receipt(
        operation_id="operation-test",
        entry_id="candidate-entry",
        expected_generation=requested_generation,
        actor="automation",
    )
    config = _ActivationConfig(receipt)
    context = _activation_context(durable, config)

    with pytest.raises(RuntimeError, match="different intent"):
        context.activate_config_entry(
            "activate",
            "candidate-entry",
            expected_generation=requested_generation,
            actor="automation",
            inputs=(requested_input,),
        )

    assert config.activate_calls == []


def _verified_candidate_context(
    durable: _RecordingProcedureContext,
    config: _PublishConfig,
    *,
    accepted: bool = True,
) -> tuple[
    LabProcedureContext,
    AnalysisPublicationOutputRef,
    AnalysisPublicationOutputRef,
    ParameterChangeProposal,
]:
    source_config = load_config()
    candidate_ref = AnalysisPublicationOutputRef(
        subject=RunAnalysisSubject(run_id="baseline-run"),
        analysis_record_id="analysis-fit-r1",
    )
    verification = AnalysisPublicationOutputRef(
        subject=ProjectAnalysisSubject(),
        analysis_record_id="analysis-verification-r1",
    )
    draft = ConfigDraft(source_config).replace_scalar(
        "drive_frequency",
        Quantity(value=5.1, unit="GHz"),
    )
    proposal = parameter_change_proposal_from_updates(
        source_run_id="baseline-run",
        source_config=source_config,
        analysis_title="DRAG beta fit",
        analysis_record_id=candidate_ref.analysis_record_id,
        proposal_id="drag-beta-fit",
        updates=draft.updates,
        reason="quadratic fit converged",
        confidence=0.9,
    )
    candidate_analysis = _ExactPublishedAnalysis(
        record_id=candidate_ref.analysis_record_id,
        subject=candidate_ref.subject,
        proposals=(proposal,),
    )
    verification_analysis = _ExactPublishedAnalysis(
        record_id=verification.analysis_record_id,
        subject=verification.subject,
        decision=AnalysisFact(
            schema_id="reference-lab.drag-beta-verification.v1",
            schema_codec="scopecat.analysis-fact-schema.v1",
            schema_hash="sha256:" + "9" * 64,
            codec="scopecat.python-json.v1",
            value={"accepted": accepted},
        ),
    )
    session = _ExactAnalysisSession(
        candidate=candidate_analysis,
        verification=verification_analysis,
    )
    return (
        LabProcedureContext(
            cast("ProcedureContext", cast("object", durable)),
            runner=cast("_DaemonRunner", object()),
            config=cast("LabConfigOperations", cast("object", config)),
            session=session,
        ),
        candidate_ref,
        verification,
        proposal,
    )


def _publish_receipt(
    command: ConfigPublishCommand,
    *,
    operation_id: str,
    base_config_content_hash: Sha256ContentHash | None = None,
) -> ConfigPublishReceipt:
    source = command.source
    assert isinstance(source, CandidateConfigRevisionSource)
    content_hash = config_content_hash(load_config())
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref=f"config-registry/entries/{command.entry_id}/config.json",
        content_hash=content_hash,
        source=CandidateConfigRegistrySource(
            run_id=source.run_id,
            proposal_id=source.proposal_id,
            base_config_content_hash=base_config_content_hash or content_hash,
            acceptance=source.acceptance,
        ),
        actor=command.actor,
        note=command.note,
    )
    activation = ConfigRegistryActivationRecord(
        generation=command.expected_generation + 1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        actor=command.actor,
        note=command.note,
    )
    return ConfigPublishReceipt(
        operation=ConfigPublishOperation(
            operation_id=operation_id,
            intent_hash=command.intent_hash,
            source_intent_hash=command.source_intent_hash,
            entry_id=command.entry_id,
            expected_generation=command.expected_generation,
            actor=command.actor,
            note=command.note,
            activation_generation=activation.generation,
        ),
        entry=entry,
        activation=activation,
    )


def _activation_context(
    durable: _RecordingProcedureContext | _ExistingStepProcedureContext,
    config: _ActivationConfig,
) -> LabProcedureContext:
    return LabProcedureContext(
        cast("ProcedureContext", cast("object", durable)),
        runner=cast("_DaemonRunner", object()),
        config=cast("LabConfigOperations", cast("object", config)),
        session=cast("ProcedureLabSession", object()),
    )


def _activation_receipt(
    *,
    operation_id: str,
    entry_id: str,
    expected_generation: int,
    actor: str,
    note: str = "",
) -> ConfigActivationReceipt:
    activation = ConfigRegistryActivationRecord(
        generation=expected_generation + 1,
        action="activation",
        entry_id=entry_id,
        entry_content_hash=config_content_hash(load_config()),
        actor=actor,
        note=note,
    )
    return ConfigActivationReceipt(
        operation=ConfigActivationOperation(
            operation_id=operation_id,
            intent_hash=config_activation_intent_hash(
                entry_id=entry_id,
                expected_generation=expected_generation,
                actor=actor,
                note=note,
            ),
            entry_id=entry_id,
            expected_generation=expected_generation,
            actor=actor,
            note=note,
            activation_generation=activation.generation,
        ),
        activation=activation,
    )


def _ambiguous_activation_error(failure: str, *, method: str) -> Exception:
    request = httpx2.Request(
        method,
        "http://daemon.local/config-registry/activation-operations/operation-test",
    )
    if failure == "unavailable":
        return DaemonUnavailableError(
            "daemon unavailable",
            response=httpx2.Response(503, request=request),
        )
    if failure == "http-500":
        response = httpx2.Response(500, request=request)
        return httpx2.HTTPStatusError(
            "internal server error",
            request=request,
            response=response,
        )
    if failure == "invalid-receipt":
        try:
            ConfigActivationReceipt.model_validate({})
        except ValidationError as error:
            return error
    raise AssertionError(f"unsupported activation failure fixture: {failure}")


@pytest.mark.parametrize(
    ("state", "closure", "outcome", "action"),
    [
        ("waiting_for_input", None, "waiting_for_input", "respond"),
        ("ready", None, "ready", "resume"),
        ("closed", "succeeded", "succeeded", "none"),
        ("closed", "failed", "failed", "none"),
    ],
)
def test_summary_reads_one_bounded_page_and_exposes_closure(
    state: str, closure: str | None, outcome: str, action: str
) -> None:
    from scopecat.api.procedures import LabProcedureOperations, ProcedureHandle
    from scopecat.automation import ProcedureRun, ProcedureStepAttemptPage
    from scopecat.automation.models import ProcedureDefinitionRef, procedure_intent_hash

    definition = ProcedureDefinitionRef(
        id="test", version="1", fingerprint="sha256:" + "1" * 64
    )
    now = datetime.now(UTC)
    run = ProcedureRun.model_validate(
        {
            "procedure_run_id": "p1",
            "request_key": "request1",
            "definition": {
                "id": "test",
                "version": "1",
                "fingerprint": "sha256:" + "1" * 64,
            },
            "intent": {},
            "intent_hash": procedure_intent_hash(definition, {}),
            "revision": 1,
            "state": state,
            "created_at": now,
            "updated_at": now,
            "closure": None
            if closure is None
            else {
                "status": closure,
                "closed_at": now,
                "reason": None if closure == "succeeded" else "test failure",
            },
        }
    )
    calls: list[tuple[object, ...]] = []

    class Operations:
        def snapshot(self, procedure_id: str) -> ProcedureRun:
            calls.append(("snapshot", procedure_id))
            return run

        def steps(
            self, procedure_id: str, *, limit: int, before: int | None
        ) -> ProcedureStepAttemptPage:
            calls.append(("steps", procedure_id, limit, before))
            return ProcedureStepAttemptPage(
                procedure_run_id=procedure_id, next_cursor=17
            )

    handle = ProcedureHandle(
        cast("LabProcedureOperations", cast("object", Operations())), "p1"
    )
    summary = handle.summary(limit=3)
    assert (summary.outcome, summary.next_action) == (outcome, action)
    assert summary.reason == ("test failure" if closure == "failed" else None)
    assert summary.next_cursor == 17
    assert calls == [("snapshot", "p1"), ("steps", "p1", 3, None)]
