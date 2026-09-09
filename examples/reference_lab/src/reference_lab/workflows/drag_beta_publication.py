"""Exact composition and publication of one verified q0/q1 DRAG cohort."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scopecat.api.calibration_finalizer import (
    CalibrationPublicationPlanningContext,
    CalibrationPublicationProcedureView,
)
from scopecat.api.calibration_publication import CalibrationCohortPublicationPlan
from scopecat.api.lab import LabClient
from scopecat.automation.calibration_wire import CalibrationCohortMemberPage
from scopecat.automation.calibrations import (
    CalibrationCohort,
    CalibrationCohortMember,
)
from scopecat.automation.models import (
    AnalysisPublicationOutputRef,
    RunOutputRef,
)
from scopecat.config.candidate_merges import (
    CommonBaseCandidateMergeResult,
    merge_common_base_parameter_proposals,
)
from scopecat.config.registry.records import (
    CalibrationCohortMergeContribution,
    ConfigCompositionPolicyRef,
)
from scopecat.daemon.wire import CalibrationPublicationReceipt
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.python_source import python_source_identity
from scopecat.kernel.value_identity import scalar_values_equal
from scopecat.records.analysis import MeasurementAnalysisRecordInput
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import (
    ParameterAtomValue,
    TableParameterValue,
)
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.run import AnalysisCandidateRunConfigSource

from reference_lab.parameters import DRAG_BETA, QUBIT, QUBITS
from reference_lab.workflows.drag_beta_experiment import DragBetaQubit
from reference_lab.workflows.drag_beta_freshness import (
    DRAG_BETA_CALIBRATION_FANOUT_SCOPE,
    DRAG_BETA_CALIBRATION_TARGETS,
    DRAG_BETA_CALIBRATION_VERSION,
    drag_beta_freshness_calibration,
    drag_beta_semantic_freshness_inputs,
)
from reference_lab.workflows.drag_beta_procedure import (
    DragBetaVerificationIntent,
    drag_beta_verification_procedure,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_MINIMUM_IMPROVEMENT,
    DRAG_BETA_VERIFICATION_SCHEMA,
)

DRAG_BETA_COMPOSITION_POLICY_ID = "reference-lab.drag-beta-cohort-composition"
DRAG_BETA_COMPOSITION_POLICY_VERSION = "7"
DRAG_BETA_VERIFICATION_EVIDENCE_STEP = "verification"
DRAG_BETA_PUBLICATION_ACTOR = "reference-lab-drag-beta-finalizer"
DRAG_BETA_PUBLICATION_NOTE = "publish verified q0/q1 DRAG calibration cohort"

_DRAG_BETA_COMPOSITION_POLICY_CODEC = "reference_lab.drag-beta-composition-policy.v1"
_DRAG_BETA_MERGED_CANDIDATE_CODEC = "reference_lab.drag-beta-merged-candidate.v1"
_DRAG_BETA_TARGET_IDS = ("q0", "q1")
_DRAG_BETA_DECISION_OUTPUT_ID = "decision"


def _drag_beta_composition_policy_fingerprint() -> str:
    return "sha256:" + stable_content_hash(
        {
            "codec": _DRAG_BETA_COMPOSITION_POLICY_CODEC,
            "calibration": drag_beta_freshness_calibration.ref.model_dump(mode="json"),
            "calibration_semantic_inputs_version": DRAG_BETA_CALIBRATION_VERSION,
            "procedure": drag_beta_verification_procedure.ref.model_dump(mode="json"),
            "merge_policy": "common_base_cells_v1",
            "proof": {
                "kind": "verified_parameter_proposal_v1",
                "evidence_step": DRAG_BETA_VERIFICATION_EVIDENCE_STEP,
            },
            "targets": list(_DRAG_BETA_TARGET_IDS),
            "owned_path": {
                "parameter_id": QUBITS.id,
                "primary_key": QUBIT.id,
                "column_id": DRAG_BETA.id,
            },
            "proposal_id": "{qubit}-drag-beta",
            "decision": {
                "output_id": _DRAG_BETA_DECISION_OUTPUT_ID,
                "schema_id": DRAG_BETA_VERIFICATION_SCHEMA.id,
                "schema_codec": DRAG_BETA_VERIFICATION_SCHEMA.schema_codec,
                "schema_hash": DRAG_BETA_VERIFICATION_SCHEMA.schema_hash,
                "accepted": True,
            },
            "composition_claim": (
                "each member candidate and the merged result have equal semantic "
                "freshness inputs"
            ),
            "implementation": {
                "preview": python_source_identity(
                    prepare_drag_beta_cohort_publication,
                    label="DRAG composition preview",
                ),
                "prepare_core": python_source_identity(
                    prepare_drag_beta_cohort_publication_from_context,
                    label="DRAG composition preparation core",
                ),
                "proposal_validator": python_source_identity(
                    validate_drag_beta_target_owned_proposal,
                    label="DRAG target-owned proposal validator",
                ),
                "result_input": python_source_identity(
                    drag_beta_merged_result_input_fingerprint,
                    label="DRAG merged result input projector",
                ),
                "cohort_base": python_source_identity(
                    _drag_beta_cohort_base,
                    label="DRAG cohort base resolver",
                ),
                "member_material": python_source_identity(
                    _drag_beta_member_material,
                    label="DRAG member proof resolver",
                ),
                "qubit_rows": python_source_identity(
                    _qubit_rows,
                    label="DRAG owned-row projector",
                ),
                "candidate_id": python_source_identity(
                    _drag_beta_merged_candidate_id,
                    label="DRAG merged candidate identity",
                ),
            },
        }
    )


def drag_beta_composition_policy_ref() -> ConfigCompositionPolicyRef:
    """Fingerprint the declarative contract and every project policy callback."""

    return ConfigCompositionPolicyRef(
        id=DRAG_BETA_COMPOSITION_POLICY_ID,
        version=DRAG_BETA_COMPOSITION_POLICY_VERSION,
        fingerprint=_drag_beta_composition_policy_fingerprint(),
    )


@dataclass(frozen=True, slots=True)
class DragBetaCohortPublication:
    """Read-only exact preview plus the deterministic mutation plan."""

    cohort: CalibrationCohort
    member_page: CalibrationCohortMemberPage
    base_config: ConfigProfileSnapshot
    proposals: tuple[ParameterChangeProposal, ...]
    merge: CommonBaseCandidateMergeResult
    contributions: tuple[CalibrationCohortMergeContribution, ...]
    plan: CalibrationCohortPublicationPlan


@dataclass(frozen=True, slots=True)
class _DragBetaMemberMaterial:
    member: CalibrationCohortMember
    procedure: CalibrationPublicationProcedureView
    qubit: DragBetaQubit
    intent: DragBetaVerificationIntent
    proposal: ParameterChangeProposal
    candidate_config: ConfigProfileSnapshot


def prepare_drag_beta_cohort_publication(
    lab: LabClient,
    cohort_id: str,
    *,
    actor: str = DRAG_BETA_PUBLICATION_ACTOR,
    note: str = DRAG_BETA_PUBLICATION_NOTE,
) -> DragBetaCohortPublication:
    """Resolve, validate, and preview one complete verified DRAG cohort.

    This function is read-only. It resolves one exact verification checkpoint
    for every member, checks the project-owned patch boundary, composes their
    common-base proposals, and proves that each member's verified candidate has
    the same semantic freshness inputs as its projection from the merged result.
    """

    context = lab.calibrations.publication_planning_context()
    return prepare_drag_beta_cohort_publication_from_context(
        context,
        cohort=context.cohort(cohort_id),
        member_page=context.cohort_members(cohort_id),
        actor=actor,
        note=note,
    )


def prepare_drag_beta_cohort_publication_from_context(
    context: CalibrationPublicationPlanningContext,
    *,
    cohort: CalibrationCohort,
    member_page: CalibrationCohortMemberPage,
    actor: str,
    note: str,
    expected_finalization_revision: int | None = None,
) -> DragBetaCohortPublication:
    """Prepare one exact plan from the shared read-only planning surface."""

    base_config = _drag_beta_cohort_base(context, cohort, member_page)
    materials = tuple(
        _drag_beta_member_material(context, cohort, member, base_config=base_config)
        for member in member_page.items
    )
    proposals = tuple(material.proposal for material in materials)
    merge = merge_common_base_parameter_proposals(
        proposals,
        base_config=base_config,
        candidate_id=_drag_beta_merged_candidate_id(cohort),
    )

    contributions: list[CalibrationCohortMergeContribution] = []
    for material in materials:
        result_input_fingerprint = drag_beta_merged_result_input_fingerprint(
            candidate_config=material.candidate_config,
            merged_config=merge.config,
            qubit=material.qubit,
            minimum_improvement=material.intent.minimum_improvement,
        )
        contributions.append(
            context.build_merge_contribution(
                cohort=cohort,
                member=material.member,
                procedure=material.procedure,
                evidence_step_key=DRAG_BETA_VERIFICATION_EVIDENCE_STEP,
                decision_output_id=_DRAG_BETA_DECISION_OUTPUT_ID,
                result_input_fingerprint=result_input_fingerprint,
            )
        )

    frozen_contributions = tuple(contributions)
    source = context.merge_source(
        cohort=cohort,
        member_page=member_page,
        composition_policy_ref=DRAG_BETA_COMPOSITION_POLICY_REF,
        candidate_id=merge.config.id,
        contributions=frozen_contributions,
        expected_result_content_hash=merge.content_hash,
    )
    plan = context.publication_plan(
        source,
        actor=actor,
        note=note,
        expected_finalization_revision=expected_finalization_revision,
    )
    return DragBetaCohortPublication(
        cohort=cohort,
        member_page=member_page,
        base_config=base_config,
        proposals=proposals,
        merge=merge,
        contributions=frozen_contributions,
        plan=plan,
    )


def publish_verified_drag_beta_cohort(
    lab: LabClient,
    cohort_id: str,
    *,
    actor: str = DRAG_BETA_PUBLICATION_ACTOR,
    note: str = DRAG_BETA_PUBLICATION_NOTE,
) -> CalibrationPublicationReceipt:
    """Publish one exact verified cohort, or replay its deterministic receipt."""

    prepared = prepare_drag_beta_cohort_publication(
        lab,
        cohort_id,
        actor=actor,
        note=note,
    )
    return lab.calibrations.publish(prepared.plan)


def validate_drag_beta_target_owned_proposal(
    proposal: ParameterChangeProposal,
    qubit: DragBetaQubit,
) -> None:
    """Require exactly one effective edit to the selected target's DRAG cell."""

    if proposal.id != f"{qubit}-drag-beta":
        raise ValueError("DRAG proposal id does not match its calibration target")
    if len(proposal.deltas) != 1:
        raise ValueError("DRAG proposal must contain exactly one parameter delta")
    [delta] = proposal.deltas
    if (
        delta.parameter_id != QUBITS.id
        or not isinstance(delta.before, TableParameterValue)
        or not isinstance(delta.after, TableParameterValue)
        or delta.before.id != QUBITS.id
        or delta.after.id != QUBITS.id
    ):
        raise ValueError("DRAG proposal must update the qubit parameter table")

    before_rows = _qubit_rows(delta.before)
    after_rows = _qubit_rows(delta.after)
    if before_rows.keys() != after_rows.keys():
        raise ValueError("DRAG proposal cannot add or remove qubit rows")
    if qubit not in before_rows or DRAG_BETA.id not in before_rows[qubit]:
        raise ValueError("DRAG proposal does not contain its owned beta cell")
    changed_cells: list[tuple[str, str]] = []
    for row_qubit, before in before_rows.items():
        after = after_rows[row_qubit]
        if before.keys() != after.keys():
            raise ValueError("DRAG proposal cannot change qubit table columns")
        for column_id, before_value in before.items():
            if not scalar_values_equal(before_value, after[column_id]):
                changed_cells.append((row_qubit, column_id))
    owned_cell = (qubit, DRAG_BETA.id)
    if any(cell != owned_cell for cell in changed_cells):
        raise ValueError("DRAG proposal changed a non-owned parameter cell")
    if owned_cell not in changed_cells:
        raise ValueError("DRAG proposal did not change its owned beta cell")


