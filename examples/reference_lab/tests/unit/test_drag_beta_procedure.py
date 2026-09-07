from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ConfigPublishOutputRef,
    ProcedureStepOutputRef,
    RunOutputRef,
)
from scopecat.kernel.frozen import freeze_json_mapping
from scopecat.records.analysis import ProjectAnalysisSubject, RunAnalysisSubject
from scopecat.records.config import config_content_hash
from scopecat.records.run import ConfigRegistryRunConfigSource, RunConfigSource

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT, bootstrap_config
from reference_lab.workflows.drag_beta_experiment import DragBetaQubit
from reference_lab.workflows.drag_beta_freshness import drag_beta_freshness_calibration
from reference_lab.workflows.drag_beta_procedure import (
    DRAG_BETA_PROCEDURE_ID,
    DRAG_BETA_PROCEDURE_VERSION,
    DragBetaCandidateRejectedError,
    DragBetaProcedureIntent,
    DragBetaVerificationIntent,
    drag_beta_calibration_procedure,
    drag_beta_calibration_request_key,
    drag_beta_verification_procedure,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_MINIMUM_IMPROVEMENT,
    DragBetaVerification,
)
from reference_lab.workflows.temperature_diagnostic import (
    temperature_diagnostic_procedure,
)


def test_application_registers_exact_drag_beta_procedure_source() -> None:
    application = create_application(EXAMPLE_ROOT)

    assert application.procedures.refs == (
        drag_beta_calibration_procedure.ref,
        drag_beta_verification_procedure.ref,
        temperature_diagnostic_procedure.ref,
    )
    assert (
        application.procedures.resolve(drag_beta_calibration_procedure.ref).ref
        == drag_beta_calibration_procedure.ref
    )
    assert drag_beta_calibration_procedure.id == DRAG_BETA_PROCEDURE_ID
    assert drag_beta_calibration_procedure.version == DRAG_BETA_PROCEDURE_VERSION
    assert (
        application.calibrations.resolve(drag_beta_freshness_calibration.ref).ref
        == drag_beta_freshness_calibration.ref
    )


def test_drag_beta_procedure_intent_retains_exact_initial_snapshot() -> None:
    initial_config = bootstrap_config()
    initial_source = ConfigRegistryRunConfigSource(
        selector="active",
        entry_id="config-entry-1",
        config_ref="active@1",
        content_hash=config_content_hash(initial_config),
        registry_generation=1,
    )
    intent = DragBetaProcedureIntent(
        initial_config=initial_config,
        initial_config_source=initial_source,
        minimum_improvement=DRAG_BETA_MINIMUM_IMPROVEMENT,
    )

    encoded = drag_beta_calibration_procedure.encode_intent(intent)
    durable = freeze_json_mapping(encoded, path="intent")
    reconstructed = drag_beta_calibration_procedure.validate_intent(durable)

    assert encoded == {
        "initial_config": initial_config.model_dump(mode="json"),
        "initial_config_source": initial_source.model_dump(mode="json"),
        "minimum_improvement": DRAG_BETA_MINIMUM_IMPROVEMENT,
    }
    assert reconstructed == intent
    assert reconstructed.initial_config == initial_config


def test_drag_beta_request_key_tracks_exact_registry_generation() -> None:
    initial_config = bootstrap_config()
    source = ConfigRegistryRunConfigSource(
        selector="active",
        entry_id="config-entry-1",
        config_ref="active@1",
        content_hash=config_content_hash(initial_config),
        registry_generation=1,
    )

    assert drag_beta_calibration_request_key(source) == (
        "reference-lab.drag-beta-calibration.v6:config-entry-1:1"
    )
    assert drag_beta_calibration_request_key(
        source.model_copy(update={"registry_generation": 2})
    ).endswith(":2")
    with pytest.raises(ValueError, match="registry generation"):
        drag_beta_calibration_request_key(
            source.model_copy(update={"registry_generation": None})
        )


