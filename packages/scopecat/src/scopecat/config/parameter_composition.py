"""Resolve exact retained sources before composing independent parameters."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from scopecat.config.candidate_merges import (
    ParameterMergeResult,
    merge_parameter_deltas,
)
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.changes import load_parameter_change_proposal
from scopecat.config.parameter_updates import parameter_cell_edits
from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import ProblemPhase, model_location, problem
from scopecat.project_state import ProjectStateServices
from scopecat.records.config import config_content_hash
from scopecat.records.parameter_change import (
    ParameterProposalComposition,
    ParameterProposalRef,
    ParameterValueDelta,
)
from scopecat.records.parameter_content import ParameterContent
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ParameterRunConfigSource,
)


@dataclass(frozen=True, slots=True)
class ResolvedParameterComposition:
    provenance: ParameterProposalComposition
    merged: ParameterMergeResult


def resolve_parameter_composition(
    sources: Sequence[ParameterProposalRef],
    *,
    anchor_run_id: str,
    services: ProjectStateServices,
    mode: Literal["parallel", "sequential"] = "parallel",
) -> ResolvedParameterComposition:
    """Resolve sibling edits or an ordered chain back to one saved base.

    This resolves stored proposals, never caller-supplied deltas or acceptance.
    It neither verifies the joint candidate nor changes any branch.
    """
    canonical = (
        tuple(sources)
        if mode == "sequential"
        else tuple(sorted(sources, key=lambda item: (item.run_id, item.proposal_id)))
    )
    if len(canonical) < 2 or len(canonical) > 200:
        raise _composition_error("composition requires between 2 and 200 sources")
    if mode == "sequential" and canonical[0].run_id != anchor_run_id:
        raise _composition_error("sequential composition must start at its anchor")
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
    expected: AnalysisCandidateRunConfigSource | None = None
    final_config = config
    for ref in canonical:
        snapshot = services.runs.read_snapshot(ref.run_id)
        current = snapshot.config_source
        matches_input = (
            current == expected
            if expected is not None
            else (
                isinstance(current, ParameterRunConfigSource)
                and current.parameters == source.parameters
                and current.setup == source.setup
                and not current.overrides
            )
        )
        if (
            snapshot.outcome is None
            or snapshot.outcome.result != "succeeded"
            or not matches_input
            or snapshot.scientific_binding.setup_content_hash
            != anchor.scientific_binding.setup_content_hash
            or snapshot.scientific_binding.subject != anchor.scientific_binding.subject
            or snapshot.scientific_binding.target_binding
            != anchor.scientific_binding.target_binding
            or snapshot.scientific_binding.scenario
            != anchor.scientific_binding.scenario
        ):
            raise _composition_error(
                "composition sources require the declared parameter input, "
                "same setup and scientific scope"
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
        if mode == "sequential":
            final_config = resolve_candidate_config_from_snapshot(
                CandidateConfig(proposal),
                source_config=services.runs.read_config_profile_snapshot(ref.run_id),
            )
            expected = AnalysisCandidateRunConfigSource(
                source_run_id=ref.run_id,
                analysis_record_id=ref.analysis_record_id,
                proposal_id=ref.proposal_id,
                base_config_content_hash=proposal.base_config_content_hash,
                content_hash=config_content_hash(final_config),
            )
    provenance = ParameterProposalComposition(
        base=source.parameters, sources=canonical, mode=mode
    )
    if mode == "sequential":
        deltas: list[ParameterValueDelta] = []
        for before in config.parameter_snapshot.values:
            after = final_config.parameter_snapshot.get(before.id)
            definition = config.parameter_catalog.get(before.id)
            assert after is not None and definition is not None
            if before != after:
                deltas.append(
                    ParameterValueDelta(
                        parameter_id=before.id,
                        before=before,
                        after=after,
                        cells=parameter_cell_edits(definition, before, after),
                    )
                )
        if not deltas:
            raise _composition_error(
                "sequential composition has no net parameter changes"
            )
        return ResolvedParameterComposition(
            provenance=provenance,
            merged=ParameterMergeResult(
                parameters=final_config.parameter_snapshot, deltas=tuple(deltas)
            ),
        )
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
