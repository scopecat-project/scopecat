"""Candidate configuration resolution from one parameter proposal."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scopecat.config.changes import is_safe_parameter_change_id
from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.config.parameter_updates import apply_parameter_change_deltas
from scopecat.kernel.errors import CheckFailed, Conflict
from scopecat.kernel.ids import artifact_slug
from scopecat.kernel.problems import (
    Problem,
    ProblemLocation,
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.project_state import ProjectStateServices
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.parameter_change import ParameterChangeProposal

type CandidateSelection = str | None


@dataclass(frozen=True)
class CandidateConfig:
    parameter_proposal: ParameterChangeProposal

    @property
    def source_run_id(self) -> str:
        return self.parameter_proposal.source_run_id

    @property
    def base_config_content_hash(self) -> str:
        return self.parameter_proposal.base_config_content_hash

    @property
    def proposal_id(self) -> str:
        return self.parameter_proposal.id

    @property
    def analysis_record_id(self) -> str:
        return self.parameter_proposal.analysis_record_id


def candidate_config_from_proposals(
    proposals: Sequence[ParameterChangeProposal],
    *,
    selection: CandidateSelection = None,
) -> CandidateConfig:
    """Select one durable proposal exposed by an analysis publication."""

    if not proposals:
        raise CheckFailed(
            [
                _candidate_problem(
                    "candidate_config_no_parameter_proposals",
                    "candidate config requires at least one parameter proposal",
                    phase=ProblemPhase.ANALYSIS,
                    location=model_location("parameter_proposals"),
                )
            ]
        )
    if selection is None:
        if len(proposals) == 1:
            return CandidateConfig(parameter_proposal=proposals[0])
        raise CheckFailed(
            [
                _candidate_problem(
                    "candidate_config_selection_required",
                    "candidate config selection is required when analysis has "
                    "multiple parameter proposals",
                    phase=ProblemPhase.ANALYSIS,
                    location=model_location("selection"),
                )
            ]
        )
    proposal_id = artifact_slug(selection, fallback="analysis")
    try:
        selected = next(
            proposal for proposal in proposals if proposal.id == proposal_id
        )
    except StopIteration:
        raise CheckFailed(
            [
                _candidate_problem(
                    "candidate_config_selection_not_found",
                    f"candidate config selection was not found: {proposal_id}",
                    phase=ProblemPhase.ANALYSIS,
                    location=model_location("selection"),
                )
            ]
        ) from None
    return CandidateConfig(parameter_proposal=selected)


def resolve_candidate_config_snapshot(
    candidate: CandidateConfig,
    *,
    services: ProjectStateServices,
) -> ConfigProfileSnapshot:
    """Resolve a candidate into a config snapshot without writing run state."""

    _validate_candidate(candidate)
    source_config = services.runs.read_config_profile_snapshot(candidate.source_run_id)
    return resolve_candidate_config_from_snapshot(
        candidate,
        source_config=source_config,
    )


def resolve_candidate_config_from_snapshot(
    candidate: CandidateConfig,
    *,
    source_config: ConfigProfileSnapshot,
) -> ConfigProfileSnapshot:
    """Resolve a candidate against its known source snapshot."""

    _validate_candidate(candidate)
    return _build_candidate_config_snapshot(
        config=source_config,
        candidate=candidate,
        candidate_id=_candidate_config_id(candidate),
    )


def _build_candidate_config_snapshot(
    *,
    config: ConfigProfileSnapshot,
    candidate: CandidateConfig,
    candidate_id: str,
) -> ConfigProfileSnapshot:
    actual_hash = config_content_hash(config)
    if actual_hash != candidate.base_config_content_hash:
        raise Conflict(
            [
                _candidate_problem(
                    "parameter_change_proposal_base_mismatch",
                    "parameter change proposal was derived from a different "
                    "source config snapshot",
                    location=model_location("candidate_config", "parameter_proposal"),
                    details={
                        "expected_content_hash": candidate.base_config_content_hash,
                        "actual_content_hash": actual_hash,
                    },
                )
            ]
        )
    proposal = candidate.parameter_proposal
    if proposal.base_config_id != config.id:
        raise Conflict(
            [
                _candidate_problem(
                    "parameter_change_proposal_base_id_mismatch",
                    "parameter change proposal base config id is stale",
                    location=model_location("candidate_config", "parameter_proposal"),
                    details={
                        "expected_config_id": config.id,
                        "proposal_config_id": proposal.base_config_id,
                    },
                )
            ]
        )
    try:
        parameter_snapshot = apply_parameter_change_deltas(
            base=config.parameter_snapshot,
            deltas=proposal.deltas,
            candidate_id=f"{candidate_id}.parameters",
        )
    except ValueError as error:
        raise CheckFailed(
            [
                _candidate_problem(
                    "parameter_change_proposal_invalid",
                    f"parameter change proposal cannot be applied: {error}",
                    location=model_location("candidate_config", "parameter_proposal"),
                )
            ]
        ) from error
    problems = validate_parameter_snapshot(
        config.parameter_catalog,
        parameter_snapshot,
        allow_missing=True,
    )
    if problems:
        raise CheckFailed(problems)
    return config.model_copy(
        update={
            "id": candidate_id,
            "parameter_snapshot": parameter_snapshot,
        },
        deep=True,
    )


def _validate_candidate(candidate: CandidateConfig) -> None:
    proposal = candidate.parameter_proposal
    if not is_safe_parameter_change_id(proposal.id):
        raise CheckFailed(
            [
                _candidate_problem(
                    "parameter_change_invalid_id",
                    "parameter change proposal id is not safe for record paths",
                    location=model_location("parameter_change_proposal", "id"),
                    details={"proposal_id": proposal.id},
                )
            ]
        )


def _candidate_config_id(candidate: CandidateConfig) -> str:
    return f"candidate-{artifact_slug(candidate.parameter_proposal.id)}"


def _candidate_problem(
    code: str,
    message: str,
    *,
    location: ProblemLocation,
    phase: ProblemPhase = ProblemPhase.CONFIGURATION,
    details: dict[str, object] | None = None,
) -> Problem:
    return problem(
        code,
        message,
        phase=phase,
        location=location,
        details=details,
    )
