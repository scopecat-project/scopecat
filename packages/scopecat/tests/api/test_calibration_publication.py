from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import httpx2
import pytest
from pydantic import ValidationError
from scopecat_testkit.workflow_fixtures import load_config

from scopecat.api.calibration_publication import (
    CalibrationCohortPublicationPlan,
    CalibrationPublicationDriftError,
    CalibrationPublicationOutcomeUnknown,
    build_calibration_cohort_merge_contribution,
    calibration_cohort_merge_revision_source,
    publish_calibration_cohort,
)
from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureOperations, ProcedureHandle
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.automation.calibration_wire import CalibrationCohortMemberPage
from scopecat.automation.calibrations import (
    CalibrationAttemptRef,
    CalibrationCohort,
    CalibrationCohortMember,
    CalibrationCohortMemberSpec,
    CalibrationCohortSpec,
    CalibrationConfigSourceRef,
    CalibrationDefinitionRef,
    CalibrationMissingSuccessDueReason,
    CalibrationPublicationPolicyRef,
    CalibrationStatus,
    CalibrationSuccessPublication,
    CalibrationSuccessRef,
    CalibrationTargetRef,
    calibration_cohort_member_request_key,
    calibration_cohort_spec_hash,
    calibration_freshness_fingerprint,
    calibration_key,
    scoped_calibration_target,
)
from scopecat.automation.models import (
    AnalysisPublicationOutputRef,
    ProcedureClosure,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureStepOperation,
    RunOutputRef,
    procedure_intent_hash,
)
from scopecat.automation.wire import ProcedureStepAttemptPage
from scopecat.config.registry.records import (
    CalibrationCohortMergeContribution,
    CalibrationCohortMergeRegistrySource,
    CalibrationPublicationOperation,
    ConfigCompositionEvidenceStepRef,
    ConfigCompositionPolicyRef,
    ConfigRegistryEntry,
    ContextConfigRegistrySource,
    ResolvedCalibrationCohortMergeContribution,
    ResolvedVerifiedParameterProposalProofV1,
    VerifiedParameterProposalProofV1,
)
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.daemon.client import (
    DaemonClient,
    DaemonConflictError,
    DaemonNotFoundError,
    DaemonUnavailableError,
)
from scopecat.daemon.wire import (
    CalibrationPublicationCommand,
    CalibrationPublicationReceipt,
)
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.analysis import (
    AnalysisFact,
    AnalysisSubject,
    MeasurementAnalysisRecordInput,
    ProjectAnalysisDecisionReference,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
)
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.config import config_content_hash
from scopecat.records.config_context import (
    ConfigContextMetadata,
    ContextRunConfigSource,
)
from scopecat.records.parameter import ScalarParameterValue
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterValueDelta,
)
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    RunSnapshot,
)
from scopecat.records.sample import SampleBinding

_SCOPE = WorkingPointCalibrationScope(
    workspace_id="test-working-point",
    sample=SampleBinding(
        role="subject",
        sample_id="chip",
        revision=1,
        content_hash="sha256:" + "a" * 64,
        kind="synthetic",
        display_name="Chip",
        context_id="parked",
    ),
)

_NOW = datetime(2026, 8, 18, 10, tzinfo=UTC)
_BASE_CONFIG = load_config()
_RESULT_CONFIG = _BASE_CONFIG.model_copy(update={"id": "candidate"})
_BASE_HASH = config_content_hash(_BASE_CONFIG)
_RESULT_HASH = config_content_hash(_RESULT_CONFIG)
_INPUT_HASH = f"sha256:{'c' * 64}"
_POLICY_HASH = f"sha256:{'d' * 64}"
_DECISION_HASH = f"sha256:{'e' * 64}"
_STEP_HASH = f"sha256:{'f' * 64}"

