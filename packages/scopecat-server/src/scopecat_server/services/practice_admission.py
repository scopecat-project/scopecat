"""Derive practice authority from retained owners, never a client simulation flag."""

import sqlite3

from scopecat.daemon.wire import RunSubmission
from scopecat.records.configuration_fence import (
    ProcedureConfigurationFence,
    SetupRevisionFence,
)
from scopecat.records.practice import PracticeResource
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ParameterRunConfigSource,
)

from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


def require_practice_setup(connection: sqlite3.Connection, revision_id: str) -> None:
    setup = SQLiteSetupRepository(connection).read_revision(revision_id).setup
    if setup.instrument_registry.instruments or setup.domain_target is not None:
        raise ValueError(
            "Practice permits software computation only; "
            "physical device capabilities are unavailable"
        )
    if setup.scenario is None:
        raise ValueError("Practice requires an explicit software scenario")


def run_scope(connection: sqlite3.Connection, submission: RunSubmission) -> str | None:
    resources: list[tuple[PracticeResource, str]] = [
        ("setup", submission.execution_setup.revision_id)
    ]
    if submission.procedure_child is not None:
        resources.append(("procedure", submission.procedure_child.procedure_run_id))
    source = submission.config_source
    if isinstance(source, ParameterRunConfigSource):
        resources.append(("parameters", source.parameters.revision_id))
    elif isinstance(source, AnalysisCandidateRunConfigSource):
        resources.append(("run", source.source_run_id))
    scope = PracticeOwnership(connection).require_shared(*resources)
    if scope is not None:
        require_practice_setup(connection, submission.execution_setup.revision_id)
        if (
            submission.request.samples
            or submission.request.record_collection is not None
        ):
            raise ValueError(
                "Practice cannot write to ordinary samples or record collections"
            )
    return scope


def procedure_scope(
    connection: sqlite3.Connection, fence: ProcedureConfigurationFence | None
) -> str | None:
    if not isinstance(fence, SetupRevisionFence):
        return None
    scope = PracticeOwnership(connection).require_shared(
        ("setup", fence.revision.revision_id)
    )
    if scope is not None:
        require_practice_setup(connection, fence.revision.revision_id)
    return scope
