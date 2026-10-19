"""Course cleanup never claims old work or settles unfinished tasks implicitly."""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from scopecat.records.practice import NotebookPracticeClearCommand, PracticeScope

from scopecat_server.services.data_cleanup import DataCleanupService
from scopecat_server.services.notebook_practice import NotebookPracticeService
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

type Course = tuple[SQLiteProjectStore, NotebookPracticeService, PracticeScope]


@pytest.fixture
def course(tmp_path: Path) -> Iterator[Course]:
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        cleanup = DataCleanupService(store)
        service = NotebookPracticeService(store, cleanup)
        scope = service.enroll("source-one", tmp_path / "source", "parameters")
        yield store, service, scope


def add_run(
    store: SQLiteProjectStore,
    scope: PracticeScope | None,
    identity: str,
    *,
    state: str = "closed",
) -> None:
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO runs(run_id,created_at,config_content_hash) VALUES "
            "(?, 'now', 'hash')",
            (identity,),
        )
        connection.execute(
            "INSERT INTO "
            "scheduler_runs(submission_id,run_id,state,updated_at,"
            "admission_json,attention_reason) "
            "VALUES (?,?,?,'now','{}',?)",
            (
                identity,
                identity,
                state,
                "inspect" if state == "attention_required" else None,
            ),
        )
        if scope is not None:
            PracticeOwnership(connection).claim(scope.id, "run", identity)


def test_empty_course_can_clear_without_changing_old_or_other_courses(
    course: Course,
) -> None:
    store, service, scope = course
    other = service.enroll("source-two", Path("other"), "parameters")
    add_run(store, None, "ordinary")
    add_run(store, other, "other-course")
    settle = Mock()
    preview = service.preview(scope.id)
    assert preview.record_count == 0
    command = NotebookPracticeClearCommand(preview=preview)
    assert service.clear(scope.id, command, settle=settle).state == "cleared"
    assert service.clear(scope.id, command, settle=settle).state == "cleared"
    settle.assert_not_called()
    with store.sqlite.read_transaction() as connection:
        assert {row[0] for row in connection.execute("SELECT run_id FROM runs")} == {
            "ordinary",
            "other-course",
        }
        owners = PracticeOwnership(connection)
        assert owners.get(other.id).state == "active"
        with pytest.raises(ValueError, match="fenced"):
            owners.claim(scope.id, "run", "late")


@pytest.mark.parametrize("state", ["queued", "leased", "attention_required"])
def test_unfinished_run_blocks_cleanup_without_retiring_it(
    course: Course, state: str
) -> None:
    store, service, scope = course
    add_run(store, scope, "unfinished", state=state)
    preview = service.preview(scope.id)
    assert preview.blockers
    settle = Mock()
    with pytest.raises(ValueError, match="active tasks"):
        service.clear(
            scope.id, NotebookPracticeClearCommand(preview=preview), settle=settle
        )
    settle.assert_not_called()
    with store.sqlite.read_transaction() as connection:
        assert PracticeOwnership(connection).get(scope.id).state == "active"


def test_cleanup_fences_new_claims_and_retries_after_filesystem_failure(
    course: Course,
) -> None:
    store, service, scope = course
    add_run(store, scope, "owned")
    command = NotebookPracticeClearCommand(preview=service.preview(scope.id))
    with (
        patch.object(service.cleanup, "execute", side_effect=OSError("unavailable")),
        pytest.raises(OSError, match="unavailable"),
    ):
        service.clear(scope.id, command, settle=Mock())
    with store.sqlite.read_transaction() as connection:
        owners = PracticeOwnership(connection)
        assert owners.get(scope.id).state == "cleaning"
        with pytest.raises(ValueError, match="fenced"):
            owners.claim(scope.id, "run", "late")
    assert service.preview(scope.id) == command.preview
    assert service.clear(scope.id, command, settle=Mock()).state == "cleared"
    with store.sqlite.read_transaction() as connection:
        assert connection.execute("SELECT 1 FROM runs").fetchone() is None


def test_external_reference_blocks_and_stale_preview_cannot_clear_new_content(
    course: Course,
) -> None:
    store, service, scope = course
    add_run(store, scope, "owned")
    preview = service.preview(scope.id)
    add_run(store, None, "ordinary")
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "UPDATE scheduler_runs SET admission_json=? WHERE run_id='ordinary'",
            ('{"run_id":"owned"}',),
        )
    assert service.preview(scope.id).blockers
    with pytest.raises(ValueError, match="changed"):
        service.clear(
            scope.id, NotebookPracticeClearCommand(preview=preview), settle=Mock()
        )
    with store.sqlite.read_transaction() as connection:
        assert PracticeOwnership(connection).get(scope.id).state == "active"


@pytest.mark.parametrize(
    "state", ["ready", "leased", "waiting_for_input", "attention_required"]
)
def test_paused_or_waiting_procedure_requires_normal_task_controls(
    course: Course, state: str
) -> None:
    store, service, scope = course
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO procedure_runs(procedure_run_id,definition_id,"
            "definition_version,definition_fingerprint,request_key,intent_hash,"
            "revision,state,created_at,updated_at,run_json) "
            "VALUES ('task','lesson','1','hash','request','hash',1,?,'now','now','{}')",
            (state,),
        )
        PracticeOwnership(connection).claim(scope.id, "procedure", "task")
    preview = service.preview(scope.id)
    assert any(item.owner == "procedure:task" for item in preview.blockers)
    settle = Mock()
    with pytest.raises(ValueError, match="active tasks"):
        service.clear(
            scope.id, NotebookPracticeClearCommand(preview=preview), settle=settle
        )
    settle.assert_not_called()