_PROCEDURE = ProcedureDefinitionRef(
    id="tests.calibration.publish-procedure",
    version="1",
    fingerprint=f"sha256:{'1' * 64}",
)
_DEFINITION = CalibrationDefinitionRef(
    id="tests.calibration.publish",
    version="1",
    fingerprint=f"sha256:{'2' * 64}",
    success_policy="published_result",
)
_BASE_SOURCE = CalibrationConfigSourceRef(
    entry_id="base-entry",
    config_ref="config-registry/entries/base-entry/config.json",
    content_hash=_BASE_HASH,
    scope=_SCOPE,
)
_POLICY = ConfigCompositionPolicyRef(
    id="tests.calibration.merge-policy",
    version="1",
    fingerprint=_POLICY_HASH,
)
_AUTOMATIC_PUBLICATION_POLICY = CalibrationPublicationPolicyRef(
    id="tests.calibration.automatic-publication",
    version="1",
    fingerprint=f"sha256:{'3' * 64}",
    calibration=_DEFINITION,
    composition_policy=_POLICY,
)


def test_publication_plan_and_source_are_deterministic_and_cover_whole_cohort() -> None:
    cohort, page = _cohort_and_members()
    q0, q1 = (_contribution(member) for member in page.items)

    source = calibration_cohort_merge_revision_source(
        cohort=cohort,
        member_page=page,
        composition_policy_ref=_POLICY,
        candidate_id="merged-candidate",
        contributions=(q1, q0),
        expected_result_content_hash=_RESULT_HASH,
    )
    forward = CalibrationCohortPublicationPlan.create(
        source,
        actor="calibration-finalizer",
        note="publish complete cohort",
    )
    reversed_source = source.model_copy(
        update={"contributions": tuple(reversed(source.contributions))}
    )
    reverse = CalibrationCohortPublicationPlan.create(
        type(source).model_validate(reversed_source.model_dump()),
        actor=forward.actor,
        note=forward.note,
    )

    assert forward == reverse
    assert forward.command.operation_id == forward.operation_id
    assert forward.command.source.base == source.base
    assert forward.entry_id.startswith("calibration-merge-")
    assert forward.operation_id.startswith("calibration-cohort-publish:")
    assert (
        CalibrationCohortPublicationPlan.create(
            source,
            actor=forward.actor,
            note="changed audit note",
        ).operation_id
        != forward.operation_id
    )

    with pytest.raises(ValueError, match="cover every cohort member"):
        calibration_cohort_merge_revision_source(
            cohort=cohort,
            member_page=page,
            composition_policy_ref=_POLICY,
            candidate_id="merged-candidate",
            contributions=(q0,),
            expected_result_content_hash=_RESULT_HASH,
        )
    with pytest.raises(ValueError, match="complete exact member page"):
        calibration_cohort_merge_revision_source(
            cohort=cohort,
            member_page=page.model_copy(update={"next_cursor": 1}),
            composition_policy_ref=_POLICY,
            candidate_id="merged-candidate",
            contributions=(q0, q1),
            expected_result_content_hash=_RESULT_HASH,
        )


def test_automatic_plan_revision_fence_does_not_change_identity() -> None:
    cohort, page = _cohort_and_members()
    source = calibration_cohort_merge_revision_source(
        cohort=cohort,
        member_page=page,
        composition_policy_ref=_POLICY,
        candidate_id="merged-candidate",
        contributions=tuple(_contribution(member) for member in page.items),
        expected_result_content_hash=_RESULT_HASH,
    )
    automatic_source = type(source).model_validate(
        {
            **source.model_dump(mode="python"),
            "automatic_publication": _AUTOMATIC_PUBLICATION_POLICY,
        }
    )

    first = CalibrationCohortPublicationPlan.create(
        automatic_source,
        actor="calibration-finalizer",
        expected_finalization_revision=2,
    )
    rebased = CalibrationCohortPublicationPlan.create(
        automatic_source,
        actor=first.actor,
        expected_finalization_revision=3,
    )

    assert first.expected_finalization_revision == 2
    assert rebased.expected_finalization_revision == 3
    assert first.entry_id == rebased.entry_id
    assert first.operation_id == rebased.operation_id
    assert first.command.intent_hash == rebased.command.intent_hash

    invalid = first.model_dump(mode="python")
    invalid["expected_finalization_revision"] = None
    with pytest.raises(ValidationError, match="plan requires an expected"):
        CalibrationCohortPublicationPlan.model_validate(invalid)

    with pytest.raises(ValidationError, match="only valid for automatic"):
        CalibrationCohortPublicationPlan.create(
            source,
            actor="calibration-finalizer",
            expected_finalization_revision=2,
        )


