"""Notebook configuration intents over one daemon-owned registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from scopecat.api._remote import RemoteRunOperations
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle, run_handle_id
from scopecat.config.candidates import (
    CandidateConfig,
    CandidateSelection,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.drafts import ConfigDraft
from scopecat.config.inventory import InstrumentInventoryChange
from scopecat.config.registry.records import (
    CandidateAcceptance,
    ConfigRegistryActivationRecord,
    CrossRunCandidateAcceptance,
    ManualCandidateAcceptance,
)
from scopecat.config.resolution import config_revision_entry_id
from scopecat.daemon.client import DaemonClient, DaemonNotFoundError
from scopecat.daemon.views import (
    ActiveConfigView,
    ConfigActivationPage,
    ConfigDraftPreview,
    ConfigEntryView,
    ConfigRegistryPage,
    ParameterProposalPage,
)
from scopecat.daemon.wire import (
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigDraftCommand,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    DirectConfigRevisionSource,
    InstrumentInventoryMigrationCommand,
    InstrumentInventoryMigrationReceipt,
    ManualConfigDraftRevisionSource,
)
from scopecat.records.analysis import (
    AnalysisParameterProposalRecordOutput,
    ProjectAnalysisDecisionReference,
)
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
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

    def edit(
        self,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
    ) -> ConfigDraft:
        return ConfigDraft.from_snapshot(self.resolve(config))

    def resolve(
        self,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
    ) -> ConfigProfileSnapshot:
        return self.resolve_with_source(config)[0]

    def resolve_with_source(
        self,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
    ) -> tuple[ConfigProfileSnapshot, RunConfigSource | None]:
        selected = self.default_config if config is None else config
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
            raise ValueError("daemon config selector must be 'active'")
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
            return (
                resolved,
                AnalysisCandidateRunConfigSource(
                    source_run_id=selected.source_run_id,
                    analysis_record_id=selected.analysis_record_id,
                    proposal_id=selected.proposal_id,
                    base_config_content_hash=selected.base_config_content_hash,
                    content_hash=config_content_hash(resolved),
                ),
            )
        return selected, None

    def preview(
        self,
        draft: ConfigDraft,
        *,
        candidate_id: str | None = None,
    ) -> ConfigDraftPreview:
        active = self.client.active_config()
        if draft.base_content_hash != active.entry.content_hash:
            raise ValueError("config draft base is no longer the active configuration")
        return self.client.preview_config_draft(
            ConfigDraftCommand(
                base_entry_id=active.entry.id,
                base_content_hash=active.entry.content_hash,
                base_generation=active.activation.generation,
                candidate_id=candidate_id or f"{active.config.id}.draft",
                updates=draft.updates,
            )
        )

    def set_default(
        self,
        config: ConfigProfileSnapshot | ConfigDraft,
        *,
        entry_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> ConfigPublishReceipt:
        """Save one immutable revision and atomically make it the default."""

        selected_actor = actor or self.operator
        if isinstance(config, ConfigDraft):
            preview = self.preview(config)
            if (
                not preview.valid
                or preview.config is None
                or preview.result_content_hash is None
            ):
                raise ValueError("only a valid config draft can become the default")
            return self.publish_config(
                ConfigPublishCommand(
                    operation_id=_interactive_publish_operation_id(),
                    source=ManualConfigDraftRevisionSource(
                        draft=_reviewed_draft_command(config, preview),
                        expected_result_content_hash=preview.result_content_hash,
                    ),
                    entry_id=entry_id or config_revision_entry_id(preview.config),
                    actor=selected_actor,
                    expected_generation=preview.base_generation,
                    note=note,
                )
            )
        return self.publish_config(
            ConfigPublishCommand(
                operation_id=_interactive_publish_operation_id(),
                source=DirectConfigRevisionSource(config=config),
                entry_id=entry_id or config_revision_entry_id(config),
                actor=selected_actor,
                expected_generation=self._generation(),
                note=note,
            )
        )

    def publish_config(self, command: ConfigPublishCommand) -> ConfigPublishReceipt:
        """Publish one exact caller-owned idempotent config command."""

        return self.client.publish_config(command)

    def publish_operation(self, operation_id: str) -> ConfigPublishReceipt:
        """Reopen the exact durable result of a config publication."""

        return self.client.config_publish_operation(operation_id)

    def activate_entry(
        self,
        entry_id: str,
        *,
        operation_id: str,
        expected_generation: int,
        actor: str | None = None,
        note: str = "",
    ) -> ConfigActivationReceipt:
        """Select an exact saved entry, restoring it if previously activated.

        Restoration retains its original content and provenance, and records the
        most recent prior activation in ``restored_from_generation``. It does not
        renew calibration validity or execute devices. An unactivated derived
        entry still requires its original base to be active.
        """

        return self.client.activate_config_entry(
            ConfigEntryActivationCommand(
                operation_id=operation_id,
                entry_id=entry_id,
                actor=actor or self.operator,
                expected_generation=expected_generation,
                note=note,
            )
        )

    def activation_operation(self, operation_id: str) -> ConfigActivationReceipt:
        """Reopen the exact durable result of an activate-entry command."""

        return self.client.config_activation_operation(operation_id)

    def migrate_instrument_inventory(
        self,
        config: ConfigProfileSnapshot,
        *,
        changes: tuple[InstrumentInventoryChange, ...],
        entry_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> InstrumentInventoryMigrationReceipt:
        """Publish changed physical identities after their owners are drained."""

        return self.client.migrate_instrument_inventory(
            InstrumentInventoryMigrationCommand(
                config=config,
                entry_id=entry_id or config_revision_entry_id(config),
                changes=changes,
                actor=actor or self.operator,
                expected_generation=self._generation(),
                note=note,
            )
        )

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

    def accept(
        self,
        candidate: CandidateConfig | PublishedAnalysis,
        *,
        selection: CandidateSelection = None,
        entry_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> ConfigPublishReceipt:
        """Accept a candidate through an explicit operator review."""

        selected = _selected_candidate(candidate, selection)
        return self._accept_candidate(
            selected,
            acceptance=ManualCandidateAcceptance(),
            entry_id=entry_id,
            actor=actor,
            note=note,
        )

    def accept_verified(
        self,
        candidate: CandidateConfig | PublishedAnalysis,
        *,
        verified_by: tuple[PublishedAnalysis, str],
        selection: CandidateSelection = None,
        entry_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> ConfigPublishReceipt:
        """Accept a candidate through one positive cross-run decision fact."""

        selected = _selected_candidate(candidate, selection)
        verification_analysis, output_id = verified_by
        if verification_analysis.view.analysis.subject.kind != "project":
            raise TypeError("candidate verification must be a project analysis")
        decision = verification_analysis.fact(output_id)
        if (
            not isinstance(decision.value, dict)
            or decision.value.get("accepted") is not True
        ):
            raise ValueError(
                "candidate verification decision must contain accepted=true"
            )
        return self._accept_candidate(
            selected,
            acceptance=CrossRunCandidateAcceptance(
                decision=ProjectAnalysisDecisionReference(
                    analysis_record_id=verification_analysis.id,
                    output_id=output_id,
                    schema_id=decision.schema_id,
                    schema_hash=decision.schema_hash,
                )
            ),
            entry_id=entry_id,
            actor=actor,
            note=note,
        )

    def _accept_candidate(
        self,
        candidate: CandidateConfig,
        *,
        acceptance: CandidateAcceptance,
        entry_id: str | None,
        actor: str | None,
        note: str,
    ) -> ConfigPublishReceipt:
        selected_entry_id = entry_id
        if selected_entry_id is None:
            source_config = self.runs.load_config(candidate.source_run_id)
            resolved = resolve_candidate_config_from_snapshot(
                candidate,
                source_config=source_config,
            )
            selected_entry_id = f"{resolved.id}-{candidate.source_run_id}"
        return self.publish_config(
            ConfigPublishCommand(
                operation_id=_interactive_publish_operation_id(),
                source=CandidateConfigRevisionSource(
                    run_id=candidate.source_run_id,
                    proposal_id=candidate.proposal_id,
                    acceptance=acceptance,
                ),
                actor=actor or self.operator,
                expected_generation=self._generation(),
                entry_id=selected_entry_id,
                note=note,
            )
        )

    def undo(
        self,
        *,
        operation_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> ConfigActivationReceipt:
        """Reactivate the exact previous distinct entry through the operation ledger.

        Supply ``operation_id`` to reopen an ambiguous result later with
        :meth:`activation_operation`.
        """

        active = self.active().activation
        return self.activate_entry(
            _previous_distinct_entry_id(self.client, active),
            operation_id=operation_id or _interactive_activation_operation_id(),
            expected_generation=active.generation,
            actor=actor,
            note=note,
        )

    def _generation(self) -> int:
        activation = self.registry().activation
        return 0 if activation is None else activation.generation


def _selected_candidate(
    candidate: CandidateConfig | PublishedAnalysis,
    selection: CandidateSelection,
) -> CandidateConfig:
    if isinstance(candidate, PublishedAnalysis):
        return candidate.candidate_config(selection)
    if selection is not None:
        raise ValueError("proposal selection belongs on a PublishedAnalysis")
    return candidate


def _reviewed_draft_command(
    draft: ConfigDraft,
    preview: ConfigDraftPreview,
) -> ConfigDraftCommand:
    config = cast("ConfigProfileSnapshot", preview.config)
    return ConfigDraftCommand(
        base_entry_id=preview.base_entry.id,
        base_content_hash=preview.base_content_hash,
        base_generation=preview.base_generation,
        candidate_id=config.id,
        updates=draft.updates,
    )


def _interactive_publish_operation_id() -> str:
    return f"config-publish:{uuid4().hex}"


def _interactive_activation_operation_id() -> str:
    return f"config-activation:{uuid4().hex}"


def _previous_distinct_entry_id(
    client: DaemonClient,
    active: ConfigRegistryActivationRecord,
) -> str:
    before: int | None = None
    while True:
        page = client.config_activation_history(limit=100, before=before)
        for record in page.items:
            if (
                record.generation < active.generation
                and record.entry_id != active.entry_id
            ):
                return record.entry_id
        if page.next_cursor is None:
            raise ValueError("config registry has no previous active entry")
        before = page.next_cursor


__all__ = ["LabConfigOperations"]