def test_drag_beta_procedure_intent_is_frozen_and_forbids_extra_fields() -> None:
    initial_config = bootstrap_config()
    intent = DragBetaProcedureIntent(
        initial_config=initial_config,
        initial_config_source=ConfigRegistryRunConfigSource(
            selector="active",
            entry_id="config-entry-1",
            config_ref="active@1",
            content_hash=config_content_hash(initial_config),
            registry_generation=1,
        ),
    )

    with pytest.raises(ValidationError, match="frozen"):
        intent.minimum_improvement = 0.0
    with pytest.raises(ValidationError, match="extra"):
        DragBetaProcedureIntent.model_validate(
            {
                **intent.model_dump(mode="python"),
                "accept_candidate": True,
            }
        )


def test_drag_beta_procedure_intent_requires_exact_initial_registry_state() -> None:
    initial_config = bootstrap_config()
    source = ConfigRegistryRunConfigSource(
        selector="active",
        entry_id="config-entry-1",
        config_ref="active@1",
        content_hash=config_content_hash(initial_config),
        registry_generation=1,
    )

    with pytest.raises(ValidationError, match="initial_config_source"):
        DragBetaProcedureIntent.model_validate(
            {"initial_config": initial_config.model_dump(mode="json")}
        )
    with pytest.raises(ValidationError, match="registry generation"):
        DragBetaProcedureIntent(
            initial_config=initial_config,
            initial_config_source=source.model_copy(
                update={"registry_generation": None}
            ),
        )
    with pytest.raises(ValidationError, match="hash does not match"):
        DragBetaProcedureIntent(
            initial_config=initial_config,
            initial_config_source=source.model_copy(
                update={"content_hash": "sha256:" + "f" * 64}
            ),
        )


def test_drag_beta_procedure_builds_exact_durable_dependency_graph() -> None:
    initial_config = bootstrap_config()
    initial_source = ConfigRegistryRunConfigSource(
        selector="accepted",
        entry_id="config-entry-1",
        config_ref="accepted@1",
        content_hash=config_content_hash(initial_config),
        registry_generation=1,
    )
    intent = DragBetaProcedureIntent(
        initial_config=initial_config,
        initial_config_source=initial_source,
    )
    (
        context,
        baseline,
        fit,
        candidate_run,
        verification,
        _verification_publication,
    ) = _procedure_context(accepted=True)

    drag_beta_calibration_procedure.run(context, intent)

    baseline_call, candidate_call = context.run_calls
    assert baseline_call.step_key == "baseline"
    assert baseline_call.config == initial_config
    assert baseline_call.config_source == initial_source
    assert baseline_call.inputs == ()
    assert candidate_call.step_key == "candidate"
    assert candidate_call.inputs == (fit,)
    assert context.run_analysis_calls == [("fit", baseline)]
    assert context.project_analysis_calls == [
        ("verification", (baseline, candidate_run))
    ]
    assert context.published_refs == [fit]
    assert context.accept_calls == [
        _AcceptCall(
            step_key="accept",
            candidate=fit,
            proposal_id="drag-beta-fit",
            verification=verification,
            decision_output_id="decision",
            expected_generation=1,
            actor="nightly-calibration",
            entry_id="drag-beta-procedure-test",
            note="accept the project-verified DRAG candidate",
        )
    ]


def test_drag_beta_procedure_fails_rejected_candidate_in_accept_step() -> None:
    context, _baseline, _fit, _candidate_run, verification, _publication = (
        _procedure_context(accepted=False)
    )
    initial_config = bootstrap_config()

    with pytest.raises(ValueError, match="accepted=true"):
        drag_beta_calibration_procedure.run(
            context,
            DragBetaProcedureIntent(
                initial_config=initial_config,
                initial_config_source=ConfigRegistryRunConfigSource(
                    selector="active",
                    entry_id="config-entry-1",
                    config_ref="active@1",
                    content_hash=config_content_hash(initial_config),
                    registry_generation=1,
                ),
            ),
        )

    assert context.project_analysis_calls == [
        ("verification", (context.baseline, context.candidate_run))
    ]
    assert context.published_refs == [context.fit]
    assert context.accept_calls == [
        _AcceptCall(
            step_key="accept",
            candidate=context.fit,
            proposal_id="drag-beta-fit",
            verification=verification,
            decision_output_id="decision",
            expected_generation=1,
            actor="nightly-calibration",
            entry_id="drag-beta-procedure-test",
            note="accept the project-verified DRAG candidate",
        )
    ]


