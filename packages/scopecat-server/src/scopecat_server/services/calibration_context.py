"""Resolve mutable parameter/setup choices without acquiring or dispatching hardware."""

from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.daemon.calibration_checks import (
    CalibrationContextResolution,
    CalibrationContextResolve,
)
from scopecat.records.calibration_check import CalibrationContext

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.services.parameter_resolution import resolve_parameters
from scopecat_server.services.samples import SampleService
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.parameter_branches import ParameterBranchRepository
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


class CalibrationContextService:
    def __init__(
        self, sqlite: SQLiteDatabase, samples: SampleService, catalog_id: str
    ) -> None:
        self._sqlite = sqlite
        self._samples = samples
        self._catalog_id = catalog_id

    def resolve(self, query: CalibrationContextResolve) -> CalibrationContextResolution:
        # Exact sample revisions are immutable. Mutable branch/setup heads share
        # one read transaction; no launch admission or global selection is changed.
        samples = self._samples.resolve_bindings(query.samples)
        with self._sqlite.read_transaction() as connection:
            try:
                branch = ParameterBranchRepository(connection).get(query.branch)
            except KeyError as error:
                raise BackendNotFound("parameter branch was not found") from error
            setup = query.setup
            if setup is None:
                active = SQLiteSetupRepository(connection).read_current()
                if active is None:
                    raise BackendConflict(
                        "select a setup or activate one before resolving context"
                    )
                setup = active.revision.ref
            resolved = resolve_parameters(
                connection, parameters=branch.revision, setup=setup
            )
            try:
                binding = bind_scientific_evidence(
                    catalog_id=self._catalog_id,
                    config=resolved.config,
                    samples=samples,
                    sample_revisions={},
                )
            except ValueError as error:
                raise BackendConflict(str(error)) from error
            return CalibrationContextResolution(
                context=CalibrationContext(
                    branch.revision,
                    binding.subject,
                    binding.setup_content_hash,
                    binding.scenario,
                ),
                branch=branch,
                setup=setup,
            )
