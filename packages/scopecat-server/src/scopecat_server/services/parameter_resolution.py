"""Read-only resolution shared by preview, saved plans and run admission."""

import sqlite3

from scopecat.config.resolution import compose_configuration
from scopecat.daemon.views import ParameterResolution
from scopecat.kernel.errors import CheckFailed
from scopecat.records.config import config_content_hash
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.setup import SetupRevisionRef

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


def resolve_parameters(
    connection: sqlite3.Connection,
    *,
    parameters: ParameterRevisionRef,
    setup: SetupRevisionRef,
) -> ParameterResolution:
    try:
        values = ParameterRevisionRepository(connection).get(parameters.revision_id)
        equipment = SQLiteSetupRepository(connection).read_revision(setup.revision_id)
    except KeyError as error:
        raise BackendNotFound("parameter or setup revision was not found") from error
    if values.ref != parameters or equipment.ref != setup:
        raise BackendConflict("parameter/setup reference differs from saved content")
    try:
        config = compose_configuration(
            equipment.setup,
            id=values.id,
            system_id="resolved-parameters",
            catalog=values.catalog,
            parameters=values.parameters,
        )
    except (CheckFailed, ValueError) as error:
        raise BackendConflict(str(error)) from error
    return ParameterResolution(
        config=config,
        config_source=ParameterRunConfigSource(
            parameters=parameters,
            setup=setup,
            content_hash=config_content_hash(config),
        ),
    )