def test_evidence_helper_freezes_one_exact_verification_checkpoint() -> None:
    cohort, page = _cohort_and_members()
    member = page.items[0]
    procedure, session, _ = _evidence_fixture(member, cohort)

    contribution = build_calibration_cohort_merge_contribution(
        cohort=cohort,
        member=member,
        procedure=procedure,
        evidence_step_key="verification",
        decision_output_id="decision",
        result_input_fingerprint=_RESULT_HASH,
        session=session,
    )

    assert contribution.member_id == member.spec.member_id
    assert contribution.proof.evidence_step == ConfigCompositionEvidenceStepRef(
        procedure_run_id=member.procedure_run_id,
        step_key="verification",
        attempt=1,
    )
    assert contribution.proof.decision.analysis_record_id == "verification-analysis-q0"
    assert contribution.proof.decision.output_id == "decision"


def test_evidence_helper_rejects_candidate_lineage_drift() -> None:
    cohort, page = _cohort_and_members()
    member = page.items[0]
    procedure, session, _ = _evidence_fixture(member, cohort)
    candidate = session.runs["candidate-run-q0"]
    snapshot = candidate.snapshot
    assert isinstance(snapshot.config_source, AnalysisCandidateRunConfigSource)
    candidate.snapshot = snapshot.model_copy(
        update={
            "config_source": snapshot.config_source.model_copy(
                update={"proposal_id": "different-proposal"}
            )
        }
    )

    with pytest.raises(ValueError, match="does not own proposal"):
        build_calibration_cohort_merge_contribution(
            cohort=cohort,
            member=member,
            procedure=procedure,
            evidence_step_key="verification",
            decision_output_id="decision",
            result_input_fingerprint=_RESULT_HASH,
            session=session,
        )


def test_publish_reconciles_unknown_and_post_receipt_drift_by_exact_key() -> None:
    cohort, page = _cohort_and_members()
    plan = _plan(cohort, page)
    receipt = _receipt(plan, page)
    transport = httpx2.ConnectError(
        "publish outcome unknown",
        request=httpx2.Request("POST", "http://daemon.test/config/publish"),
    )
    operations = _ConfigOperations(publish_result=transport, lookup_result=receipt)

    assert publish_calibration_cohort(operations, plan) == receipt
    assert operations.lookup_ids == [plan.operation_id]

    drifted = _receipt(plan, page, resolved_candidate_id="drifted-candidate")
    operations = _ConfigOperations(publish_result=drifted, lookup_result=receipt)
    assert publish_calibration_cohort(operations, plan) == receipt
    assert operations.lookup_ids == [plan.operation_id]


def test_publish_unknown_missing_is_typed_and_conflict_does_not_reopen() -> None:
    cohort, page = _cohort_and_members()
    plan = _plan(cohort, page)
    transport = httpx2.ConnectError(
        "publish outcome unknown",
        request=httpx2.Request("POST", "http://daemon.test/config/publish"),
    )
    missing = DaemonNotFoundError(
        "operation missing",
        response=_response(404),
    )
    operations = _ConfigOperations(publish_result=transport, lookup_result=missing)

    with pytest.raises(CalibrationPublicationOutcomeUnknown) as captured:
        publish_calibration_cohort(operations, plan)
    assert captured.value.cause is transport
    assert captured.value.plan == plan

    conflict = DaemonConflictError("stale base", response=_response(409))
    operations = _ConfigOperations(
        publish_result=conflict,
        lookup_result=_receipt(plan, page),
    )
    with pytest.raises(DaemonConflictError, match="stale base"):
        publish_calibration_cohort(operations, plan)
    assert operations.lookup_ids == []