def test_verify_only_drag_beta_member_has_no_config_publish_step() -> None:
    context, baseline, fit, candidate_run, verification, _publication = (
        _procedure_context(accepted=True)
    )

    drag_beta_verification_procedure.run(
        context,
        _verification_intent("q1"),
    )

    assert tuple(call.step_key for call in context.run_calls) == (
        "baseline",
        "candidate",
    )
    assert context.run_analysis_calls == [("fit", baseline)]
    assert context.project_analysis_calls == [
        ("verification", (baseline, candidate_run))
    ]
    assert context.published_refs == [fit, verification]
    assert context.accept_calls == []


def test_verify_only_drag_beta_rejection_is_a_known_failed_result() -> None:
    context, _baseline, _fit, _candidate_run, _verification, _publication = (
        _procedure_context(accepted=False)
    )

    with pytest.raises(DragBetaCandidateRejectedError, match="accepted=false"):
        drag_beta_verification_procedure.run(
            context,
            _verification_intent("q0"),
        )

    assert context.accept_calls == []


def _verification_intent(qubit: DragBetaQubit) -> DragBetaVerificationIntent:
    initial_config = bootstrap_config()
    return DragBetaVerificationIntent(
        qubit=qubit,
        initial_config=initial_config,
        initial_config_source=ConfigRegistryRunConfigSource(
            selector="active",
            entry_id="config-entry-1",
            config_ref="active@1",
            content_hash=config_content_hash(initial_config),
            registry_generation=1,
        ),
    )


def _procedure_context(
    *,
    accepted: bool,
) -> tuple[
    _RecordingProcedureContext,
    RunOutputRef,
    AnalysisPublicationOutputRef,
    RunOutputRef,
    AnalysisPublicationOutputRef,
    _FakePublishedAnalysis,
]:
    baseline = RunOutputRef(run_id="baseline-run")
    fit = AnalysisPublicationOutputRef(
        subject=RunAnalysisSubject(run_id=baseline.run_id),
        analysis_record_id="fit-analysis-record",
    )
    candidate_run = RunOutputRef(run_id="candidate-run")
    verification = AnalysisPublicationOutputRef(
        subject=ProjectAnalysisSubject(),
        analysis_record_id="verification-analysis-record",
    )
    fit_publication = _FakePublishedAnalysis(
        candidate=_FakeCandidate(proposal_id="drag-beta-fit")
    )
    verification_publication = _FakePublishedAnalysis(
        decision=DragBetaVerification(
            baseline_mean_probability_1=0.02,
            candidate_mean_probability_1=0.01 if accepted else 0.02,
            improvement=0.01 if accepted else 0.0,
            minimum_improvement=DRAG_BETA_MINIMUM_IMPROVEMENT,
            accepted=accepted,
        )
    )
    context = _RecordingProcedureContext(
        baseline=baseline,
        fit=fit,
        candidate_run=candidate_run,
        verification=verification,
        fit_publication=fit_publication,
        verification_publication=verification_publication,
    )
    return (
        context,
        baseline,
        fit,
        candidate_run,
        verification,
        verification_publication,
    )


@dataclass(frozen=True, slots=True)
class _RunCall:
    step_key: str
    config: object
    config_source: RunConfigSource | None
    inputs: tuple[ProcedureStepOutputRef, ...]


@dataclass(frozen=True, slots=True)
class _AcceptCall:
    step_key: str
    candidate: AnalysisPublicationOutputRef
    proposal_id: str
    verification: AnalysisPublicationOutputRef
    decision_output_id: str
    expected_generation: int
    actor: str
    entry_id: str
    note: str


@dataclass(frozen=True, slots=True)
class _FakeCandidate:
    proposal_id: str


@dataclass(frozen=True, slots=True)
class _FakeRunHandle:
    id: str


