"""Configuration registry application service."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from threading import Lock

from scopecat.config.changes import (
    prepare_parameter_change_approval,
)
from scopecat.config.contexts import (
    apply_context_overrides,
    context_value_origins,
    missing_context_values,
)
from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.config.registry import service as config_registry_service
from scopecat.config.registry.records import (
    ConfigActivationOperation,
    ConfigContextPublishOperation,
    ConfigPublishOperation,
    ContextConfigRegistrySource,
    CrossRunCandidateAcceptance,
)
from scopecat.config.structure import (
    ParameterStructurePlan,
    ParameterStructurePreview,
    parameter_structure_version,
    preview_parameter_structure,
)
from scopecat.control.models import (
    DurableEventInput,
)
from scopecat.daemon.views import (
    ActiveConfigView,
    ConfigActivationPage,
    ConfigContextResolution,
    ConfigDraftPreview,
    ConfigEntryView,
    ConfigRegistryPage,
    ParameterResolution,
)
from scopecat.daemon.wire import (
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigContextPublishCommand,
    ConfigContextPublishReceipt,
    ConfigContextResolveCommand,
    ConfigContextSaveCommand,
    ConfigDraftCommand,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    ConfigSetupRebindCommand,
    ConfigSetupRebindPreviewCommand,
    DirectConfigRevisionSource,
    ManualConfigDraftRevisionSource,
    ParameterBindCommand,
    ParameterBranchCommitCommand,
    ParameterBranchPublishCommand,
    ParameterConfigRevisionSource,
    ParameterResolveCommand,
    ParameterSaveCommand,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
    NotFound,
)
from scopecat.project_state import ProjectStateServices
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.parameter_branch import (
    ParameterBranch,
    ParameterBranchPublication,
)
from scopecat.records.parameter_revision import (
    ParameterRevision,
    ParameterRevisionContent,
    parameter_revision_hash,
)
from scopecat.records.parameter_structure import (
    AddParameterColumn,
    ChangeParameterColumn,
)
from scopecat.records.run import (
    ParameterRunConfigSource,
)

from scopecat_server.storage.sqlite.config_operations import SQLiteConfigOperationStore
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.parameter_branches import ParameterBranchRepository
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

from ..errors import BackendConflict, BackendNotFound
from .analyses import AnalysisService
from .parameter_resolution import resolve_parameters
from .samples import SampleService


class ConfigService:
    """Own config-registry commands and their in-process serialization."""

    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        config_registry: SQLiteConfigRegistryStore,
        config_operations: SQLiteConfigOperationStore,
        runs: SQLiteRunRepository,
        services: ProjectStateServices,
        analyses: AnalysisService,
        samples: SampleService,
    ) -> None:
        self._samples = samples
        self._control = control
        self._config_registry = config_registry
        self._config_operations = config_operations
        self._runs = runs
        self._services = services
        self._analyses = analyses

        self._mutation_lock = Lock()

    def parameter_revisions(self) -> tuple[ParameterRevision, ...]:
        with self._control.sqlite.read_connection() as connection:
            return ParameterRevisionRepository(connection).list()

    def parameter_branch(self, name: str) -> ParameterBranch:
        with self._control.sqlite.read_connection() as connection:
            try:
                return ParameterBranchRepository(connection).get(name)
            except KeyError as error:
                raise BackendNotFound("parameter branch was not found") from error

    def parameter_branch_heads(
        self, *, limit: int, after: str | None
    ) -> tuple[ParameterBranch, ...]:
        with self._control.sqlite.read_connection() as connection:
            return ParameterBranchRepository(connection).heads(limit=limit, after=after)

    def parameter_branch_history(self, name: str) -> tuple[ParameterBranch, ...]:
        with self._control.sqlite.read_connection() as connection:
            return ParameterBranchRepository(connection).history(name)

    def commit_parameter_branch(
        self, command: ParameterBranchCommitCommand
    ) -> ParameterBranch:
        intent = sha256_json_hash(command.model_dump(mode="json"))
        with self._config_errors(), self._control.write_transaction() as connection:
            branches = ParameterBranchRepository(connection)
            revisions = ParameterRevisionRepository(connection)
            try:
                replay = branches.replay(
                    command.name, command.expected_generation + 1, intent
                )
                if replay is not None:
                    return replay
                try:
                    previous = branches.get(command.name)
                except KeyError:
                    previous = None
                if (
                    previous.generation if previous else 0
                ) != command.expected_generation:
                    raise BackendConflict(
                        "parameter branch changed; reload before saving"
                    )
                source = command.source
                if isinstance(source, ParameterSaveCommand):
                    problems = validate_parameter_snapshot(
                        source.catalog, source.parameters, allow_missing=True
                    )
                    if problems:
                        raise CheckFailed(problems)
                    revision = revisions.save(
                        ParameterRevision(
                            id=source.revision_id,
                            catalog=source.catalog,
                            parameters=source.parameters,
                            content_hash=parameter_revision_hash(
                                source.catalog, source.parameters
                            ),
                            actor=source.actor,
                            note=source.note,
                        )
                    )
                else:
                    revision = revisions.get(source.revision_id)
                    if revision.ref != source:
                        raise BackendConflict(
                            "parameter reference does not match saved content"
                        )
                branch = ParameterBranch(
                    name=command.name,
                    generation=command.expected_generation + 1,
                    revision=revision.ref,
                    previous=previous.revision if previous else None,
                    actor=command.actor,
                    note=command.note,
                )
                branches.append(branch, intent)
                return branch
            except KeyError as error:
                raise BackendNotFound("parameter revision was not found") from error
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def publish_parameter_branch(
        self, command: ParameterBranchPublishCommand
    ) -> ParameterBranch:
        """Commit exact candidate values and acceptance evidence with the head."""
        intent = sha256_json_hash(command.model_dump(mode="json"))
        with self._config_errors(), self._control.write_transaction() as connection:
            branches = ParameterBranchRepository(connection)
            revisions = ParameterRevisionRepository(connection)
            try:
                replay = branches.replay(
                    command.name, command.expected_generation + 1, intent
                )
                if replay is not None:
                    return replay
                head = branches.get(command.name)
                if (
                    head.generation != command.expected_generation
                    or head.revision != command.base
                ):
                    raise BackendConflict(
                        "parameter branch changed; reload before publishing"
                    )
                base = revisions.get(command.base.revision_id)
                baseline = self._runs.read_snapshot(command.run_id)
                source = baseline.config_source
                if (
                    not isinstance(source, ParameterRunConfigSource)
                    or source.parameters != command.base
                    or source.overrides
                    or baseline.outcome is None
                    or baseline.outcome.result != "succeeded"
                ):
                    raise BackendConflict(
                        "publication requires a successful baseline using the exact "
                        "branch revision without unsaved overrides"
                    )
                candidate = config_registry_service.validate_candidate_source_records(
                    storage=self._services.runs,
                    run_id=command.run_id,
                    proposal_id=command.proposal_id,
                    acceptance=CrossRunCandidateAcceptance(
                        decision=command.verification
                    ),
                )
                if candidate.config.parameter_catalog != base.catalog:
                    raise BackendConflict("candidate catalog differs from the branch")
                self._analyses.validate_candidate_verification(
                    command.verification,
                    source_run_id=command.run_id,
                    proposal_id=command.proposal_id,
                )
                revision = revisions.save(
                    ParameterRevision(
                        id=command.revision_id,
                        catalog=base.catalog,
                        parameters=candidate.config.parameter_snapshot,
                        content_hash=parameter_revision_hash(
                            base.catalog, candidate.config.parameter_snapshot
                        ),
                        actor=command.actor,
                        note=command.note,
                    )
                )
                published = ParameterBranch(
                    name=head.name,
                    generation=head.generation + 1,
                    revision=revision.ref,
                    previous=head.revision,
                    actor=command.actor,
                    note=command.note,
                    publication=ParameterBranchPublication(
                        run_id=command.run_id,
                        proposal_id=command.proposal_id,
                        verification=command.verification,
                    ),
                )
                branches.append(published, intent)
                return published
            except KeyError as error:
                raise BackendNotFound(
                    "parameter branch or revision was not found"
                ) from error
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def resolve_parameters(
        self, command: ParameterResolveCommand
    ) -> ParameterResolution:
        with self._control.sqlite.read_transaction() as connection:
            return resolve_parameters(
                connection,
                parameters=command.parameters,
                setup=command.setup,
                overrides=command.overrides,
            )

    def parameter_revision(self, revision_id: str) -> ParameterRevision:
        with self._control.sqlite.read_connection() as connection:
            try:
                return ParameterRevisionRepository(connection).get(revision_id)
            except KeyError as error:
                raise BackendNotFound("parameter revision was not found") from error

    def save_parameters(self, command: ParameterSaveCommand) -> ParameterRevision:
        with self._config_errors():
            problems = validate_parameter_snapshot(
                command.catalog, command.parameters, allow_missing=True
            )
            if problems:
                raise CheckFailed(problems)
            revision = ParameterRevision(
                id=command.revision_id,
                catalog=command.catalog,
                parameters=command.parameters,
                content_hash=parameter_revision_hash(
                    command.catalog, command.parameters
                ),
                actor=command.actor,
                note=command.note,
            )
            with self._control.write_transaction() as connection:
                try:
                    return ParameterRevisionRepository(connection).save(revision)
                except ValueError as error:
                    raise BackendConflict(str(error)) from error

    def bind_parameters(self, command: ParameterBindCommand) -> ConfigEntryView:
        """Resolve exact independent inputs; do not select setup or parameters."""
        with (
            self._mutation_lock,
            self._config_errors(),
            self._config_transaction() as (connection, services),
        ):
            try:
                parameters = ParameterRevisionRepository(connection).get(
                    command.parameters.revision_id
                )
                if parameters.ref != command.parameters:
                    raise BackendConflict(
                        "parameter reference does not match saved content"
                    )
                result = config_registry_service.save_config_revision(
                    revision=config_registry_service.ConfigRevision(
                        entry_id=command.entry_id,
                        actor=command.actor,
                        note=command.note,
                        source=config_registry_service.ParameterConfigRevisionSource(
                            parameters=ParameterRevisionContent(
                                id=command.entry_id,
                                system_id=command.system_id,
                                catalog=parameters.catalog,
                                parameters=parameters.parameters,
                            ),
                            setup=command.setup,
                            origin=parameters.ref,
                        ),
                    ),
                    unit_of_work=services.config_registry,
                )
                saved = config_registry_service.load_config_registry_entry_snapshot(
                    entry_id=result.entry.id,
                    unit_of_work=services.config_registry,
                )
                return ConfigEntryView(entry=saved.entry, config=saved.config)
            except KeyError as error:
                raise BackendNotFound(
                    "parameter or setup revision was not found"
                ) from error

    def preview_setup_rebind(
        self, command: ConfigSetupRebindPreviewCommand
    ) -> ConfigProfileSnapshot:
        with self._config_errors():
            try:
                return config_registry_service.preview_setup_rebind(
                    base=command.base,
                    setup=command.setup,
                    unit_of_work=self._config_registry.read_unit_of_work,
                )
            except KeyError as error:
                raise BackendNotFound("setup revision was not found") from error
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def rebind_setup(self, command: ConfigSetupRebindCommand) -> ConfigEntryView:
        with (
            self._mutation_lock,
            self._config_errors(),
            self._config_transaction() as (_, services),
        ):
            try:
                saved = config_registry_service.rebind_config_setup(
                    base=command.base,
                    setup=command.setup,
                    entry_id=command.entry_id,
                    actor=command.actor,
                    note=command.note,
                    unit_of_work=services.config_registry,
                )
                return ConfigEntryView(entry=saved.entry, config=saved.config)
            except KeyError as error:
                raise BackendNotFound("setup revision was not found") from error
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def latest_context(self, context: ConfigContextRef) -> ConfigEntryView:
        with self._config_errors():
            try:
                saved = config_registry_service.latest_parameter_context(
                    context, unit_of_work=self._config_registry.read_unit_of_work
                )
                return ConfigEntryView(entry=saved.entry, config=saved.config)
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def save_context(self, command: ConfigContextSaveCommand) -> ConfigEntryView:
        with (
            self._mutation_lock,
            self._config_errors(),
            self._config_transaction() as (_connection, services),
        ):
            try:
                self._validate_structure_evidence(command.structure_plan)
                selector = command.sample.model_copy(
                    update={"context_id": command.working_point_id}
                )
                sample = self._samples.resolve_bindings((selector,))[0]
                snapshot = config_registry_service.save_config_context(
                    entry_id=command.entry_id,
                    base=command.base,
                    sample=sample,
                    working_point_id=command.working_point_id,
                    label=command.label,
                    parameters=command.parameters,
                    structure_plan=command.structure_plan,
                    actor=command.actor,
                    note=command.note,
                    advance=command.advance,
                    unit_of_work=services.config_registry,
                )

                return ConfigEntryView(entry=snapshot.entry, config=snapshot.config)
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def preview_structure(
        self, plan: ParameterStructurePlan
    ) -> ParameterStructurePreview:
        with self._config_errors():
            self._validate_structure_evidence(plan)
            saved = self.get_config_entry(plan.base.entry_id)
            try:
                return preview_parameter_structure(saved.config, plan)
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def _validate_structure_evidence(self, plan: ParameterStructurePlan | None) -> None:
        if plan is None:
            return
        for run_id in {
            decision.source_run_id
            for edit in plan.edits
            if isinstance(edit, AddParameterColumn | ChangeParameterColumn)
            for decision in edit.values
            if decision.source_run_id is not None
        }:
            self._runs.read_snapshot(run_id)

    def resolve_context(
        self, command: ConfigContextResolveCommand
    ) -> ConfigContextResolution:
        with self._config_errors():
            try:
                saved = config_registry_service.load_config_registry_entry_snapshot(
                    entry_id=command.context.entry_id,
                    unit_of_work=self._config_registry.read_unit_of_work,
                )
                if (
                    saved.entry.content_hash != command.context.content_hash
                    or not isinstance(saved.entry.source, ContextConfigRegistrySource)
                ):
                    raise ValueError(
                        "context reference does not match a saved parameter context"
                    )
                resolved = apply_context_overrides(saved.config, command.overrides)
                metadata = saved.entry.source.context
                return ConfigContextResolution(
                    config=resolved,
                    config_source=ContextRunConfigSource(
                        context=command.context,
                        content_hash=config_content_hash(resolved),
                        sample=metadata.sample,
                        overrides=command.overrides,
                    ),
                    value_origins=context_value_origins(
                        resolved,
                        base=saved.config.parameter_snapshot,
                        base_ref=command.context,
                        selected_ref=command.context,
                        inherited=metadata.value_origins,
                        overrides=command.overrides,
                    ),
                    missing_values=missing_context_values(resolved),
                )
            except ValueError as error:
                raise BackendConflict(str(error)) from error

    def get_config_registry(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigRegistryPage:
        with self._config_errors():
            snapshot = config_registry_service.load_config_registry_page(
                limit=limit,
                before=before,
                unit_of_work=self._config_registry.read_unit_of_work,
            )
            return ConfigRegistryPage(
                entries=snapshot.entries,
                activation=snapshot.activation,
                next_cursor=snapshot.next_cursor,
            )

    def get_config_activation_history(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigActivationPage:
        with self._config_errors():
            page = config_registry_service.load_config_registry_activation_page(
                limit=limit,
                before=before,
                unit_of_work=self._config_registry.read_unit_of_work,
            )
            return ConfigActivationPage(
                items=page.items,
                next_cursor=page.next_cursor,
            )

    def get_active_config(self) -> ActiveConfigView:
        with self._config_errors():
            snapshot = config_registry_service.load_active_config_registry_snapshot(
                unit_of_work=self._config_registry.read_unit_of_work
            )
            return ActiveConfigView(
                entry=snapshot.entry,
                activation=snapshot.activation,
                config=snapshot.config,
            )

    def get_config_entry(self, entry_id: str) -> ConfigEntryView:
        with self._config_errors():
            snapshot = config_registry_service.load_config_registry_entry_snapshot(
                entry_id=entry_id,
                unit_of_work=self._config_registry.read_unit_of_work,
            )
            return ConfigEntryView(
                entry=snapshot.entry,
                config=snapshot.config,
                latest_activation=snapshot.latest_activation,
                structure_version=parameter_structure_version(
                    snapshot.config.parameter_catalog
                ),
            )

    def get_config_activation_operation(
        self,
        operation_id: str,
    ) -> ConfigActivationReceipt:
        with self._config_errors():
            receipt = self._config_operations.find(operation_id)
            if receipt is None:
                raise BackendNotFound(
                    f"config activation operation was not found: {operation_id}"
                )
            if not isinstance(receipt, ConfigActivationReceipt):
                raise BackendConflict(
                    f"config operation is not an activation: {operation_id}"
                )
            return receipt

    def get_config_publish_operation(
        self,
        operation_id: str,
    ) -> ConfigPublishReceipt:
        with self._config_errors():
            receipt = self._config_operations.find(operation_id)
            if receipt is None:
                raise BackendNotFound(
                    f"config publish operation was not found: {operation_id}"
                )
            if type(receipt) is not ConfigPublishReceipt:
                raise BackendConflict(
                    f"config operation is not a config publication: {operation_id}"
                )
            return receipt

    def get_context_publish_operation(
        self, operation_id: str
    ) -> ConfigContextPublishReceipt:
        with self._config_errors():
            receipt = self._config_operations.find(operation_id)
            if not isinstance(receipt, ConfigContextPublishReceipt):
                raise BackendNotFound(f"context publication not found: {operation_id}")
            return receipt

    def publish_context(
        self, command: ConfigContextPublishCommand
    ) -> ConfigContextPublishReceipt:
        """Commit verification, one working-point head, and receipt together."""
        with (
            self._mutation_lock,
            self._config_errors(),
            self._config_transaction() as (connection, services),
        ):
            existing = self._config_operations.find_in_transaction(
                connection, command.operation_id
            )
            if existing is not None:
                if (
                    not isinstance(existing, ConfigContextPublishReceipt)
                    or existing.operation.intent_hash != command.intent_hash
                ):
                    raise BackendConflict("config operation id has a different intent")
                return existing
            base = config_registry_service.load_config_registry_entry_snapshot(
                entry_id=command.base.entry_id,
                unit_of_work=services.config_registry,
            )
            if base.entry.content_hash != command.base.content_hash or not isinstance(
                base.entry.source, ContextConfigRegistrySource
            ):
                raise BackendConflict("publication requires an exact working point")
            metadata = base.entry.source.context
            baseline = self._runs.read_snapshot(command.run_id)
            if (
                not isinstance(baseline.config_source, ContextRunConfigSource)
                or baseline.config_source.context != command.base
                or baseline.samples != (metadata.sample,)
            ):
                raise BackendConflict(
                    "candidate source must use this exact working point "
                    "and sample scope"
                )
            acceptance = CrossRunCandidateAcceptance(decision=command.verification)
            with services.config_registry() as work:
                workspace_id = metadata.workspace_id
                if work.registry.context_head(workspace_id) != command.base.entry_id:
                    raise BackendConflict(
                        "Working point changed since candidate baseline"
                    )
                candidate = config_registry_service.validate_candidate_source_records(
                    storage=work.runs,
                    run_id=command.run_id,
                    proposal_id=command.proposal_id,
                    acceptance=acceptance,
                )
            if candidate.source.base_config_content_hash != command.base.content_hash:
                raise BackendConflict("candidate base differs from the working point")
            self._analyses.validate_candidate_verification(
                command.verification,
                source_run_id=command.run_id,
                proposal_id=command.proposal_id,
            )
            # Save performs the workspace-local CAS in this same transaction.
            try:
                saved = config_registry_service.save_config_context(
                    entry_id=command.entry_id,
                    base=command.base,
                    sample=metadata.sample,
                    working_point_id=metadata.working_point_id,
                    label=metadata.label,
                    parameters=candidate.config.parameter_snapshot,
                    advance=True,
                    publication=candidate.source,
                    actor=command.actor,
                    note=command.note,
                    unit_of_work=services.config_registry,
                )
            except ValueError as error:
                raise BackendConflict(str(error)) from error
            prepared = prepare_parameter_change_approval(
                run_id=command.run_id,
                selector=command.proposal_id,
                services=services,
                actor=command.actor,
                note=command.note,
            )
            if prepared.publication is not None:
                publication = self._runs.prepare_content_publication(
                    prepared.publication
                )
                self._runs.publish_prepared_content_in_transaction(
                    connection, publication
                )
                self._control.append_event_in_transaction(
                    connection,
                    DurableEventInput(
                        run_id=command.run_id,
                        kind="parameter_proposal_approved",
                        payload={
                            "proposal_id": command.proposal_id,
                            "actor": command.actor,
                        },
                        occurred_at=prepared.approval.approved_at,
                    ),
                )
            receipt = ConfigContextPublishReceipt(
                operation=ConfigContextPublishOperation(
                    operation_id=command.operation_id,
                    intent_hash=command.intent_hash,
                    base=command.base,
                    entry_id=command.entry_id,
                    actor=command.actor,
                    note=command.note,
                ),
                entry=saved.entry,
                deltas=candidate.deltas,
            )
            self._config_operations.commit_in_transaction(connection, receipt)

            return receipt

    def publish_config(
        self,
        command: ConfigPublishCommand,
    ) -> ConfigPublishReceipt:
        """Publish one revision; candidate approval shares the same commit."""

        receipt = self._publish_revision(command)
        assert type(receipt) is ConfigPublishReceipt
        return receipt

    def _publish_revision(
        self,
        command: ConfigPublishCommand,
    ) -> ConfigPublishReceipt:
        with self._mutation_lock, self._config_errors():
            with self._config_transaction() as transaction:
                connection, services = transaction
                existing = self._config_operations.find_in_transaction(
                    connection,
                    command.operation_id,
                )
                if existing is not None:
                    if (
                        type(existing) is not ConfigPublishReceipt
                        or existing.operation.intent_hash != command.intent_hash
                    ):
                        raise BackendConflict(
                            "config operation id is already committed for a different "
                            f"intent: {command.operation_id}"
                        )
                    return existing
                source = command.source
                if isinstance(source, CandidateConfigRevisionSource):
                    if isinstance(source.acceptance, CrossRunCandidateAcceptance):
                        self._analyses.validate_candidate_verification(
                            source.acceptance.decision,
                            source_run_id=source.run_id,
                            proposal_id=source.proposal_id,
                        )
                    prepared = prepare_parameter_change_approval(
                        run_id=source.run_id,
                        selector=source.proposal_id,
                        services=self._services,
                        actor=command.actor,
                        note=command.note,
                    )
                    if prepared.publication is not None:
                        publication = self._runs.prepare_content_publication(
                            prepared.publication
                        )
                        self._runs.publish_prepared_content_in_transaction(
                            connection,
                            publication,
                        )
                        self._control.append_event_in_transaction(
                            connection,
                            DurableEventInput(
                                run_id=source.run_id,
                                kind="parameter_proposal_approved",
                                payload={
                                    "proposal_id": source.proposal_id,
                                    "actor": command.actor,
                                },
                                occurred_at=prepared.approval.approved_at,
                            ),
                        )
                result = config_registry_service.publish_config_revision(
                    revision=_config_revision(command),
                    unit_of_work=services.config_registry,
                    expected_generation=command.expected_generation,
                )
                self._append_revision_events(connection, command, result)
                activation = result.activation
                assert activation is not None
                operation = ConfigPublishOperation(
                    operation_id=command.operation_id,
                    intent_hash=command.intent_hash,
                    source_intent_hash=command.source_intent_hash,
                    entry_id=command.entry_id,
                    expected_generation=command.expected_generation,
                    actor=command.actor,
                    note=command.note,
                    activation_generation=activation.generation,
                )
                receipt = ConfigPublishReceipt(
                    operation=operation,
                    entry=result.entry,
                    deltas=result.deltas,
                    activation=activation,
                )
                self._config_operations.commit_in_transaction(connection, receipt)
            return receipt

    def preview_config_draft(
        self,
        command: ConfigDraftCommand,
    ) -> ConfigDraftPreview:
        with self._config_errors():
            result = config_registry_service.preview_manual_config_draft(
                unit_of_work=self._config_registry.read_unit_of_work,
                base_entry_id=command.base_entry_id,
                base_config_content_hash=command.base_content_hash,
                base_generation=command.base_generation,
                candidate_id=command.candidate_id,
                updates=command.updates,
            )
            candidate = result.check.candidate
            return ConfigDraftPreview(
                valid=result.check.ok,
                base_entry=result.base_entry,
                base_generation=result.base_generation,
                base_content_hash=result.base_entry.content_hash,
                config=candidate,
                result_content_hash=(
                    None if candidate is None else config_content_hash(candidate)
                ),
                deltas=result.check.deltas,
                problems=result.check.problems,
            )

    def activate_config_entry(
        self,
        command: ConfigEntryActivationCommand,
    ) -> ConfigActivationReceipt:
        with self._mutation_lock, self._config_errors():
            with self._config_transaction() as transaction:
                connection, services = transaction
                existing = self._config_operations.find_in_transaction(
                    connection,
                    command.operation_id,
                )
                if existing is not None:
                    if (
                        not isinstance(existing, ConfigActivationReceipt)
                        or existing.operation.intent_hash != command.intent_hash
                    ):
                        raise BackendConflict(
                            "config operation id is already committed for a different "
                            f"intent: {command.operation_id}"
                        )
                    return existing
                result = config_registry_service.activate_config_registry_entry(
                    entry_id=command.entry_id,
                    unit_of_work=services.config_registry,
                    actor=command.actor,
                    expected_generation=command.expected_generation,
                    note=command.note,
                )
                activation = result.activation
                assert activation is not None
                if result.activated:
                    self._control.append_event_in_transaction(
                        connection,
                        DurableEventInput(
                            kind="config_activated",
                            payload={
                                "entry_id": activation.entry_id,
                                "generation": activation.generation,
                            },
                            occurred_at=activation.recorded_at,
                        ),
                    )
                operation = ConfigActivationOperation(
                    operation_id=command.operation_id,
                    intent_hash=command.intent_hash,
                    entry_id=command.entry_id,
                    expected_generation=command.expected_generation,
                    actor=command.actor,
                    note=command.note,
                    activation_generation=activation.generation,
                )
                receipt = ConfigActivationReceipt(
                    operation=operation,
                    activation=activation,
                )
                self._config_operations.commit_in_transaction(
                    connection,
                    receipt,
                )
            return receipt

    def _append_revision_events(
        self,
        connection: sqlite3.Connection,
        command: ConfigPublishCommand,
        result: config_registry_service.ConfigRegistryMutationResult,
    ) -> None:
        source = command.source
        run_id = (
            source.run_id if isinstance(source, CandidateConfigRevisionSource) else None
        )
        if result.saved:
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    run_id=run_id,
                    kind="config_saved",
                    payload={"entry_id": result.entry.id},
                    occurred_at=result.entry.recorded_at,
                ),
            )
        activation = result.activation
        if result.activated and activation is not None:
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    run_id=run_id,
                    kind="config_activated",
                    payload={
                        "entry_id": result.entry.id,
                        "generation": activation.generation,
                    },
                    occurred_at=activation.recorded_at,
                ),
            )

    @contextmanager
    def _config_transaction(
        self,
    ) -> Generator[tuple[sqlite3.Connection, ProjectStateServices]]:
        """Commit registry state and replay events through one SQLite writer."""

        with self._control.write_transaction() as connection:
            services = replace(
                self._services,
                config_registry=lambda: self._config_registry.borrowed_unit_of_work(
                    connection
                ),
            )
            yield connection, services

    @contextmanager
    def _config_errors(self) -> Generator[None]:
        try:
            yield
        except NotFound as error:
            raise BackendNotFound(str(error)) from error
        except (
            CheckFailed,
            Conflict,
            DataIntegrityError,
        ) as error:
            raise BackendConflict(str(error)) from error


def _config_revision(
    command: ConfigPublishCommand,
) -> config_registry_service.ConfigRevision:
    source = command.source
    if isinstance(source, DirectConfigRevisionSource):
        revision_source = config_registry_service.DirectConfigRevisionSource(
            source.config
        )
    elif isinstance(source, ParameterConfigRevisionSource):
        revision_source = config_registry_service.ParameterConfigRevisionSource(
            parameters=source.parameters, setup=source.setup
        )
    elif isinstance(source, ManualConfigDraftRevisionSource):
        draft = source.draft
        revision_source = config_registry_service.ManualConfigDraftRevisionSource(
            base_entry_id=draft.base_entry_id,
            base_config_content_hash=draft.base_content_hash,
            base_generation=draft.base_generation,
            candidate_id=draft.candidate_id,
            updates=draft.updates,
            expected_result_content_hash=source.expected_result_content_hash,
        )
    else:
        revision_source = config_registry_service.CandidateConfigRevisionSource(
            run_id=source.run_id,
            proposal_id=source.proposal_id,
            acceptance=source.acceptance,
        )
    return config_registry_service.ConfigRevision(
        source=revision_source,
        entry_id=command.entry_id,
        actor=command.actor,
        note=command.note,
    )