@pytest.mark.parametrize(
    "lookup_kind",
    ["transport", "unavailable", "server-error"],
)
def test_publish_unknown_lookup_failure_preserves_original_outcome(
    lookup_kind: str,
) -> None:
    if lookup_kind == "transport":
        lookup_error: BaseException = httpx2.ConnectError(
            "lookup transport failed",
            request=httpx2.Request(
                "GET",
                "http://daemon.test/config/publish-operation",
            ),
        )
    elif lookup_kind == "unavailable":
        lookup_error = DaemonUnavailableError(
            "lookup unavailable",
            response=_response(503),
        )
    else:
        lookup_error = httpx2.HTTPStatusError(
            "lookup failed",
            request=httpx2.Request(
                "GET",
                "http://daemon.test/config/publish-operation",
            ),
            response=_response(500),
        )
    cohort, page = _cohort_and_members()
    plan = _plan(cohort, page)
    publish_error = httpx2.ConnectError(
        "publish outcome unknown",
        request=httpx2.Request("POST", "http://daemon.test/config/publish"),
    )
    operations = _ConfigOperations(
        publish_result=publish_error,
        lookup_result=lookup_error,
    )

    with pytest.raises(CalibrationPublicationOutcomeUnknown) as captured:
        publish_calibration_cohort(operations, plan)
    assert captured.value.cause is publish_error
    assert captured.value.plan == plan
    assert captured.value.__cause__ is lookup_error


def test_unknown_publish_reconciliation_rejects_exact_operation_drift() -> None:
    cohort, page = _cohort_and_members()
    plan = _plan(cohort, page)
    drifted = _receipt(plan, page, resolved_candidate_id="drifted-candidate")
    operations = _ConfigOperations(
        publish_result=httpx2.ConnectError(
            "publish outcome unknown",
            request=httpx2.Request("POST", "http://daemon.test/config/publish"),
        ),
        lookup_result=drifted,
    )

    with pytest.raises(CalibrationPublicationDriftError):
        publish_calibration_cohort(operations, plan)
    assert operations.publish_calls == 1
    assert operations.lookup_ids == [plan.operation_id]


def test_lab_client_wires_its_exact_procedure_and_read_session() -> None:
    lab = LabClient(cast("DaemonClient", object()))

    assert lab.calibrations._publication_session is lab
    assert lab.calibrations._procedures is lab.procedures


@dataclass(slots=True)
class _ConfigOperations:
    publish_result: CalibrationPublicationReceipt | BaseException
    lookup_result: CalibrationPublicationReceipt | BaseException
    publish_calls: int = 0
    lookup_ids: list[str] | None = None

    def __post_init__(self) -> None:
        self.lookup_ids = []

    def publish_calibration(
        self,
        command: CalibrationPublicationCommand,
    ) -> CalibrationPublicationReceipt:
        del command
        self.publish_calls += 1
        if isinstance(self.publish_result, BaseException):
            raise self.publish_result
        return self.publish_result

    def calibration_publication_operation(
        self,
        operation_id: str,
    ) -> CalibrationPublicationReceipt:
        assert self.lookup_ids is not None
        self.lookup_ids.append(operation_id)
        if isinstance(self.lookup_result, BaseException):
            raise self.lookup_result
        return self.lookup_result


