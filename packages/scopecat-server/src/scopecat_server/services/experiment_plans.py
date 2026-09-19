"""Validate exact plan references without compiling, dispatching, or activating."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scopecat.daemon.wire import ConfigContextResolveCommand
from scopecat.records.experiment_plan import (
    ExperimentPlanDefinition,
    ExperimentPlanRevision,
    ExperimentPlanSave,
)
from scopecat.records.scientific_selection import (
    SavedConfiguration,
    WorkingPointConfiguration,
    require_selection_binding,
)

from scopecat_server.errors import BackendConflict
from scopecat_server.services.scientific_binding import validate_scientific_binding
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore

if TYPE_CHECKING:
    from scopecat_server.services.author_workspaces import AuthorWorkspaceServices
    from scopecat_server.services.config import ConfigService
    from scopecat_server.services.runs import RunService
    from scopecat_server.services.samples import SampleService


class ExperimentPlanService:
    def __init__(
        self,
        repository: ExperimentPlanRepository,
        *,
        config: ConfigService,
        samples: SampleService,
        runs: RunService,
        authors: AuthorWorkspaceServices,
        targets: TargetCatalogStore,
    ) -> None:
        self.repository = repository
        self.config = config
        self.samples = samples
        self.runs = runs
        self.authors = authors
        self.targets = targets

    def validate_definition(self, definition: ExperimentPlanDefinition) -> None:
        choice = definition.selection.configuration
        if isinstance(choice, SavedConfiguration):
            entry = self.config.get_config_entry(choice.ref.entry_id)
            if entry.entry.content_hash != choice.ref.content_hash:
                raise BackendConflict("plan configuration content hash does not match")
            config = entry.config
        elif isinstance(choice, WorkingPointConfiguration):
            context = self.config.resolve_context(
                ConfigContextResolveCommand(
                    context=choice.ref, overrides=choice.overrides
                )
            )
            config = context.config
            if (
                context.config_source.sample
                not in definition.scientific_binding.samples
            ):
                raise BackendConflict("plan sample must match its exact saved context")
        else:
            raise BackendConflict(
                "saved plan requires exact saved configuration or working point"
            )
        try:
            require_selection_binding(
                definition.selection, definition.scientific_binding
            )
        except ValueError as error:
            raise BackendConflict(str(error)) from error
        validate_scientific_binding(
            definition.scientific_binding,
            config,
            sample_service=self.samples,
            targets=self.targets,
        )
        if definition.code_revision is not None:
            self.authors.get(definition.workspace_id).get(definition.code_revision)
        if definition.source is not None:
            source = definition.source
            publication = self.runs.get_run_analysis(source.run_id, source.analysis_id)
            if (
                publication.entry.id != source.analysis_id
                or publication.analysis.publication_hash != source.publication_hash
            ):
                raise BackendConflict(
                    "plan analysis source does not match its exact publication"
                )

    def save(self, command: ExperimentPlanSave) -> ExperimentPlanRevision:
        self.validate_definition(command.definition)
        return self.repository.save(command)
