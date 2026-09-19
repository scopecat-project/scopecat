"""Exact project-side finalization of one completed calibration cohort."""

from __future__ import annotations

from typing import Annotated, Protocol, Self

import httpx2
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from scopecat.api.procedures import ProcedureHandle
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.automation.calibration_wire import CalibrationCohortMemberPage
from scopecat.automation.calibrations import (
    CalibrationCohort,
    CalibrationCohortMember,
    CalibrationConfigSourceRef,
)
from scopecat.automation.models import (
    AnalysisPublicationOutputRef,
    ProcedureStepAttempt,
    ProcedureStepOutputRef,
    RunOutputRef,
)
from scopecat.config.registry.records import (
    CalibrationCohortMergeContribution,
    CalibrationCohortMergeRegistrySource,
    ConfigCompositionEvidenceStepRef,
    ConfigCompositionPolicyRef,
    ContextConfigRegistrySource,
    ResolvedCalibrationCohortMergeContribution,
    VerifiedParameterProposalProofV1,
)
from scopecat.daemon.client import (
    DaemonNotFoundError,
    DaemonUnavailableError,
)
from scopecat.daemon.wire import (
    CalibrationCohortMergeRevisionSource,
    CalibrationPublicationCommand,
    CalibrationPublicationReceipt,
)
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.records.analysis import (
    MeasurementAnalysisRecordInput,
    ProjectAnalysisDecisionReference,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
)
from scopecat.records.calibration_scope import WorkingPointCalibrationScope
from scopecat.records.config import ConfigContentHash
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
)

type _NonEmptyText = Annotated[str, Field(min_length=1)]

_PUBLICATION_ENTRY_ID_CODEC = "scopecat.calibration-cohort-publication-entry.v1"
_PUBLICATION_ENTRY_ID_PREFIX = "calibration-merge-"
_PUBLICATION_OPERATION_ID_PREFIX = "calibration-cohort-publish:"
_OPERATION_ID_PROBE = "calibration-cohort-publish:identity-probe"