def _cohort_and_members() -> tuple[CalibrationCohort, CalibrationCohortMemberPage]:
    specs = tuple(_member_spec(target_id) for target_id in ("q0", "q1"))
    spec = CalibrationCohortSpec(
        definition=_DEFINITION,
        config_source=_BASE_SOURCE,
        fanout_scope="tests.calibration",
        max_in_flight=2,
        observed_fanout_active_count=0,
        evaluated_at=_NOW,
        observations=tuple(
            CalibrationStatus(calibration_key=member.calibration_key)
            for member in specs
        ),
        members=specs,
    )
    cohort = CalibrationCohort(
        cohort_id="cohort-publish",
        spec=spec,
        spec_hash=calibration_cohort_spec_hash(spec),
        created_at=_NOW,
    )
    members = tuple(
        CalibrationCohortMember(
            cohort_id=cohort.cohort_id,
            index=index,
            spec=member,
            procedure_run_id=f"procedure-{member.target.id}",
            request_key=calibration_cohort_member_request_key(
                cohort.cohort_id,
                member,
            ),
            admitted_at=cohort.created_at,
        )
        for index, member in enumerate(specs)
    )
    return cohort, CalibrationCohortMemberPage(
        cohort_id=cohort.cohort_id,
        items=members,
    )


def _member_spec(target_id: str) -> CalibrationCohortMemberSpec:
    target = scoped_calibration_target(
        CalibrationTargetRef(kind="qubit", id=target_id), _BASE_SOURCE
    )
    input_fingerprint = f"sha256:{target_id[-1] * 64}"
    freshness = calibration_freshness_fingerprint(
        definition=_DEFINITION,
        target=target,
        procedure=_PROCEDURE,
        input_fingerprint=input_fingerprint,
        dependencies=(),
    )
    return CalibrationCohortMemberSpec(
        member_id=f"member-{target_id}",
        calibration_key=calibration_key(_DEFINITION.id, target),
        definition=_DEFINITION,
        target=target,
        procedure=_PROCEDURE,
        intent={"target_id": target_id},
        input_fingerprint=input_fingerprint,
        freshness_fingerprint=freshness,
        due_reasons=(CalibrationMissingSuccessDueReason(),),
    )


def _contribution(
    member: CalibrationCohortMember,
) -> CalibrationCohortMergeContribution:
    target_id = member.spec.target.id
    return CalibrationCohortMergeContribution(
        member_id=member.spec.member_id,
        proof=VerifiedParameterProposalProofV1(
            evidence_step=ConfigCompositionEvidenceStepRef(
                procedure_run_id=member.procedure_run_id,
                step_key="verification",
                attempt=1,
            ),
            decision=ProjectAnalysisDecisionReference(
                analysis_record_id=f"verification-analysis-{target_id}",
                output_id="decision",
                schema_id="tests.acceptance.v1",
                schema_hash=_DECISION_HASH,
            ),
        ),
        result_input_fingerprint=f"sha256:{(str(int(target_id[-1]) + 5)) * 64}",
    )


def _plan(
    cohort: CalibrationCohort,
    page: CalibrationCohortMemberPage,
) -> CalibrationCohortPublicationPlan:
    source = calibration_cohort_merge_revision_source(
        cohort=cohort,
        member_page=page,
        composition_policy_ref=_POLICY,
        candidate_id="merged-candidate",
        contributions=tuple(_contribution(member) for member in page.items),
        expected_result_content_hash=_RESULT_HASH,
    )
    return CalibrationCohortPublicationPlan.create(
        source,
        actor="calibration-finalizer",
        note="publish complete cohort",
    )