class _FakePublishedAnalysis:
    def __init__(
        self,
        *,
        candidate: object | None = None,
        decision: DragBetaVerification | None = None,
    ) -> None:
        self._candidate = candidate
        self._decision = decision

    def candidate_config(self) -> object:
        if self._candidate is None:
            raise AssertionError("publication has no candidate")
        return self._candidate

    def verification_accepted(self) -> bool:
        if self._decision is None:
            raise AssertionError("publication has no verification decision")
        return self._decision.accepted

    def fact_as(
        self,
        id: str,
        schema: object,
    ) -> DragBetaVerification:
        del schema
        if id != "decision" or self._decision is None:
            raise AssertionError("publication has no verification decision")
        return self._decision


class _RecordingProcedureContext:
    procedure_run_id = "procedure-test"

    def __init__(
        self,
        *,
        baseline: RunOutputRef,
        fit: AnalysisPublicationOutputRef,
        candidate_run: RunOutputRef,
        verification: AnalysisPublicationOutputRef,
        fit_publication: _FakePublishedAnalysis,
        verification_publication: _FakePublishedAnalysis,
    ) -> None:
        self.baseline = baseline
        self.fit = fit
        self.candidate_run = candidate_run
        self.verification = verification
        self._fit_publication = fit_publication
        self._verification_publication = verification_publication
        self.run_calls: list[_RunCall] = []
        self.run_analysis_calls: list[tuple[str, RunOutputRef]] = []
        self.project_analysis_calls: list[
            tuple[str, tuple[ProcedureStepOutputRef, ...]]
        ] = []
        self.published_refs: list[AnalysisPublicationOutputRef] = []
        self.accept_calls: list[_AcceptCall] = []

    def run(
        self,
        step_key: str,
        experiment: object,
        *,
        config: object,
        config_source: RunConfigSource | None = None,
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
        name: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> RunOutputRef:
        del experiment, name, tags
        self.run_calls.append(
            _RunCall(
                step_key=step_key,
                config=config,
                config_source=config_source,
                inputs=inputs,
            )
        )
        if step_key == "baseline":
            return self.baseline
        if step_key == "candidate":
            return self.candidate_run
        raise AssertionError(f"unexpected run step: {step_key}")

    def analyze_run(
        self,
        step_key: str,
        run: RunOutputRef,
        analysis: object,
    ) -> AnalysisPublicationOutputRef:
        del analysis
        self.run_analysis_calls.append((step_key, run))
        return self.fit

    def analyze_project(
        self,
        step_key: str,
        analysis: object,
        *,
        inputs: tuple[ProcedureStepOutputRef, ...],
    ) -> AnalysisPublicationOutputRef:
        del analysis
        self.project_analysis_calls.append((step_key, inputs))
        return self.verification

    def run_handle(self, ref: RunOutputRef) -> _FakeRunHandle:
        return _FakeRunHandle(ref.run_id)

    def published_analysis(
        self,
        ref: AnalysisPublicationOutputRef,
    ) -> _FakePublishedAnalysis:
        self.published_refs.append(ref)
        if ref == self.fit:
            return self._fit_publication
        if ref == self.verification:
            return self._verification_publication
        raise AssertionError(f"unexpected analysis reference: {ref}")

    def accept_verified_candidate(
        self,
        step_key: str,
        candidate_ref: AnalysisPublicationOutputRef,
        *,
        proposal_id: str,
        verification: AnalysisPublicationOutputRef,
        decision_output_id: str,
        expected_generation: int,
        actor: str,
        entry_id: str,
        note: str = "",
    ) -> ConfigPublishOutputRef:
        self.accept_calls.append(
            _AcceptCall(
                step_key=step_key,
                candidate=candidate_ref,
                proposal_id=proposal_id,
                verification=verification,
                decision_output_id=decision_output_id,
                expected_generation=expected_generation,
                actor=actor,
                entry_id=entry_id,
                note=note,
            )
        )
        if not self._verification_publication.verification_accepted():
            raise ValueError(
                "candidate verification decision must contain accepted=true"
            )
        return ConfigPublishOutputRef(
            generation=expected_generation + 1,
            entry_id=entry_id,
            entry_content_hash="sha256:" + "8" * 64,
        )
