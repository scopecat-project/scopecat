from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat.records.practice import PracticeScope

from scopecat_server.errors import BackendConflict
from scopecat_server.request_workspace import request_workspace
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


@contextmanager
def workspace(identity: str):
    token = request_workspace.set(identity)
    try:
        yield
    finally:
        request_workspace.reset(token)


def revision(identity: str) -> ParameterRevision:
    catalog = ParameterCatalog(id=identity)
    parameters = ParameterSnapshot(id=identity)
    return ParameterRevision(
        id=identity,
        catalog=catalog,
        parameters=parameters,
        content_hash=parameter_revision_hash(catalog, parameters),
        actor="test",
    )


def test_registered_course_claims_only_new_records_and_fences_late_writes(
    tmp_path: Path,
):
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        with store.sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            scope = PracticeScope(
                id="course",
                title="Parameters",
                lesson="parameters",
                directory="notes",
                kind="notebook",
                workspace_id="source-one",
            )
            owners.save(scope)
            owners.claim(scope.id, "source", "source-one")
            ordinary = ParameterRevisionRepository(connection).save(
                revision("ordinary")
            )
        with workspace("source-one"), store.sqlite.write_transaction() as connection:
            revisions = ParameterRevisionRepository(connection)
            assert revisions.save(ordinary) == ordinary
            revisions.save(revision("owned"))
            owners = PracticeOwnership(connection)
            assert owners.owner("parameters", "ordinary") is None
            assert owners.owner("parameters", "owned") == "course"
            owners.save(scope.model_copy(update={"state": "cleaning"}))
        with (
            workspace("source-one"),
            pytest.raises(ValueError, match="fenced"),
            store.sqlite.write_transaction() as connection,
        ):
            ParameterRevisionRepository(connection).save(revision("late"))
        with store.sqlite.read_transaction() as connection, pytest.raises(KeyError):
            ParameterRevisionRepository(connection).get("late")


def test_course_context_does_not_transfer_between_registered_sources(tmp_path: Path):
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        with store.sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            for identity in ("one", "two"):
                scope = PracticeScope(
                    id=identity,
                    title=identity,
                    lesson="parameters",
                    directory=identity,
                    kind="notebook",
                    workspace_id=identity,
                )
                owners.save(scope)
                owners.claim(identity, "source", identity)
                owners.claim(identity, "branch", identity)
            with workspace("one"):
                owners.require_writer("branch", "one")
                with pytest.raises(BackendConflict, match="another practice"):
                    owners.require_writer("branch", "two")
                with pytest.raises(BackendConflict, match="another practice"):
                    owners.require_writer("branch", "ordinary")
            with (
                workspace("ordinary"),
                pytest.raises(BackendConflict, match="another practice"),
            ):
                owners.require_writer("branch", "one")
            assert owners.workspace_scope() is None


def test_no_workspace_is_ordinary_and_worker_writes_still_require_active_scope(
    tmp_path: Path,
):
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        with store.sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            scope = PracticeScope(
                id="course",
                title="Course",
                lesson="parameters",
                directory="course",
                kind="notebook",
                workspace_id="course-source",
            )
            owners.save(scope)
            owners.claim(scope.id, "source", "course-source")
            for kind in (
                "branch",
                "plan",
                "schedule",
                "calibration_task",
                "run",
                "procedure",
            ):
                owners.claim(scope.id, kind, "owned")
                with pytest.raises(BackendConflict, match="another practice"):
                    owners.require_writer(kind, "owned")
                owners.require_writer(kind, "ordinary")
                with workspace("course-source"):
                    owners.require_writer(kind, "owned")
                owners.require_writer(kind, "owned", worker=True)
                if kind in {"run", "procedure"}:
                    with pytest.raises(BackendConflict, match="another practice"):
                        owners.require_operator(kind, "owned")
                    with workspace("course-source"):
                        owners.require_operator(kind, "owned")
            owners.save(scope.model_copy(update={"state": "cleaning"}))
            with pytest.raises(BackendConflict, match="fenced"):
                owners.require_writer("calibration_task", "owned", worker=True)
            with workspace("course-source"):
                for kind in (
                    "branch",
                    "plan",
                    "schedule",
                    "calibration_task",
                    "run",
                    "procedure",
                ):
                    with pytest.raises(BackendConflict, match="fenced"):
                        owners.require_writer(kind, "owned")
                    if kind in {"run", "procedure"}:
                        with pytest.raises(BackendConflict, match="fenced"):
                            owners.require_operator(kind, "owned")
