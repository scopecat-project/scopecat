from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureSchedule,
    ProcedureScheduleCancellation,
    procedure_intent_hash,
)

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.procedure_schedules import (
    SQLiteProcedureScheduleStore,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

_START = datetime(2026, 8, 18, 9, tzinfo=UTC)
_HASH = "sha256:" + "1" * 64


def _store(tmp_path: Path) -> SQLiteProcedureScheduleStore:
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    SQLiteProjectStore(sqlite, tmp_path / "objects").bootstrap()
    return SQLiteProcedureScheduleStore(sqlite)


def _schedule(schedule_id: str, *, due_at: datetime) -> ProcedureSchedule:
    definition = ProcedureDefinitionRef(
        id="drag-calibration",
        version="1",
        fingerprint=_HASH,
    )
    intent = {"qubits": [schedule_id]}
    return ProcedureSchedule(
        schedule_id=schedule_id,
        definition=definition,
        intent=intent,
        intent_hash=procedure_intent_hash(definition, intent),
        due_at=due_at,
        revision=1,
        state="pending",
        created_at=_START,
        updated_at=_START,
    )


def test_store_round_trips_pages_and_due_schedules_by_insertion_keyset(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    later = _schedule("later", due_at=_START + timedelta(minutes=2))
    earlier = _schedule("earlier", due_at=_START + timedelta(minutes=1))
    future = _schedule("future", due_at=_START + timedelta(hours=1))
    with store.write_transaction() as connection:
        for schedule in (later, earlier, future):
            store.insert_in_transaction(connection, schedule)

    newest = store.list(limit=1)
    assert newest.items == (future,)
    assert newest.next_cursor is not None
    assert store.list(limit=2, before=newest.next_cursor).items == (earlier, later)

    offset_now = (_START + timedelta(minutes=3)).astimezone(
        timezone(timedelta(hours=8))
    )
    first_due = store.due(at=offset_now, limit=1)
    assert first_due.items == (later,)
    assert first_due.next_cursor is not None
    assert store.due(
        at=offset_now,
        limit=1,
        after=first_due.next_cursor,
        through_sequence=first_due.through_sequence,
    ).items == (earlier,)
    assert store.due(at=offset_now, limit=10).items == (later, earlier)
    with pytest.raises(ValueError, match="cursor must be positive"):
        store.due(at=offset_now, after=0)

    cancelled_at = _START + timedelta(minutes=4)
    cancelled = earlier.model_copy(
        update={
            "revision": 2,
            "state": "cancelled",
            "updated_at": cancelled_at,
            "cancellation": ProcedureScheduleCancellation(
                actor="operator",
                reason="maintenance",
                cancelled_at=cancelled_at,
            ),
        }
    )
    with store.write_transaction() as connection:
        store.replace_in_transaction(connection, cancelled, expected_revision=1)
    assert store.read(earlier.schedule_id) == cancelled
    assert store.list(state="cancelled").items == (cancelled,)
    assert store.due(at=offset_now + timedelta(hours=1)).items == (later, future)


def test_course_due_and_catalog_traversal_never_visit_other_scopes(tmp_path: Path):
    from scopecat.records.practice import PracticeScope

    from scopecat_server.request_workspace import request_workspace
    from scopecat_server.storage.sqlite.practice import PracticeOwnership

    store = _store(tmp_path)
    with store.write_transaction() as connection:
        owners = PracticeOwnership(connection)
        for identity in ("one", "two", "empty"):
            owners.save(
                PracticeScope(
                    id=identity,
                    title=identity,
                    lesson="parameters",
                    directory=identity,
                    kind="notebook",
                    workspace_id=identity,
                )
            )
            owners.claim(identity, "source", identity)
        for identity, scope in (
            ("ordinary-1", None),
            ("one-1", "one"),
            ("two-1", "two"),
            ("ordinary-2", None),
            ("one-2", "one"),
            ("two-2", "two"),
        ):
            store.insert_in_transaction(connection, _schedule(identity, due_at=_START))
            if scope is not None:
                owners.claim(scope, "schedule", identity)
    for scope in (None, "one", "two", "empty"):
        token = request_workspace.set(scope)
        try:
            prefix = scope or "ordinary"
            expected = () if scope == "empty" else (f"{prefix}-1", f"{prefix}-2")
            assert tuple(item.schedule_id for item in store.list().items) == tuple(
                reversed(expected)
            )
            page = store.due(at=_START, limit=1)
            if not expected:
                assert page.items == () and page.next_cursor is None
                continue
            assert tuple(item.schedule_id for item in page.items) == expected[:1]
            assert page.next_cursor is not None and page.through_sequence is not None
            with store.write_transaction() as connection:
                store.insert_in_transaction(
                    connection, _schedule(f"{prefix}-late", due_at=_START)
                )
            rest = store.due(
                at=_START,
                limit=1,
                after=page.next_cursor,
                through_sequence=page.through_sequence,
            )
            assert tuple(item.schedule_id for item in rest.items) == expected[1:]
            assert rest.next_cursor is None
            catalog = store.list(limit=1)
            assert catalog.items[0].schedule_id == f"{prefix}-late"
            assert catalog.next_cursor is not None
            older = store.list(limit=10, before=catalog.next_cursor)
            assert tuple(item.schedule_id for item in older.items) == tuple(
                reversed(expected)
            )
        finally:
            request_workspace.reset(token)