def drag_beta_merged_result_input_fingerprint(
    *,
    candidate_config: ConfigProfileSnapshot,
    merged_config: ConfigProfileSnapshot,
    qubit: DragBetaQubit,
    minimum_improvement: float = DRAG_BETA_MINIMUM_IMPROVEMENT,
) -> Sha256ContentHash:
    """Prove composition preserves one verified candidate's semantic inputs."""

    candidate_inputs = drag_beta_semantic_freshness_inputs(
        candidate_config,
        qubit,
        minimum_improvement=minimum_improvement,
    )
    result_inputs = drag_beta_semantic_freshness_inputs(
        merged_config,
        qubit,
        minimum_improvement=minimum_improvement,
    )
    if candidate_inputs != result_inputs:
        raise ValueError(
            "merged DRAG result changes a member's verified semantic inputs"
        )
    return drag_beta_freshness_calibration.input_fingerprint(result_inputs)


def _drag_beta_cohort_base(
    context: CalibrationPublicationPlanningContext,
    cohort: CalibrationCohort,
    member_page: CalibrationCohortMemberPage,
) -> ConfigProfileSnapshot:
    expected_ref = drag_beta_freshness_calibration.ref
    supported_targets = {
        (target.kind, target.id) for target in DRAG_BETA_CALIBRATION_TARGETS
    }
    actual_targets = tuple(
        sorted(
            (member.spec.target.kind, member.spec.target.id)
            for member in member_page.items
        )
    )
    if (
        cohort.spec.definition != expected_ref
        or cohort.spec.fanout_scope != DRAG_BETA_CALIBRATION_FANOUT_SCOPE
        or member_page.cohort_id != cohort.cohort_id
        or member_page.next_cursor is not None
        or len(member_page.items) != len(cohort.spec.members)
        or not 1 <= len(member_page.items) <= len(_DRAG_BETA_TARGET_IDS)
        or len(actual_targets) != len(set(actual_targets))
        or not set(actual_targets).issubset(supported_targets)
    ):
        raise ValueError(
            "DRAG publication requires one complete non-empty q0/q1 cohort subset"
        )
    if any(
        member.spec.definition != expected_ref
        or member.spec.procedure != drag_beta_verification_procedure.ref
        or member.spec.dependencies
        for member in member_page.items
    ):
        raise ValueError("DRAG cohort member does not match its composition policy")

    base = cohort.spec.config_source
    base_view = context.config_entry(base.entry_id)
    if (
        base_view.entry.id != base.entry_id
        or base_view.entry.config_ref != base.config_ref
        or base_view.entry.content_hash != base.content_hash
        or config_content_hash(base_view.config) != base.content_hash
    ):
        raise ValueError("DRAG cohort base entry does not match its immutable config")
    return base_view.config


