"""Resolve mutable parameter/setup choices without acquiring or dispatching hardware."""

from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.daemon.measurement_context import (
    MeasurementContextResolution,
    MeasurementContextResolve,
)
from scopecat.records.measurement_context import MeasurementContext
from scopecat.records.sample import SampleSelector

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.services.parameter_resolution import resolve_parameters
from scopecat_server.services.samples import SampleService
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.parameter_branches import ParameterBranchRepository
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore


class MeasurementContextService:
    def __init__(
        self,
        sqlite: SQLiteDatabase,
        samples: SampleService,
        targets: TargetCatalogStore,
    ) -> None:
        self._sqlite = sqlite
        self._samples = samples
        self._targets = targets

    def resolve(self, query: MeasurementContextResolve) -> MeasurementContextResolution:
        # Exact sample revisions are immutable. Mutable branch/setup heads share
        # one read transaction; no launch admission or global selection is changed.
        target = (
            self._targets.resolve(query.target) if query.target is not None else None
        )
        selectors = query.samples
        if target is not None:
            if len(target.content.members) != 1 or target.content.connections:
                raise BackendConflict(
                    "registered measurement context requires one target member "
                    "and no connections"
                )
            member = target.content.members[0]
            selectors = (
                SampleSelector(sample_id=member.sample_id, revision=member.revision),
            )
        samples = self._samples.resolve_bindings(selectors)
        revisions = (
            {
                (member.sample_id, member.revision): self._samples.revision(
                    member.sample_id, member.revision
                )
                for member in target.content.members
            }
            if target is not None
            else {}
        )
        with self._sqlite.read_transaction() as connection:
            branch = None
            parameters = query.parameters
            if query.branch is not None:
                try:
                    branch = ParameterBranchRepository(connection).get(query.branch)
                except KeyError as error:
                    raise BackendNotFound("parameter branch was not found") from error
                parameters = branch.revision
            assert parameters is not None
            setup = query.setup
            if setup is None:
                active = SQLiteSetupRepository(connection).read_current()
                if active is None:
                    raise BackendConflict(
                        "select a setup or activate one before resolving context"
                    )
                setup = active.revision.ref
            resolved = resolve_parameters(
                connection, parameters=parameters, setup=setup
            )
            try:
                binding = bind_scientific_evidence(
                    catalog_id=self._targets.catalog_id,
                    config=resolved.config,
                    samples=samples,
                    sample_revisions=revisions,
                    target=target,
                )
            except ValueError as error:
                raise BackendConflict(str(error)) from error
            return MeasurementContextResolution(
                context=MeasurementContext.from_binding(parameters, binding),
                branch=branch,
                setup=setup,
            )
