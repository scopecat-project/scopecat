"""Resolve exact retained sources before composing independent parameters."""

from collections.abc import Sequence
from dataclasses import dataclass

from scopecat.config.candidate_merges import (
    ParameterMergeResult,
    merge_parameter_deltas,
)
from scopecat.config.changes import load_parameter_change_proposal
from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import ProblemPhase, model_location, problem
from scopecat.project_state import ProjectStateServices
from scopecat.records.parameter_change import (
    ParameterProposalComposition,
    ParameterProposalRef,
    ParameterValueDelta,
)
from scopecat.records.parameter_content import ParameterContent
from scopecat.records.run import ParameterRunConfigSource


@dataclass(frozen=True, slots=True)
class ResolvedParameterComposition:
    provenance: ParameterProposalComposition
    merged: ParameterMergeResult


def resolve_parameter_composition(
    sources: Sequence[ParameterProposalRef],
    *,
    anchor_run_id: str,
    services: ProjectStateServices,
) -> ResolvedParameterComposition:
    """Only a shared saved base and scientific scope permit value composition.

    This resolves stored proposals, never caller-supplied deltas or acceptance.
    It neither verifies the joint candidate nor changes any branch.
    """
    canonical = tuple(sorted(sources, key=lambda item: (item.run_id, item.proposal_id)))
    identities = {(item.run_id, item.proposal_id) for item in canonical}
    if len(identities) != len(canonical):
        raise _composition_error("composition sources must be unique")
    if anchor_run_id not in {item.run_id for item in canonical}:
        raise _composition_error("composition anchor must be a contributing baseline")
    anchor = services.runs.read_snapshot(anchor_run_id)
    source = anchor.config_source
    if not isinstance(source, ParameterRunConfigSource) or source.overrides:
        raise _composition_error(
            "composition requires saved independent parameters without overrides"
        )
    config = services.runs.read_config_profile_snapshot(anchor_run_id)
    changes: list[tuple[ParameterValueDelta, ...]] = []
    for ref in canonical:
        snapshot = services.runs.read_snapshot(ref.run_id)
        current = snapshot.config_source
        if (
            snapshot.outcome is None
            or snapshot.outcome.result != "succeeded"
            or not isinstance(current, ParameterRunConfigSource)
            or current.parameters != source.parameters
            or current.setup != source.setup
            or current.overrides
            or snapshot.scientific_binding.subject != anchor.scientific_binding.subject
            or snapshot.scientific_binding.scenario
            != anchor.scientific_binding.scenario
        ):
            raise _composition_error(
                "composition sources require the same exact parameters, "
                "setup and scientific scope"
            )
        proposal = load_parameter_change_proposal(
            run_id=ref.run_id,
            selector=ref.proposal_id,
            services=services,
        )
        if (
            proposal.id != ref.proposal_id
            or proposal.source_run_id != ref.run_id
            or proposal.analysis_record_id != ref.analysis_record_id
            or f"sha256:{model_wire_content_hash(proposal)}" != ref.content_hash
            or proposal.base_config_content_hash != snapshot.config_content_hash
        ):
            raise _composition_error(
                "composition source does not match its retained proposal"
            )
        if proposal.composition is not None:
            raise _composition_error(
                "combine original candidates in one call, not nested compositions"
            )
        changes.append(proposal.deltas)
    provenance = ParameterProposalComposition(base=source.parameters, sources=canonical)
    return ResolvedParameterComposition(
        provenance=provenance,
        merged=merge_parameter_deltas(
            changes,
            base=ParameterContent(
                parameter_catalog=config.parameter_catalog,
                parameter_snapshot=config.parameter_snapshot,
            ),
            snapshot_id="composed-candidate",
        ),
    )


def _composition_error(message: str) -> CheckFailed:
    return CheckFailed(
        [
            problem(
                "parameter_composition.invalid_source",
                message,
                phase=ProblemPhase.ANALYSIS,
                location=model_location("composition", "sources"),
            )
        ]
    )
