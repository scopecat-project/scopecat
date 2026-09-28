"""Notebook configuration intents over one daemon-owned registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scopecat.api._remote import RemoteRunOperations
from scopecat.api.parameter_candidates import ParameterCandidate, stage_candidate
from scopecat.api.parameters import RowKey
from scopecat.api.published_analysis import AnalysisResult
from scopecat.api.run import RunHandle, run_handle_id
from scopecat.authoring.parameter_models import (
    ParameterFieldIdentity,
    ParameterModel,
)
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.daemon.client import DaemonClient, DaemonNotFoundError
from scopecat.daemon.views import (
    ActiveConfigView,
    ConfigActivationPage,
    ConfigContextResolution,
    ConfigEntryView,
    ConfigRegistryPage,
    ParameterProposalPage,
    ParameterResolution,
)
from scopecat.daemon.wire import (
    ConfigContextResolveCommand,
)
from scopecat.records.analysis import (
    AnalysisParameterProposalRecordOutput,
)
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
    RunConfigSource,
)
from scopecat.runs.selectors import RunSelector


@dataclass(frozen=True, slots=True)
class LabConfigOperations:
    """Configuration editing, provenance, and default-selection intents."""

    client: DaemonClient
    runs: RemoteRunOperations
    default_config: ConfigProfileSnapshot | None
    operator: str

    @property
    def run_operations(self) -> RemoteRunOperations:
        return self.runs

    def stage[ResultT](
        self,
        result: AnalysisResult[ResultT],
        *,
        name: str,
        table: str | type[ParameterModel],
        key: RowKey,
        fields: Mapping[str | ParameterFieldIdentity, str],
        note: str = "",
    ) -> ParameterCandidate:
        """Save a receipt-backed cell proposal; fields maps target to result name."""
        return stage_candidate(
            self, result, name=name, table=table, key=key, fields=fields, note=note
        )

    def candidate(self, run_id: str, name: str) -> ParameterCandidate:
        """Reopen an exact saved proposal; names are scoped to their source run."""
        proposal = self.client.parameter_proposal(run_id, name).proposal
        selected = CandidateConfig(proposal)
        self.resolve_with_source(selected)
        return ParameterCandidate(self, selected)

    def resolve_context(
        self,
        context: ConfigContextRef,
        *,
        overrides: tuple[ParameterUpdate, ...] = (),
    ) -> ConfigContextResolution:
        return self.client.resolve_context(
            ConfigContextResolveCommand(context=context, overrides=overrides)
        )

    def registry(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigRegistryPage:
        return self.client.config_registry(limit=limit, before=before)

    def history(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigActivationPage:
        return self.client.config_activation_history(limit=limit, before=before)

    def active(self) -> ActiveConfigView:
        return self.client.active_config()

    def entry(self, entry_id: str) -> ConfigEntryView:
        return self.client.config_entry(entry_id)

    def resolve(
        self,
        config: str
        | ConfigProfileSnapshot
        | CandidateConfig
        | ConfigContextRef
        | ConfigContextResolution
        | ParameterResolution
        | None = None,
    ) -> ConfigProfileSnapshot:
        return self.resolve_with_source(config)[0]

    def resolve_with_source(
        self,
        config: str
        | ConfigProfileSnapshot
        | CandidateConfig
        | ConfigContextRef
        | ConfigContextResolution
        | ParameterResolution
        | None = None,
    ) -> tuple[ConfigProfileSnapshot, RunConfigSource | None]:
        selected = self.default_config if config is None else config
        if isinstance(selected, ConfigContextRef):
            selected = self.resolve_context(selected)
        if isinstance(selected, ConfigContextResolution | ParameterResolution):
            return selected.config, selected.config_source
        if selected is None or selected == "active":
            active = self.client.active_config()
            return (
                active.config,
                ConfigRegistryRunConfigSource(
                    selector="active",
                    entry_id=active.entry.id,
                    config_ref=active.entry.config_ref,
                    content_hash=active.entry.content_hash,
                    registry_generation=active.activation.generation,
                ),
            )
        if isinstance(selected, str):
            saved = self.entry(selected)
            return (
                saved.config,
                ConfigRegistryRunConfigSource(
                    selector=saved.entry.id,
                    entry_id=saved.entry.id,
                    config_ref=saved.entry.config_ref,
                    content_hash=saved.entry.content_hash,
                ),
            )
        if isinstance(selected, CandidateConfig):
            proposal = selected.parameter_proposal
            try:
                saved_proposal = self.client.parameter_proposal(
                    selected.source_run_id,
                    proposal.id,
                ).proposal
            except DaemonNotFoundError:
                saved_proposal = None
            if saved_proposal != proposal:
                raise ValueError(
                    "save the producing analysis before using its candidate config"
                )
            analysis = self.runs.analysis(
                selected.source_run_id,
                selected.analysis_record_id,
            )
            if not any(
                isinstance(output, AnalysisParameterProposalRecordOutput)
                and output.content.proposal_id == proposal.id
                for output in analysis.analysis.outputs
            ):
                raise ValueError(
                    "candidate proposal does not belong to its producing analysis"
                )
            resolved = resolve_candidate_config_from_snapshot(
                selected,
                source_config=self.runs.load_config(selected.source_run_id),
            )
            setup = self.client.get_run(selected.source_run_id).snapshot.execution_setup
            if setup is None:
                raise ValueError(
                    "candidate baseline has no retained application setup; "
                    "collect the baseline through the application before trying it"
                )
            return (
                resolved,
                AnalysisCandidateRunConfigSource(
                    source_run_id=selected.source_run_id,
                    analysis_record_id=selected.analysis_record_id,
                    proposal_id=selected.proposal_id,
                    base_config_content_hash=selected.base_config_content_hash,
                    content_hash=config_content_hash(resolved),
                    setup=setup,
                ),
            )
        return selected, None

    def proposals(
        self,
        run: RunSelector | RunHandle,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ParameterProposalPage:
        return self.client.parameter_proposals(
            run_handle_id(run),
            limit=limit,
            before=before,
        )


__all__ = ["LabConfigOperations"]
