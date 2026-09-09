"""Validate exact plan references without compiling, dispatching, or activating."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scopecat.daemon.wire import ConfigContextResolveCommand
from scopecat.records.experiment_plan import (
    ExperimentPlanDefinition,
    ExperimentPlanRevision,
    ExperimentPlanSave,
)
from scopecat.records.sample import SampleSelector

from scopecat_server.errors import BackendConflict
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)

if TYPE_CHECKING:
    from scopecat_server.services.author_revisions import AuthorRevisionService
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
        authors: AuthorRevisionService,
    ) -> None:
        self.repository = repository
        self.config = config
        self.samples = samples
        self.runs = runs
        self.authors = authors

    def validate_definition(self, definition: ExperimentPlanDefinition) -> None:
        if definition.configuration is not None:
            entry = self.config.get_config_entry(definition.configuration.entry_id)
            if entry.entry.content_hash != definition.configuration.content_hash:
                raise BackendConflict("plan configuration content hash does not match")
        if definition.context is not None:
            context = self.config.resolve_context(
                ConfigContextResolveCommand(
                    context=definition.context, overrides=definition.overrides
                )
            )
            if context.config_source.sample != definition.sample:
                raise BackendConflict("plan sample must match its exact saved context")
        if definition.sample is not None:
            sample = definition.sample
            actual = self.samples.resolve_bindings(
                (
                    SampleSelector(
                        sample_id=sample.sample_id,
                        revision=sample.revision,
                        role=sample.role,
                        context_id=sample.context_id,
                    ),
                )
            )
            if actual != (sample,):
                raise BackendConflict(
                    "plan sample binding does not match its immutable revision"
                )
        if definition.code_revision is not None:
            self.authors.get(definition.code_revision)
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