def _drag_beta_member_material(
    context: CalibrationPublicationPlanningContext,
    cohort: CalibrationCohort,
    member: CalibrationCohortMember,
    *,
    base_config: ConfigProfileSnapshot,
) -> _DragBetaMemberMaterial:
    target_id = member.spec.target.id
    if target_id not in _DRAG_BETA_TARGET_IDS:
        raise ValueError("DRAG cohort member has an unsupported target")
    qubit = target_id
    intent = DragBetaVerificationIntent.model_validate(member.spec.intent)
    base = cohort.spec.config_source
    source = intent.initial_config_source
    if (
        intent.qubit != qubit
        or intent.initial_config != base_config
        or source.selector != "active"
        or source.entry_id != base.entry_id
        or source.config_ref != base.config_ref
        or source.content_hash != base.content_hash
        or source.registry_generation != base.registry_generation
    ):
        raise ValueError("DRAG member intent does not match its exact cohort base")
    observed_inputs = drag_beta_semantic_freshness_inputs(
        base_config,
        qubit,
        minimum_improvement=intent.minimum_improvement,
    )
    if (
        drag_beta_freshness_calibration.input_fingerprint(observed_inputs)
        != member.spec.input_fingerprint
    ):
        raise ValueError("DRAG member input fingerprint does not match its base")

    procedure = context.procedure(member.procedure_run_id)
    snapshot = procedure.snapshot
    if (
        snapshot.state != "closed"
        or snapshot.closure is None
        or snapshot.closure.status != "succeeded"
    ):
        raise ValueError(
            "DRAG publication requires every member procedure to be closed succeeded"
        )
    evidence_attempt = procedure.step(DRAG_BETA_VERIFICATION_EVIDENCE_STEP)
    evidence = evidence_attempt.output
    if (
        evidence_attempt.state != "succeeded"
        or not isinstance(evidence, AnalysisPublicationOutputRef)
        or len(evidence_attempt.inputs) != 2
        or not all(isinstance(item, RunOutputRef) for item in evidence_attempt.inputs)
    ):
        raise TypeError("DRAG member has the wrong verification evidence shape")
    evidence_run_ids = tuple(
        item.run_id
        for item in evidence_attempt.inputs
        if isinstance(item, RunOutputRef)
    )
    run_handles = tuple(context.run(run_id) for run_id in evidence_run_ids)
    baseline_runs = tuple(
        run
        for run in run_handles
        if run.snapshot.config_source == intent.initial_config_source
    )
    candidate_runs = tuple(
        run
        for run in run_handles
        if isinstance(run.snapshot.config_source, AnalysisCandidateRunConfigSource)
    )
    if len(baseline_runs) != 1 or len(candidate_runs) != 1:
        raise ValueError(
            "DRAG verification evidence does not identify one baseline and candidate"
        )
    baseline_run = baseline_runs[0]
    candidate_run = candidate_runs[0]
    candidate_source = candidate_run.snapshot.config_source
    assert isinstance(candidate_source, AnalysisCandidateRunConfigSource)
    if candidate_source.source_run_id != baseline_run.id:
        raise ValueError("DRAG candidate does not derive from its evidence baseline")
    fit_analysis = baseline_run.published_analysis(candidate_source.analysis_record_id)
    proposal = fit_analysis.proposal(candidate_source.proposal_id)
    validate_drag_beta_target_owned_proposal(proposal, qubit)

    candidate_config = candidate_run.config
    if candidate_run.snapshot.config_content_hash != config_content_hash(
        candidate_config
    ):
        raise ValueError("DRAG candidate run config does not match its snapshot")

    evidence_analysis = context.published_analysis(evidence.analysis_record_id)
    evidence_analysis_run_ids = tuple(
        input_ref.run_id
        for input_ref in evidence_analysis.inputs
        if isinstance(input_ref, MeasurementAnalysisRecordInput)
    )
    if len(evidence_analysis.inputs) != 2 or set(evidence_analysis_run_ids) != set(
        evidence_run_ids
    ):
        raise ValueError("DRAG evidence step and project analysis inputs differ")
    decision = evidence_analysis.fact_as(
        _DRAG_BETA_DECISION_OUTPUT_ID,
        DRAG_BETA_VERIFICATION_SCHEMA,
    )
    if (
        not decision.accepted
        or decision.minimum_improvement != intent.minimum_improvement
    ):
        raise ValueError("DRAG verification decision does not satisfy its intent")

    return _DragBetaMemberMaterial(
        member=member,
        procedure=procedure,
        qubit=qubit,
        intent=intent,
        proposal=proposal,
        candidate_config=candidate_config,
    )


