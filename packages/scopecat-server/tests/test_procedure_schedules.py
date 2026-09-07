from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureRunListQuery,
    ProcedureScheduleCancelCommand,
    ProcedureScheduleCreateCommand,
    ProcedureScheduleDueQuery,
    ProcedureScheduleListQuery,
    ProcedureScheduleMaterializeCommand,
    ProcedureSubmitCommand,
    procedure_schedule_request_key,
)

from scopecat_server import BackendConflict
from scopecat_server.services.automation import AutomationService
from scopecat_server.services.procedure_schedules import ProcedureScheduleService
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.procedure_schedules import (
    ProcedureScheduleConflict,
    SQLiteProcedureScheduleStore,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

_START = datetime(2026, 8, 18, 9, tzinfo=UTC)
_HASH = "sha256:" + "1" * 64


def _definition() -> ProcedureDefinitionRef:
    return ProcedureDefinitionRef(
        id="drag-calibration",
        version="1",
        fingerprint=_HASH,
    )


def _command(
    schedule_id: str = "schedule-1",
    *,
    due_at: datetime | None = None,
    qubit: str = "q0",
) -> ProcedureScheduleCreateCommand:
    return ProcedureScheduleCreateCommand(
        schedule_id=schedule_id,
        definition=_definition(),
        intent={"qubits": [qubit]},
        due_at=due_at or (_START + timedelta(minutes=1)),
    )


def _services(
    tmp_path: Path,
) -> tuple[
    ProcedureScheduleService,
    AutomationService,
    SQLiteProcedureScheduleStore,
    list[datetime],
]:
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    SQLiteProjectStore(sqlite, tmp_path / "objects").bootstrap()
    now = [_START]
    automation = AutomationService(
        SQLiteAutomationStore(sqlite),
        runs=SQLiteRunRepository(sqlite, tmp_path / "objects"),
        clock=lambda: now[0],
    )
    store = SQLiteProcedureScheduleStore(sqlite)
    return (
        ProcedureScheduleService(store, automation, clock=lambda: now[0]),
        automation,
        store,
        now,
    )


def test_schedule_create_list_cancel_and_terminal_create_replay(
    tmp_path: Path,
) -> None:
    service, _, _, _ = _services(tmp_path)
    command = _command()
    created = service.create(command).schedule
    assert created.state == "pending"
    assert service.create(command).schedule == created

    with pytest.raises(BackendConflict, match="different specification"):
        service.create(_command(qubit="q1"))

    cancelled_command = ProcedureScheduleCancelCommand(
        schedule_id=created.schedule_id,
        expected_schedule_revision=created.revision,
        actor="operator",
        reason="maintenance",
    )
    cancelled = service.cancel(cancelled_command).schedule
    assert cancelled.state == "cancelled"
    assert cancelled.revision == 2
    assert service.cancel(cancelled_command).schedule == cancelled
    assert service.create(command).schedule == cancelled
    assert service.list(ProcedureScheduleListQuery(state="cancelled")).items == (
        cancelled,
    )

    with pytest.raises(BackendConflict, match="revision changed"):
        service.cancel(
            cancelled_command.model_copy(update={"expected_schedule_revision": 999})
        )
    with pytest.raises(BackendConflict, match="cancelled differently"):
        service.cancel(cancelled_command.model_copy(update={"reason": "other"}))
    with pytest.raises(BackendConflict, match=r"cancelled.*cannot be materialized"):
        service.materialize(
            ProcedureScheduleMaterializeCommand(
                schedule_id=cancelled.schedule_id,
                expected_schedule_revision=cancelled.revision,
            )
        )


def test_schedule_materialization_is_due_atomic_and_exactly_replayable(
    tmp_path: Path,
) -> None:
    service, automation, _, now = _services(tmp_path)
    command = _command()
    pending = service.create(command).schedule
    materialize = ProcedureScheduleMaterializeCommand(
        schedule_id=pending.schedule_id,
        expected_schedule_revision=pending.revision,
    )

    assert service.due(ProcedureScheduleDueQuery()).items == ()
    with pytest.raises(BackendConflict, match="not due"):
        service.materialize(materialize)
    assert automation.list(ProcedureRunListQuery()).items == ()

    now[0] = command.due_at.astimezone(timezone(timedelta(hours=8)))
    assert service.due(ProcedureScheduleDueQuery()).items == (pending,)
    terminal = service.materialize(materialize).schedule
    assert terminal.state == "materialized"
    assert terminal.revision == 2
    assert terminal.materialization is not None
    run = automation.get(terminal.materialization.procedure_run_id)
    assert run.state == "ready"
    assert run.definition == command.definition
    assert run.intent == command.intent
    assert run.request_key == terminal.materialization.request_key

    assert service.materialize(materialize).schedule == terminal
    assert service.create(command).schedule == terminal
    assert service.due(ProcedureScheduleDueQuery()).items == ()
    assert len(automation.list(ProcedureRunListQuery()).items) == 1
    with pytest.raises(BackendConflict, match="revision changed"):
        service.materialize(
            materialize.model_copy(update={"expected_schedule_revision": 999})
        )
    with pytest.raises(BackendConflict, match=r"materialized.*cannot be cancelled"):
        service.cancel(
            ProcedureScheduleCancelCommand(
                schedule_id=terminal.schedule_id,
                expected_schedule_revision=terminal.revision,
                actor="operator",
                reason="too late",
            )
        )


def test_due_schedule_high_water_wraps_to_newly_due_lower_sequence(
    tmp_path: Path,
) -> None:
    service, _, _, now = _services(tmp_path)
    poison = service.create(
        _command("poison", due_at=_START - timedelta(minutes=1))
    ).schedule
    initially_future = service.create(
        _command("initially-future", due_at=_START + timedelta(hours=1))
    ).schedule
    third = service.create(
        _command("third", due_at=_START - timedelta(minutes=5))
    ).schedule

    first_page = service.due(ProcedureScheduleDueQuery(limit=1))
    assert first_page.items == (poison,)
    assert first_page.next_cursor is not None
    assert first_page.through_sequence is not None
    end_of_first_traversal = service.due(
        ProcedureScheduleDueQuery(
            cursor=first_page.next_cursor,
            through_sequence=first_page.through_sequence,
            limit=1,
        )
    )
    assert end_of_first_traversal.items == (third,)
    assert end_of_first_traversal.next_cursor is None
    assert end_of_first_traversal.through_sequence is None

    now[0] = _START + timedelta(hours=2)
    fourth = service.create(_command("fourth", due_at=_START)).schedule
    wrapped_first = service.due(ProcedureScheduleDueQuery(limit=1))
    assert wrapped_first.items == (poison,)
    assert wrapped_first.next_cursor is not None
    assert wrapped_first.through_sequence is not None

    fifth = service.create(_command("fifth", due_at=_START)).schedule
    wrapped_second = service.due(
        ProcedureScheduleDueQuery(
            cursor=wrapped_first.next_cursor,
            through_sequence=wrapped_first.through_sequence,
            limit=1,
        )
    )
    assert wrapped_second.items == (initially_future,)
    assert wrapped_second.next_cursor is not None
    assert wrapped_second.through_sequence == wrapped_first.through_sequence

    sixth = service.create(_command("sixth", due_at=_START)).schedule
    wrapped_third = service.due(
        ProcedureScheduleDueQuery(
            cursor=wrapped_second.next_cursor,
            through_sequence=wrapped_second.through_sequence,
            limit=1,
        )
    )
    assert wrapped_third.items == (third,)
    assert wrapped_third.next_cursor is not None
    end_of_second_traversal = service.due(
        ProcedureScheduleDueQuery(
            cursor=wrapped_third.next_cursor,
            through_sequence=wrapped_third.through_sequence,
            limit=1,
        )
    )
    assert end_of_second_traversal.items == (fourth,)
    assert end_of_second_traversal.next_cursor is None
    assert end_of_second_traversal.through_sequence is None

    next_traversal = service.due(ProcedureScheduleDueQuery(limit=10))
    assert next_traversal.items == (
        poison,
        initially_future,
        third,
        fourth,
        fifth,
        sixth,
    )


def test_schedule_materialization_rolls_back_run_when_terminal_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, automation, store, now = _services(tmp_path)
    command = _command(due_at=_START)
    pending = service.create(command).schedule

    def fail_replace(
        connection: sqlite3.Connection,
        schedule: object,
        *,
        expected_revision: int,
    ) -> None:
        del connection, schedule, expected_revision
        raise ProcedureScheduleConflict("injected schedule write failure")

    monkeypatch.setattr(store, "replace_in_transaction", fail_replace)
    now[0] = _START + timedelta(seconds=1)
    with pytest.raises(BackendConflict, match="injected schedule write failure"):
        service.materialize(
            ProcedureScheduleMaterializeCommand(
                schedule_id=pending.schedule_id,
                expected_schedule_revision=pending.revision,
            )
        )

    assert store.read(pending.schedule_id) == pending
    assert automation.list(ProcedureRunListQuery()).items == ()


def test_pending_schedule_refuses_to_adopt_a_preexisting_deterministic_run(
    tmp_path: Path,
) -> None:
    service, automation, _, now = _services(tmp_path)
    command = _command(due_at=_START)
    pending = service.create(command).schedule
    request_key = procedure_schedule_request_key(
        pending.schedule_id,
        pending.due_at,
        pending.definition,
        pending.intent_hash,
    )
    existing = automation.submit(
        ProcedureSubmitCommand(
            request_key=request_key,
            definition=pending.definition,
            intent=pending.intent,
        )
    ).run

    now[0] = _START + timedelta(seconds=1)
    with pytest.raises(BackendConflict, match="already has a durable run"):
        service.materialize(
            ProcedureScheduleMaterializeCommand(
                schedule_id=pending.schedule_id,
                expected_schedule_revision=pending.revision,
            )
        )

    assert service.get(pending.schedule_id) == pending
    assert automation.list(ProcedureRunListQuery()).items == (existing,)