def _receipt(
    plan: CalibrationCohortPublicationPlan,
    page: CalibrationCohortMemberPage,
    *,
    resolved_candidate_id: str | None = None,
) -> CalibrationPublicationReceipt:
    source = plan.source
    resolved = CalibrationCohortMergeRegistrySource(
        cohort_id=source.cohort_id,
        spec_hash=source.spec_hash,
        composition_policy_ref=source.composition_policy_ref,
        base=source.base,
        candidate_id=resolved_candidate_id or source.candidate_id,
        contributions=tuple(
            _resolved_contribution(contribution)
            for contribution in source.contributions
        ),
    )
    published_at = _NOW + timedelta(minutes=2)
    entry = ConfigRegistryEntry(
        id=plan.entry_id,
        config_ref=f"config-registry/entries/{plan.entry_id}/config.json",
        content_hash=source.expected_result_content_hash,
        source=ContextConfigRegistrySource(
            context=ConfigContextMetadata(
                sample=_SCOPE.sample,
                working_point_id="parked",
                label="Parked",
                workspace_id=_SCOPE.workspace_id,
                base=source.base.context_ref,
            ),
            publication=resolved,
        ),
        actor=plan.actor,
        note=plan.note,
        recorded_at=published_at,
    )
    command = plan.command
    operation = CalibrationPublicationOperation(
        operation_id=command.operation_id,
        intent_hash=command.intent_hash,
        source_intent_hash=command.source_intent_hash,
        entry_id=command.entry_id,
        base=command.source.base,
        actor=command.actor,
        note=command.note,
        recorded_at=published_at,
    )
    members = {member.spec.member_id: member for member in page.items}
    successes = tuple(
        _published_success(
            member=members[contribution.member_id],
            contribution=contribution,
            plan=plan,
            entry=entry,
            published_at=published_at,
        )
        for contribution in source.contributions
    )
    return CalibrationPublicationReceipt(
        operation=operation,
        entry=entry,
        calibration_successes=successes,
    )


def _published_success(
    *,
    member: CalibrationCohortMember,
    contribution: CalibrationCohortMergeContribution,
    plan: CalibrationCohortPublicationPlan,
    entry: ConfigRegistryEntry,
    published_at: datetime,
) -> CalibrationSuccessRef:
    attempt = CalibrationAttemptRef(
        calibration_key=member.spec.calibration_key,
        cohort_id=member.cohort_id,
        member_id=member.spec.member_id,
        procedure_run_id=member.procedure_run_id,
        definition=member.spec.definition,
        target=member.spec.target,
        procedure=member.spec.procedure,
        input_fingerprint=member.spec.input_fingerprint,
        dependencies=member.spec.dependencies,
        freshness_fingerprint=member.spec.freshness_fingerprint,
        admitted_at=member.admitted_at,
    )
    publication = CalibrationSuccessPublication(
        operation_id=plan.operation_id,
        source_intent_hash=plan.command.source_intent_hash,
        result_input_fingerprint=contribution.result_input_fingerprint,
        result_freshness_fingerprint=calibration_freshness_fingerprint(
            definition=attempt.definition,
            target=attempt.target,
            procedure=attempt.procedure,
            input_fingerprint=contribution.result_input_fingerprint,
            dependencies=attempt.dependencies,
        ),
        result_config_source=CalibrationConfigSourceRef(
            entry_id=entry.id,
            config_ref=entry.config_ref,
            content_hash=entry.content_hash,
            scope=_SCOPE,
        ),
        published_at=published_at,
    )
    return CalibrationSuccessRef(
        attempt=attempt,
        base_config_source=_BASE_SOURCE,
        succeeded_at=_NOW + timedelta(minutes=1),
        publication=publication,
    )


def _resolved_contribution(
    contribution: CalibrationCohortMergeContribution,
) -> ResolvedCalibrationCohortMergeContribution:
    target_id = contribution.member_id.removeprefix("member-")
    return ResolvedCalibrationCohortMergeContribution(
        member_id=contribution.member_id,
        proof=ResolvedVerifiedParameterProposalProofV1(
            evidence_step=contribution.proof.evidence_step,
            baseline_run_id=f"baseline-run-{target_id}",
            fit_analysis_record_id=f"fit-analysis-{target_id}",
            proposal_id=f"proposal-{target_id}",
            candidate_run_id=f"candidate-run-{target_id}",
            decision=contribution.proof.decision,
        ),
        result_input_fingerprint=contribution.result_input_fingerprint,
    )


@dataclass(slots=True)
class _FakeProcedureOperations:
    snapshot_value: ProcedureRun
    attempts: tuple[ProcedureStepAttempt, ...]

    def snapshot(self, procedure_run_id: str) -> ProcedureRun:
        assert procedure_run_id == self.snapshot_value.procedure_run_id
        return self.snapshot_value

    def steps(
        self,
        procedure_run_id: str,
        *,
        limit: int = 50,
        before: int | None = None,
    ) -> ProcedureStepAttemptPage:
        del limit, before
        return ProcedureStepAttemptPage(
            procedure_run_id=procedure_run_id,
            items=self.attempts,
        )