class _PublicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CalibrationCohortPublicationPlan(_PublicationModel):
    """One frozen merge publication and its deterministic replay identities."""

    source: CalibrationCohortMergeRevisionSource
    actor: _NonEmptyText
    note: str = ""
    expected_finalization_revision: int | None = Field(
        default=None,
        ge=1,
    )
    entry_id: _NonEmptyText
    operation_id: _NonEmptyText

    @field_validator("actor", "entry_id", "operation_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("calibration publication identity must be non-empty")
        return value

    @classmethod
    def create(
        cls,
        source: CalibrationCohortMergeRevisionSource,
        *,
        actor: str,
        note: str = "",
        expected_finalization_revision: int | None = None,
    ) -> Self:
        entry_id = calibration_cohort_publication_entry_id(
            source,
            actor=actor,
            note=note,
        )
        operation_id = calibration_cohort_publication_operation_id(
            source,
            actor=actor,
            note=note,
            entry_id=entry_id,
            expected_finalization_revision=expected_finalization_revision,
        )
        return cls(
            source=source,
            actor=actor,
            note=note,
            expected_finalization_revision=expected_finalization_revision,
            entry_id=entry_id,
            operation_id=operation_id,
        )

    @model_validator(mode="after")
    def validate_derived_identity(self) -> CalibrationCohortPublicationPlan:
        automatic_merge = self.source.automatic_publication is not None
        if automatic_merge and self.expected_finalization_revision is None:
            raise ValueError(
                "automatic calibration publication plan requires an expected "
                "finalization revision"
            )
        if not automatic_merge and self.expected_finalization_revision is not None:
            raise ValueError(
                "expected calibration finalization revision is only valid for "
                "automatic calibration publication plans"
            )
        expected_entry_id = calibration_cohort_publication_entry_id(
            self.source,
            actor=self.actor,
            note=self.note,
        )
        if self.entry_id != expected_entry_id:
            raise ValueError(
                "calibration publication entry id must cover its exact intent"
            )
        expected_operation_id = calibration_cohort_publication_operation_id(
            self.source,
            actor=self.actor,
            note=self.note,
            entry_id=self.entry_id,
            expected_finalization_revision=self.expected_finalization_revision,
        )
        if self.operation_id != expected_operation_id:
            raise ValueError(
                "calibration publication operation id must cover its exact intent"
            )
        return self

    @property
    def command(self) -> CalibrationPublicationCommand:
        return CalibrationPublicationCommand(
            operation_id=self.operation_id,
            source=self.source,
            actor=self.actor,
            expected_finalization_revision=self.expected_finalization_revision,
            entry_id=self.entry_id,
            note=self.note,
        )


class CalibrationPublicationOutcomeUnknown(RuntimeError):
    """A retry-safe publish may have committed but could not be reopened."""

    def __init__(
        self,
        plan: CalibrationCohortPublicationPlan,
        *,
        cause: BaseException,
    ) -> None:
        super().__init__(
            "calibration publication outcome for operation "
            f"{plan.operation_id!r} is unknown"
        )
        self.plan = plan
        self.cause = cause


class CalibrationPublicationDriftError(RuntimeError):
    """A publication receipt does not match its exact project-side plan."""


class _CalibrationPublicationOperations(Protocol):
    def publish_calibration(
        self,
        command: CalibrationPublicationCommand,
    ) -> CalibrationPublicationReceipt: ...

    def calibration_publication_operation(
        self,
        operation_id: str,
    ) -> CalibrationPublicationReceipt: ...


class CalibrationPublicationReadSession(Protocol):
    """Narrow exact-read surface needed to resolve verification evidence."""

    def get_run(self, run: str) -> RunHandle: ...

    def published_analysis(self, selector: str) -> PublishedAnalysis: ...


def calibration_cohort_publication_entry_id(
    source: CalibrationCohortMergeRevisionSource,
    *,
    actor: str,
    note: str = "",
) -> str:
    """Derive a registry-safe entry id from the complete publish meaning."""

    digest = stable_content_hash(
        {
            "codec": _PUBLICATION_ENTRY_ID_CODEC,
            "source": source.model_dump(mode="json"),
            "actor": actor,
            "note": note,
        }
    )
    return f"{_PUBLICATION_ENTRY_ID_PREFIX}{digest}"


def calibration_cohort_publication_operation_id(
    source: CalibrationCohortMergeRevisionSource,
    *,
    actor: str,
    entry_id: str,
    note: str = "",
    expected_finalization_revision: int | None = None,
) -> str:
    """Derive the replay key from the final config-publish intent hash."""

    probe = CalibrationPublicationCommand(
        operation_id=_OPERATION_ID_PROBE,
        source=source,
        actor=actor,
        expected_finalization_revision=expected_finalization_revision,
        entry_id=entry_id,
        note=note,
    )
    digest = probe.intent_hash.removeprefix("sha256:")
    return f"{_PUBLICATION_OPERATION_ID_PREFIX}{digest}"


def calibration_cohort_merge_revision_source(
    *,
    cohort: CalibrationCohort,
    member_page: CalibrationCohortMemberPage,
    composition_policy_ref: ConfigCompositionPolicyRef,
    candidate_id: str,
    contributions: tuple[CalibrationCohortMergeContribution, ...],
    expected_result_content_hash: ConfigContentHash,
) -> CalibrationCohortMergeRevisionSource:
    """Bind a merge source to the complete exact member page of one cohort."""

    members = _complete_cohort_members(cohort, member_page)
    members_by_id = {member.spec.member_id: member for member in members}
    contributions_by_id = {
        contribution.member_id: contribution for contribution in contributions
    }
    if contributions_by_id.keys() != members_by_id.keys():
        raise ValueError(
            "calibration merge contributions must cover every cohort member"
        )
    for member_id, member in members_by_id.items():
        if member.spec.definition.success_policy != "published_result":
            raise ValueError(
                "calibration merge members must require a published result"
            )
        if (
            contributions_by_id[member_id].proof.evidence_step.procedure_run_id
            != member.procedure_run_id
        ):
            raise ValueError(
                "calibration merge contribution must match its exact member run"
            )

    base = cohort.spec.config_source
    return CalibrationCohortMergeRevisionSource(
        cohort_id=cohort.cohort_id,
        spec_hash=cohort.spec_hash,
        automatic_publication=cohort.spec.automatic_publication,
        composition_policy_ref=composition_policy_ref,
        base=base,
        candidate_id=candidate_id,
        contributions=contributions,
        expected_result_content_hash=expected_result_content_hash,
    )


def build_calibration_cohort_merge_contribution(
    *,
    cohort: CalibrationCohort,
    member: CalibrationCohortMember,
    procedure: ProcedureHandle,
    evidence_step_key: str,
    decision_output_id: str,
    result_input_fingerprint: Sha256ContentHash,
    session: CalibrationPublicationReadSession,
) -> CalibrationCohortMergeContribution:
    """Freeze one exact candidate-verification checkpoint into merge evidence."""

    _validate_member_procedure(cohort, member, procedure)
    evidence_attempt = _successful_step(procedure, evidence_step_key)
    evidence = _require_output(
        evidence_attempt,
        AnalysisPublicationOutputRef,
        operation="analysis",
    )
    if not isinstance(evidence.subject, ProjectAnalysisSubject):
        raise ValueError("calibration evidence must be a project analysis")
    if len(evidence_attempt.inputs) != 2 or not all(
        isinstance(item, RunOutputRef) for item in evidence_attempt.inputs
    ):
        raise ValueError("calibration evidence must reference exactly two runs")
    evidence_run_ids = tuple(
        item.run_id
        for item in evidence_attempt.inputs
        if isinstance(item, RunOutputRef)
    )

    evidence_analysis = session.published_analysis(evidence.analysis_record_id)
    _require_exact_analysis(evidence_analysis, evidence)
    analysis_run_ids = tuple(
        item.run_id
        for item in evidence_analysis.inputs
        if isinstance(item, MeasurementAnalysisRecordInput)
    )
    if (
        len(evidence_analysis.inputs) != 2
        or len(analysis_run_ids) != 2
        or set(analysis_run_ids) != set(evidence_run_ids)
    ):
        raise ValueError(
            "calibration evidence analysis must consume its exact two step inputs"
        )

    run_handles = tuple(session.get_run(run_id) for run_id in evidence_run_ids)
    cohort_base = cohort.spec.config_source
    baseline_handles = tuple(
        handle for handle in run_handles if _matches_cohort_base(handle, cohort_base)
    )
    candidate_handles = tuple(
        handle
        for handle in run_handles
        if isinstance(handle.snapshot.config_source, AnalysisCandidateRunConfigSource)
    )
    if len(baseline_handles) != 1 or len(candidate_handles) != 1:
        raise ValueError(
            "calibration evidence must uniquely identify one baseline and candidate"
        )
    baseline_handle = baseline_handles[0]
    candidate_handle = candidate_handles[0]
    baseline_snapshot = baseline_handle.snapshot
    candidate_snapshot = candidate_handle.snapshot
    candidate_source = candidate_snapshot.config_source
    assert isinstance(candidate_source, AnalysisCandidateRunConfigSource)

    if (
        baseline_snapshot.outcome is None
        or baseline_snapshot.outcome.result != "succeeded"
        or candidate_snapshot.outcome is None
        or candidate_snapshot.outcome.result != "succeeded"
        or candidate_source.source_run_id != baseline_snapshot.run_id
        or candidate_source.base_config_content_hash != cohort_base.content_hash
    ):
        raise ValueError("calibration evidence run lineage does not match its base")

    fit_analysis = baseline_handle.published_analysis(
        candidate_source.analysis_record_id
    )
    if fit_analysis.view.analysis.subject != RunAnalysisSubject(
        run_id=baseline_snapshot.run_id
    ):
        raise ValueError("calibration fit must be owned by its exact baseline run")
    proposal = _parameter_proposal(fit_analysis, candidate_source.proposal_id)
    if (
        proposal.source_run_id != baseline_snapshot.run_id
        or proposal.analysis_record_id != candidate_source.analysis_record_id
        or proposal.base_config_content_hash != cohort_base.content_hash
        or candidate_source.proposal_id != proposal.id
    ):
        raise ValueError("calibration candidate run must use its exact fit proposal")

    decision = evidence_analysis.fact(decision_output_id)
    if (
        not isinstance(decision.value, dict)
        or decision.value.get("accepted") is not True
    ):
        raise ValueError("calibration verification decision must contain accepted=true")

    return CalibrationCohortMergeContribution(
        member_id=member.spec.member_id,
        proof=VerifiedParameterProposalProofV1(
            evidence_step=ConfigCompositionEvidenceStepRef(
                procedure_run_id=member.procedure_run_id,
                step_key=evidence_attempt.step_key,
                attempt=evidence_attempt.attempt,
            ),
            decision=ProjectAnalysisDecisionReference(
                analysis_record_id=evidence.analysis_record_id,
                output_id=decision_output_id,
                schema_id=decision.schema_id,
                schema_hash=decision.schema_hash,
            ),
        ),
        result_input_fingerprint=result_input_fingerprint,
    )


def publish_calibration_cohort(
    config: _CalibrationPublicationOperations,
    plan: CalibrationCohortPublicationPlan,
) -> CalibrationPublicationReceipt:
    """Publish once, reconciling only outcomes that may have committed."""

    try:
        receipt = config.publish_calibration(plan.command)
        _validate_publication_receipt(receipt, plan)
        return receipt
    except (httpx2.TransportError, DaemonUnavailableError, ValidationError) as error:
        return _reconcile_unknown_publication(config, plan, cause=error)
    except httpx2.HTTPStatusError as error:
        if error.response.status_code < 500:
            raise
        return _reconcile_unknown_publication(config, plan, cause=error)
    except CalibrationPublicationDriftError as error:
        return _reconcile_unknown_publication(config, plan, cause=error)


def _reopen_calibration_cohort_publication(
    config: _CalibrationPublicationOperations,
    plan: CalibrationCohortPublicationPlan,
) -> CalibrationPublicationReceipt:
    """Read and validate the exact durable result without issuing a publish."""

    try:
        receipt = config.calibration_publication_operation(plan.operation_id)
    except ValidationError as error:
        raise CalibrationPublicationDriftError(
            "calibration publication durable receipt is invalid"
        ) from error
    _validate_publication_receipt(receipt, plan)
    return receipt


def _complete_cohort_members(
    cohort: CalibrationCohort,
    page: CalibrationCohortMemberPage,
) -> tuple[CalibrationCohortMember, ...]:
    if page.cohort_id != cohort.cohort_id or page.next_cursor is not None:
        raise ValueError("calibration merge requires one complete exact member page")
    if len(page.items) != len(cohort.spec.members):
        raise ValueError("calibration merge member page must cover the whole cohort")
    for index, (member, spec) in enumerate(
        zip(page.items, cohort.spec.members, strict=True)
    ):
        if (
            member.cohort_id != cohort.cohort_id
            or member.index != index
            or member.spec != spec
            or member.admitted_at != cohort.created_at
        ):
            raise ValueError(
                "calibration merge member page does not match its exact cohort"
            )
    return page.items


def _validate_member_procedure(
    cohort: CalibrationCohort,
    member: CalibrationCohortMember,
    procedure: ProcedureHandle,
) -> None:
    if member.cohort_id != cohort.cohort_id:
        raise ValueError("calibration member does not belong to its cohort")
    try:
        expected_spec = cohort.spec.members[member.index]
    except IndexError:
        raise ValueError("calibration member index is outside its cohort") from None
    if expected_spec != member.spec:
        raise ValueError("calibration member does not match its exact cohort spec")
    if member.admitted_at != cohort.created_at:
        raise ValueError("calibration member admission does not match its cohort")
    if member.spec.definition.success_policy != "published_result":
        raise ValueError("calibration contribution requires published-result policy")
    if procedure.id != member.procedure_run_id:
        raise ValueError("procedure handle does not match the calibration member")
    snapshot = procedure.snapshot
    if (
        snapshot.procedure_run_id != member.procedure_run_id
        or snapshot.request_key != member.request_key
        or snapshot.definition != member.spec.procedure
        or snapshot.intent != member.spec.intent
        or snapshot.samples != cohort.spec.config_source.scope.sample_selectors()
        or snapshot.state != "closed"
        or snapshot.closure is None
        or snapshot.closure.status != "succeeded"
    ):
        raise ValueError(
            "calibration contribution requires its exact successful procedure"
        )


def _successful_step(
    procedure: ProcedureHandle,
    step_key: str,
) -> ProcedureStepAttempt:
    attempt = procedure.step(step_key)
    if (
        attempt.procedure_run_id != procedure.id
        or attempt.step_key != step_key
        or attempt.state != "succeeded"
        or attempt.output is None
    ):
        raise ValueError(f"calibration step {step_key!r} is not successful")
    return attempt


def _require_output[OutputT: ProcedureStepOutputRef](
    attempt: ProcedureStepAttempt,
    output_type: type[OutputT],
    *,
    operation: str,
) -> OutputT:
    output = attempt.output
    if attempt.operation != operation or not isinstance(output, output_type):
        raise TypeError(
            f"calibration step {attempt.step_key!r} has the wrong operation output"
        )
    return output


def _require_exact_analysis(
    published: PublishedAnalysis,
    ref: AnalysisPublicationOutputRef,
) -> None:
    if (
        published.id != ref.analysis_record_id
        or published.view.analysis.subject != ref.subject
    ):
        raise ValueError("analysis publication does not match its exact step output")


def _parameter_proposal(
    published: PublishedAnalysis,
    proposal_id: str,
) -> ParameterChangeProposal:
    try:
        return next(
            proposal
            for proposal in published.parameter_proposals
            if proposal.id == proposal_id
        )
    except StopIteration:
        raise ValueError(
            f"calibration fit does not own proposal {proposal_id!r}"
        ) from None


def _matches_cohort_base(
    run: RunHandle,
    base: CalibrationConfigSourceRef,
) -> bool:
    snapshot = run.snapshot
    source = snapshot.config_source
    return (
        isinstance(source, ContextRunConfigSource)
        and source.context == base.context_ref
        and source.content_hash == base.content_hash
        and isinstance(base.scope, WorkingPointCalibrationScope)
        and source.sample == base.scope.sample
        and not source.overrides
        and snapshot.samples == (base.scope.sample,)
    )


def _reconcile_unknown_publication(
    config: _CalibrationPublicationOperations,
    plan: CalibrationCohortPublicationPlan,
    *,
    cause: BaseException,
) -> CalibrationPublicationReceipt:
    try:
        return _reopen_calibration_cohort_publication(config, plan)
    except (
        DaemonNotFoundError,
        DaemonUnavailableError,
        httpx2.TransportError,
    ) as lookup_error:
        raise CalibrationPublicationOutcomeUnknown(plan, cause=cause) from lookup_error
    except httpx2.HTTPStatusError as lookup_error:
        if lookup_error.response.status_code < 500:
            raise
        raise CalibrationPublicationOutcomeUnknown(
            plan,
            cause=cause,
        ) from lookup_error


def _validate_publication_receipt(
    receipt: CalibrationPublicationReceipt,
    plan: CalibrationCohortPublicationPlan,
) -> None:
    command = plan.command
    operation = receipt.operation
    expected_operation = (
        command.operation_id,
        command.intent_hash,
        command.source_intent_hash,
        command.entry_id,
        command.source.base,
        command.actor,
        command.note,
    )
    actual_operation = (
        operation.operation_id,
        operation.intent_hash,
        operation.source_intent_hash,
        operation.entry_id,
        operation.base,
        operation.actor,
        operation.note,
    )
    source = plan.source
    entry_source = receipt.entry.source
    resolved_source = (
        entry_source.publication
        if isinstance(entry_source, ContextConfigRegistrySource)
        else None
    )
    contributions = {
        contribution.member_id: contribution for contribution in source.contributions
    }
    successes = {
        success.attempt.member_id: success for success in receipt.calibration_successes
    }
    if (
        actual_operation != expected_operation
        or receipt.entry.id != plan.entry_id
        or receipt.entry.content_hash != source.expected_result_content_hash
        or not isinstance(resolved_source, CalibrationCohortMergeRegistrySource)
        or not _resolved_source_matches(resolved_source, source)
        or successes.keys() != contributions.keys()
    ):
        raise CalibrationPublicationDriftError(
            "config publish receipt does not match its exact calibration plan"
        )
    for member_id, contribution in contributions.items():
        success = successes[member_id]
        publication = success.publication
        result_source = (
            None if publication is None else publication.result_config_source
        )
        if (
            success.attempt.cohort_id != source.cohort_id
            or success.attempt.procedure_run_id
            != contribution.proof.evidence_step.procedure_run_id
            or success.base_config_source != source.base
            or publication is None
            or publication.operation_id != plan.operation_id
            or publication.source_intent_hash != command.source_intent_hash
            or publication.result_input_fingerprint
            != contribution.result_input_fingerprint
            or result_source is None
            or result_source.entry_id != receipt.entry.id
            or result_source.config_ref != receipt.entry.config_ref
            or result_source.content_hash != receipt.entry.content_hash
            or result_source.scope != source.base.scope
            or publication.published_at != operation.recorded_at
        ):
            raise CalibrationPublicationDriftError(
                "calibration publication successes do not match its contributions"
            )


def _resolved_source_matches(
    actual: CalibrationCohortMergeRegistrySource,
    expected: CalibrationCohortMergeRevisionSource,
) -> bool:
    projected_contributions = tuple(
        _project_resolved_contribution(contribution)
        for contribution in actual.contributions
    )
    return (
        actual.cohort_id == expected.cohort_id
        and actual.spec_hash == expected.spec_hash
        and actual.automatic_publication_policy_id
        == (
            None
            if expected.automatic_publication is None
            else expected.automatic_publication.id
        )
        and actual.automatic_publication_policy_version
        == (
            None
            if expected.automatic_publication is None
            else expected.automatic_publication.version
        )
        and actual.automatic_publication_policy_fingerprint
        == (
            None
            if expected.automatic_publication is None
            else expected.automatic_publication.fingerprint
        )
        and actual.composition_policy_ref == expected.composition_policy_ref
        and actual.merge_policy == expected.merge_policy
        and actual.base == expected.base
        and actual.candidate_id == expected.candidate_id
        and projected_contributions == expected.contributions
    )


def _project_resolved_contribution(
    contribution: ResolvedCalibrationCohortMergeContribution,
) -> CalibrationCohortMergeContribution:
    return CalibrationCohortMergeContribution(
        member_id=contribution.member_id,
        proof=VerifiedParameterProposalProofV1(
            evidence_step=contribution.proof.evidence_step,
            decision=contribution.proof.decision,
        ),
        result_input_fingerprint=contribution.result_input_fingerprint,
    )


__all__ = [
    "CalibrationCohortPublicationPlan",
    "CalibrationPublicationDriftError",
    "CalibrationPublicationOutcomeUnknown",
    "CalibrationPublicationReadSession",
    "build_calibration_cohort_merge_contribution",
    "calibration_cohort_merge_revision_source",
    "calibration_cohort_publication_entry_id",
    "calibration_cohort_publication_operation_id",
    "publish_calibration_cohort",
]
