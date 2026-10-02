"""Validate retained proposal provenance without granting activation authority."""

from collections.abc import Mapping

from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.registry.records import CandidateConfigRegistrySource
from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.errors import CheckFailed, Conflict
from scopecat.records.analysis import (
    AnalysisFactRecordOutput,
    AnalysisParameterProposalRecordOutput,
    AnalysisSubject,
    ProjectAnalysisDecisionReference,
    ProjectAnalysisOutputReference,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
)
from scopecat.records.candidate_input import AnalysisCandidateRunConfigSource
from scopecat.records.config import config_content_hash
from scopecat.records.parameter_branch import ParameterBranchPublication
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterProposalRef,
)
from scopecat.records.plan_ref import PlanAnalysisSource

from .models import AnalysisEvidence, ScientificEvidence
from .traversal import evidence_models


def validate_proposal_references(
    evidence: ScientificEvidence, proposals: tuple[ParameterChangeProposal, ...]
) -> None:
    runs = {run.snapshot.run_id: run for run in evidence.runs}
    analyses = {
        (item.record.subject, item.entry.id): item for item in evidence.analyses
    }
    indexed = {(item.source_run_id, item.id): item for item in proposals}
    if len(indexed) != len(proposals):
        raise ValueError("exchange contains duplicate proposal identities")
    for run in evidence.runs:
        source = run.snapshot.config_source
        if (
            isinstance(source, AnalysisCandidateRunConfigSource)
            and source.content_hash != run.snapshot.config_content_hash
        ):
            raise ValueError("run configuration differs from its candidate source")
    for proposal in proposals:
        run = runs.get(proposal.source_run_id)
        if run is None or (run.configuration.id, run.snapshot.config_content_hash) != (
            proposal.base_config_id,
            proposal.base_config_content_hash,
        ):
            raise ValueError("proposal baseline run is missing or differs")
        analysis = analyses.get(
            (
                RunAnalysisSubject(run_id=proposal.source_run_id),
                proposal.analysis_record_id,
            )
        )
        if analysis is None or not any(
            isinstance(output, AnalysisParameterProposalRecordOutput)
            and output.content.proposal_id == proposal.id
            for output in analysis.record.outputs
        ):
            raise ValueError("proposal publication is missing or differs")
        if not set(proposal.evidence_output_ids) <= {
            output.id for output in analysis.record.outputs
        }:
            raise ValueError("proposal evidence outputs are missing")
    for item in evidence_models((evidence, *proposals)):
        if isinstance(item, ProjectAnalysisOutputReference):
            _validate_project_decision(item, analyses)
        elif isinstance(item, PlanAnalysisSource):
            analysis = analyses.get(
                (RunAnalysisSubject(run_id=item.run_id), item.analysis_id)
            )
            if (
                analysis is None
                or analysis.record.publication_hash != item.publication_hash
            ):
                raise ValueError("plan analysis source is missing or differs")
        elif isinstance(
            item,
            AnalysisCandidateRunConfigSource
            | CandidateConfigRegistrySource
            | ParameterBranchPublication
            | ParameterProposalRef,
        ):
            run_id = (
                item.source_run_id
                if isinstance(item, AnalysisCandidateRunConfigSource)
                else item.run_id
            )
            proposal = indexed.get((run_id, item.proposal_id))
            if proposal is None:
                raise ValueError("candidate proposal is missing from exchange")
            if isinstance(item, AnalysisCandidateRunConfigSource):
                try:
                    candidate = resolve_candidate_config_from_snapshot(
                        CandidateConfig(parameter_proposal=proposal),
                        source_config=runs[run_id].configuration,
                    )
                except (CheckFailed, Conflict) as error:
                    raise ValueError(
                        "candidate proposal cannot resolve against "
                        "its retained baseline"
                    ) from error
                if config_content_hash(candidate) != item.content_hash:
                    raise ValueError(
                        "candidate configuration differs from retained proposal"
                    )
            if (
                isinstance(
                    item, AnalysisCandidateRunConfigSource | ParameterProposalRef
                )
                and item.analysis_record_id != proposal.analysis_record_id
            ):
                raise ValueError("candidate proposal publication differs")
            if (
                isinstance(
                    item,
                    AnalysisCandidateRunConfigSource | CandidateConfigRegistrySource,
                )
                and item.base_config_content_hash != proposal.base_config_content_hash
            ):
                raise ValueError("candidate proposal baseline differs")
            if (
                isinstance(item, ParameterProposalRef)
                and item.content_hash != f"sha256:{model_wire_content_hash(proposal)}"
            ):
                raise ValueError("composed proposal content identity differs")


def _validate_project_decision(
    item: ProjectAnalysisOutputReference,
    analyses: Mapping[tuple[AnalysisSubject, str], AnalysisEvidence],
) -> None:
    analysis = analyses.get((ProjectAnalysisSubject(), item.analysis_record_id))
    output = (
        None
        if analysis is None
        else next(
            (
                output
                for output in analysis.record.outputs
                if output.id == item.output_id
            ),
            None,
        )
    )
    if output is None:
        raise ValueError("project decision output is missing from exchange")
    if isinstance(item, ProjectAnalysisDecisionReference) and (
        not isinstance(output, AnalysisFactRecordOutput)
        or output.content.schema_id != item.schema_id
        or output.content.schema_hash != item.schema_hash
    ):
        raise ValueError("project decision fact schema differs")