def _qubit_rows(
    table: TableParameterValue,
) -> dict[str, Mapping[str, ParameterAtomValue]]:
    rows: dict[str, Mapping[str, ParameterAtomValue]] = {}
    for row in table.rows:
        entity = row.get(QUBIT.id)
        if not isinstance(entity, EntityRef) or entity.kind != "logical_qubit":
            raise ValueError("DRAG proposal has an invalid qubit primary key")
        if entity.id in rows:
            raise ValueError("DRAG proposal has duplicate qubit rows")
        rows[entity.id] = row
    return rows


def _drag_beta_merged_candidate_id(cohort: CalibrationCohort) -> str:
    digest = stable_content_hash(
        {
            "codec": _DRAG_BETA_MERGED_CANDIDATE_CODEC,
            "cohort_id": cohort.cohort_id,
            "spec_hash": cohort.spec_hash,
            "composition_policy": DRAG_BETA_COMPOSITION_POLICY_REF.model_dump(
                mode="json"
            ),
        }
    )
    return f"drag-beta-cohort-{digest}"


DRAG_BETA_COMPOSITION_POLICY_REF = drag_beta_composition_policy_ref()
DRAG_BETA_COMPOSITION_POLICY_FINGERPRINT = DRAG_BETA_COMPOSITION_POLICY_REF.fingerprint


__all__ = [
    "DRAG_BETA_COMPOSITION_POLICY_FINGERPRINT",
    "DRAG_BETA_COMPOSITION_POLICY_ID",
    "DRAG_BETA_COMPOSITION_POLICY_REF",
    "DRAG_BETA_COMPOSITION_POLICY_VERSION",
    "DRAG_BETA_PUBLICATION_ACTOR",
    "DRAG_BETA_PUBLICATION_NOTE",
    "DRAG_BETA_VERIFICATION_EVIDENCE_STEP",
    "DragBetaCohortPublication",
    "drag_beta_composition_policy_ref",
    "drag_beta_merged_result_input_fingerprint",
    "prepare_drag_beta_cohort_publication",
    "prepare_drag_beta_cohort_publication_from_context",
    "publish_verified_drag_beta_cohort",
    "validate_drag_beta_target_owned_proposal",
]