@dataclass(slots=True)
class _FakePublishedAnalysis:
    id: str
    subject: AnalysisSubject
    inputs: tuple[MeasurementAnalysisRecordInput, ...] = ()
    proposals: tuple[ParameterChangeProposal, ...] = ()
    decision: AnalysisFact | None = None

    @property
    def view(self) -> object:
        return SimpleNamespace(analysis=SimpleNamespace(subject=self.subject))

    @property
    def parameter_proposals(self) -> tuple[ParameterChangeProposal, ...]:
        return self.proposals

    def fact(self, output_id: str) -> AnalysisFact:
        if output_id != "decision" or self.decision is None:
            raise KeyError(output_id)
        return self.decision


@dataclass(slots=True)
class _FakeRunHandle:
    snapshot: RunSnapshot
    analyses: dict[str, _FakePublishedAnalysis]

    def published_analysis(self, selector: str) -> PublishedAnalysis:
        return cast("PublishedAnalysis", cast("object", self.analyses[selector]))


@dataclass(slots=True)
class _FakePublicationSession:
    runs: dict[str, _FakeRunHandle]
    project: _FakePublishedAnalysis

    def get_run(self, run: str) -> RunHandle:
        return cast("RunHandle", cast("object", self.runs[run]))

    def published_analysis(self, selector: str) -> PublishedAnalysis:
        assert selector == self.project.id
        return cast("PublishedAnalysis", cast("object", self.project))


