"""Authoritative resolution shared by candidate launches and scientific contexts."""

from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_snapshot,
)
from scopecat.config.changes import (
    load_parameter_change_proposal,
    parameter_change_proposal_record_ref,
)
from scopecat.kernel.errors import ProblemFailure
from scopecat.project_state import ProjectStateServices
from scopecat.records.analysis import (
    AnalysisParameterProposalRecordOutput,
    AnalysisRecord,
)
from scopecat.records.candidate_input import AnalysisCandidateRunConfigSource
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.runs.refs import record_content_ref

from scopecat_server.errors import BackendConflict


def resolve_candidate_input(
    source: AnalysisCandidateRunConfigSource, services: ProjectStateServices
) -> ConfigProfileSnapshot:
    try:
        proposal = load_parameter_change_proposal(
            run_id=source.source_run_id,
            selector=source.proposal_id,
            services=services,
        )
        if (
            proposal.id != source.proposal_id
            or proposal.analysis_record_id != source.analysis_record_id
            or proposal.base_config_content_hash != source.base_config_content_hash
        ):
            raise BackendConflict(
                "analysis candidate source does not match its durable proposal"
            )
        analysis = services.runs.read_model(
            source.source_run_id,
            record_content_ref(
                record_id=source.analysis_record_id,
                kind="analysis",
            ),
            AnalysisRecord,
        )
        if (
            analysis.subject.kind != "run"
            or analysis.subject.run_id != source.source_run_id
            or not _analysis_references_proposal(
                analysis,
                proposal_id=proposal.id,
            )
        ):
            raise BackendConflict(
                "analysis candidate proposal does not belong to its analysis"
            )
        resolved = resolve_candidate_config_snapshot(
            CandidateConfig(parameter_proposal=proposal),
            services=services,
        )
        if config_content_hash(resolved) != source.content_hash:
            raise BackendConflict(
                "analysis candidate source does not match its resolved configuration"
            )
        return resolved
    except BackendConflict:
        raise
    except ProblemFailure as error:
        raise BackendConflict(
            "analysis candidate config source cannot be resolved"
        ) from error


def _analysis_references_proposal(
    analysis: AnalysisRecord,
    *,
    proposal_id: str,
) -> bool:
    expected_ref = parameter_change_proposal_record_ref(proposal_id)
    for output in analysis.outputs:
        if not isinstance(output, AnalysisParameterProposalRecordOutput):
            continue
        if (
            output.content.proposal_id == proposal_id
            and output.content.record_ref == expected_ref
        ):
            return True
    return False
