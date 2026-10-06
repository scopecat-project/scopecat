"""Independent parameter editing and retained configuration evidence."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from threading import Lock

from scopecat.config.contexts import (
    apply_context_overrides,
    context_value_origins,
    missing_context_values,
)
from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.config.registry import service as config_registry_service
from scopecat.config.registry.records import (
    ContextConfigRegistrySource,
    CrossRunCandidateAcceptance,
)
from scopecat.config.structure import (
    parameter_structure_version,
)
from scopecat.daemon.views import (
    ConfigContextResolution,
    ConfigEntryView,
    ConfigRegistryPage,
    ParameterResolution,
)
from scopecat.daemon.wire import (
    ConfigContextResolveCommand,
    ParameterBranchCommitCommand,
    ParameterBranchPublishCommand,
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
from scopecat.records.config import config_content_hash
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.parameter_branch import (
    ParameterBranch,
    ParameterBranchPublication,
)
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat.records.run import (
    ParameterRunConfigSource,
)

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
    """Own parameter branches and resolve exact scientific inputs."""

    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        config_registry: SQLiteConfigRegistryStore,
        runs: SQLiteRunRepository,
        services: ProjectStateServices,
        analyses: AnalysisService,
        samples: SampleService,
    ) -> None:
        self._samples = samples
        self._control = control
        self._config_registry = config_registry
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
        with self._control.write_transaction() as connection:
            return self.commit_parameter_branch_in_transaction(connection, command)

    def commit_parameter_branch_in_transaction(
        self, connection: sqlite3.Connection, command: ParameterBranchCommitCommand
    ) -> ParameterBranch:
        """Compose the same branch operation into an owning atomic transaction."""
        intent = sha256_json_hash(command.model_dump(mode="json"))
        with self._config_errors():
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
        with self._control.write_transaction() as connection:
            return self.save_parameters_in_transaction(connection, command)

    def save_parameters_in_transaction(
        self, connection: sqlite3.Connection, command: ParameterSaveCommand
    ) -> ParameterRevision:
        """Save scientific input within an owning application transaction."""
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
            try:
                return ParameterRevisionRepository(connection).save(revision)
            except ValueError as error:
                raise BackendConflict(str(error)) from error

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
                next_cursor=snapshot.next_cursor,
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
                structure_version=parameter_structure_version(
                    snapshot.config.parameter_catalog
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