def _evidence_fixture(
    member: CalibrationCohortMember,
    cohort: CalibrationCohort,
) -> tuple[ProcedureHandle, _FakePublicationSession, ParameterChangeProposal]:
    target_id = member.spec.target.id
    baseline = RunOutputRef(run_id=f"baseline-run-{target_id}")
    fit = AnalysisPublicationOutputRef(
        subject=RunAnalysisSubject(run_id=baseline.run_id),
        analysis_record_id=f"fit-analysis-{target_id}",
    )
    candidate = RunOutputRef(run_id=f"candidate-run-{target_id}")
    verification = AnalysisPublicationOutputRef(
        subject=ProjectAnalysisSubject(),
        analysis_record_id=f"verification-analysis-{target_id}",
    )
    attempts = (
        _step(member, "baseline", "run", baseline),
        _step(member, "fit", "analysis", fit, inputs=(baseline,)),
        _step(member, "candidate", "run", candidate, inputs=(fit,)),
        _step(
            member,
            "verification",
            "analysis",
            verification,
            inputs=(baseline, candidate),
        ),
    )
    snapshot = ProcedureRun(
        procedure_run_id=member.procedure_run_id,
        request_key=member.request_key,
        definition=member.spec.procedure,
        intent=member.spec.intent,
        intent_hash=procedure_intent_hash(
            member.spec.procedure, member.spec.intent, samples=_SCOPE.sample_selectors()
        ),
        samples=_SCOPE.sample_selectors(),
        resolved_samples=_SCOPE.sample_selectors(),
        revision=5,
        state="closed",
        created_at=_NOW,
        updated_at=_NOW + timedelta(minutes=1),
        closure=ProcedureClosure(
            status="succeeded",
            closed_at=_NOW + timedelta(minutes=1),
        ),
    )
    operations = _FakeProcedureOperations(snapshot, attempts)
    procedure = ProcedureHandle(
        cast("LabProcedureOperations", cast("object", operations)),
        member.procedure_run_id,
    )
    proposal = ParameterChangeProposal(
        id=f"proposal-{target_id}",
        source_run_id=baseline.run_id,
        analysis_record_id=fit.analysis_record_id,
        base_config_id="base-config",
        base_config_content_hash=cohort.spec.config_source.content_hash,
        reason="improve calibration",
        deltas=(
            ParameterValueDelta(
                parameter_id="drive",
                before=ScalarParameterValue(id="drive", value=0.0),
                after=ScalarParameterValue(id="drive", value=0.1),
            ),
        ),
        proposed_at=_NOW,
    )
    fit_analysis = _FakePublishedAnalysis(
        id=fit.analysis_record_id,
        subject=fit.subject,
        proposals=(proposal,),
    )
    baseline_snapshot = RunSnapshot(
        run_id=baseline.run_id,
        samples=(_SCOPE.sample,),
        outcome=RunOutcome(
            run_id=baseline.run_id,
            result="succeeded",
            certainty="known",
            finished_at=_NOW,
        ),
        config_content_hash=_BASE_HASH,
        scientific_binding=bind_scientific_evidence(
            catalog_id="test-store",
            config=_BASE_CONFIG,
            samples=(_SCOPE.sample,),
            sample_revisions={},
        ),
        config_source=ContextRunConfigSource(
            context=_BASE_SOURCE.context_ref,
            content_hash=_BASE_SOURCE.content_hash,
            sample=_SCOPE.sample,
        ),
    )
    candidate_snapshot = RunSnapshot(
        run_id=candidate.run_id,
        samples=(_SCOPE.sample,),
        outcome=RunOutcome(
            run_id=candidate.run_id,
            result="succeeded",
            certainty="known",
            finished_at=_NOW,
        ),
        config_content_hash=_RESULT_HASH,
        scientific_binding=bind_scientific_evidence(
            catalog_id="test-store",
            config=_RESULT_CONFIG,
            samples=(_SCOPE.sample,),
            sample_revisions={},
        ),
        config_source=AnalysisCandidateRunConfigSource(
            source_run_id=baseline.run_id,
            analysis_record_id=fit.analysis_record_id,
            proposal_id=proposal.id,
            base_config_content_hash=_BASE_HASH,
            content_hash=_RESULT_HASH,
        ),
    )
    verification_analysis = _FakePublishedAnalysis(
        id=verification.analysis_record_id,
        subject=verification.subject,
        inputs=(
            _measurement_input("baseline", baseline.run_id),
            _measurement_input("candidate", candidate.run_id),
        ),
        decision=AnalysisFact(
            schema_id="tests.acceptance.v1",
            schema_codec="scopecat.analysis-fact-schema.v1",
            schema_hash=_DECISION_HASH,
            codec="scopecat.python-json.v1",
            value={"accepted": True},
        ),
    )
    session = _FakePublicationSession(
        runs={
            baseline.run_id: _FakeRunHandle(
                snapshot=baseline_snapshot,
                analyses={fit.analysis_record_id: fit_analysis},
            ),
            candidate.run_id: _FakeRunHandle(
                snapshot=candidate_snapshot,
                analyses={},
            ),
        },
        project=verification_analysis,
    )
    return procedure, session, proposal


def _step(
    member: CalibrationCohortMember,
    step_key: str,
    operation: ProcedureStepOperation,
    output: RunOutputRef | AnalysisPublicationOutputRef,
    *,
    inputs: tuple[RunOutputRef | AnalysisPublicationOutputRef, ...] = (),
) -> ProcedureStepAttempt:
    return ProcedureStepAttempt(
        procedure_run_id=member.procedure_run_id,
        step_key=step_key,
        attempt=1,
        operation=operation,
        intent_hash=_STEP_HASH,
        inputs=inputs,
        revision=2,
        state="succeeded",
        started_at=_NOW,
        updated_at=_NOW + timedelta(seconds=1),
        finished_at=_NOW + timedelta(seconds=1),
        output=output,
    )


def _measurement_input(id: str, run_id: str) -> MeasurementAnalysisRecordInput:
    return MeasurementAnalysisRecordInput(
        id=id,
        target=f"runs/{run_id}/measurements",
        content_hash=_INPUT_HASH,
        codec="scopecat.arrow-ipc.v1",
        role=id,
        run_id=run_id,
    )


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(
        status,
        request=httpx2.Request("POST", "http://daemon.test/config/publish"),
    )
