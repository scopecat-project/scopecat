"""SQLite persistence for durable one-shot procedure schedules."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from pydantic import ValidationError
from scopecat.automation import ProcedureSchedule, ProcedureScheduleState

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.practice import PracticeOwnership


class ProcedureScheduleStoreError(RuntimeError):
    """Durable procedure schedule state could not be read or committed."""


class ProcedureScheduleNotFound(ProcedureScheduleStoreError):
    """A requested durable procedure schedule does not exist."""


class ProcedureScheduleConflict(ProcedureScheduleStoreError):
    """A schedule command conflicts with current durable state."""


@dataclass(frozen=True, slots=True)
class StoredProcedureSchedulePage:
    """Newest-first keyset page of durable procedure schedules."""

    items: tuple[ProcedureSchedule, ...]
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class StoredProcedureScheduleDuePage:
    """Insertion-oldest keyset page of pending due schedules."""

    items: tuple[ProcedureSchedule, ...]
    next_cursor: int | None = None
    through_sequence: int | None = None


class SQLiteProcedureScheduleStore:
    """Revisioned one-shot procedure schedule snapshots."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.sqlite = database
        self.database = database.path

    @contextmanager
    def write_transaction(self) -> Generator[sqlite3.Connection]:
        with self.sqlite.write_transaction() as connection:
            yield connection

    def read(self, schedule_id: str) -> ProcedureSchedule:
        with self.sqlite.read_connection() as connection:
            return self.read_in_transaction(connection, schedule_id)

    def read_in_transaction(
        self,
        connection: sqlite3.Connection,
        schedule_id: str,
    ) -> ProcedureSchedule:
        row = _one(
            connection.execute(
                "SELECT schedule_json FROM procedure_schedules WHERE schedule_id = ?",
                (schedule_id,),
            )
        )
        if row is None:
            raise ProcedureScheduleNotFound(
                f"procedure schedule was not found: {schedule_id}"
            )
        return _schedule(row)

    def list(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ProcedureScheduleState | None = None,
    ) -> StoredProcedureSchedulePage:
        if not 1 <= limit <= 500:
            raise ValueError("procedure schedule page size must be between 1 and 500")
        if before is not None and before < 1:
            raise ValueError("procedure schedule cursor must be positive")
        clauses: list[str] = []
        parameters: list[str | int] = []
        if before is not None:
            clauses.append("sequence < ?")
            parameters.append(before)
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        parameters.append(limit + 1)
        try:
            with self.sqlite.read_connection() as connection:
                rows = _all(
                    connection.execute(
                        f"""
                        SELECT sequence, schedule_json FROM procedure_schedules
                        {where}
                        ORDER BY sequence DESC
                        LIMIT ?
                        """,  # noqa: S608 - clauses are fixed internal fragments
                        parameters,
                    )
                )
            selected = rows[:limit]
            return StoredProcedureSchedulePage(
                items=tuple(_schedule(row) for row in selected),
                next_cursor=(
                    _integer(selected[-1], "sequence") if len(rows) > limit else None
                ),
            )
        except (sqlite3.Error, ValidationError) as error:
            raise ProcedureScheduleStoreError(
                "failed to list procedure schedules"
            ) from error

    def due(
        self,
        *,
        at: datetime,
        limit: int = 50,
        after: int | None = None,
        through_sequence: int | None = None,
    ) -> StoredProcedureScheduleDuePage:
        if not 1 <= limit <= 500:
            raise ValueError(
                "due procedure schedule page size must be between 1 and 500"
            )
        if after is not None and after < 1:
            raise ValueError("due procedure schedule cursor must be positive")
        _require_traversal_pair(after, through_sequence)
        try:
            with self.sqlite.read_transaction() as connection:
                traversal_end = through_sequence
                if traversal_end is None:
                    row = _one(
                        connection.execute(
                            "SELECT MAX(sequence) AS sequence FROM procedure_schedules"
                        )
                    )
                    traversal_end = (
                        None if row is None else _optional_integer(row, "sequence")
                    )
                if traversal_end is None:
                    return StoredProcedureScheduleDuePage(items=())
                rows = _all(
                    connection.execute(
                        """
                        SELECT sequence, schedule_json FROM procedure_schedules
                        WHERE state = 'pending'
                          AND due_at <= ?
                          AND (? IS NULL OR sequence > ?)
                          AND sequence <= ?
                        ORDER BY sequence ASC
                        LIMIT ?
                        """,
                        (
                            _timestamp(at),
                            after,
                            after,
                            traversal_end,
                            limit + 1,
                        ),
                    )
                )
            selected = rows[:limit]
            has_next = len(rows) > limit
            return StoredProcedureScheduleDuePage(
                items=tuple(_schedule(row) for row in selected),
                next_cursor=(_integer(selected[-1], "sequence") if has_next else None),
                through_sequence=traversal_end if has_next else None,
            )
        except (sqlite3.Error, ValidationError) as error:
            raise ProcedureScheduleStoreError(
                "failed to list due procedure schedules"
            ) from error

    def insert_in_transaction(
        self,
        connection: sqlite3.Connection,
        schedule: ProcedureSchedule,
    ) -> None:
        try:
            connection.execute(
                """
                INSERT INTO procedure_schedules(
                    schedule_id, intent_hash, due_at, revision, state,
                    created_at, updated_at, procedure_run_id, schedule_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.schedule_id,
                    schedule.intent_hash,
                    _timestamp(schedule.due_at),
                    schedule.revision,
                    schedule.state,
                    _timestamp(schedule.created_at),
                    _timestamp(schedule.updated_at),
                    _procedure_run_id(schedule),
                    schedule.model_dump_json(),
                ),
            )
            PracticeOwnership(connection).claim_created(
                "schedule",
                schedule.schedule_id,
                workspace_id=schedule.source.workspace_id if schedule.source else None,
            )
        except sqlite3.IntegrityError as error:
            raise ProcedureScheduleConflict(
                "procedure schedule id or materialized run already exists"
            ) from error

    def replace_in_transaction(
        self,
        connection: sqlite3.Connection,
        schedule: ProcedureSchedule,
        *,
        expected_revision: int,
    ) -> None:
        PracticeOwnership(connection).require_writer("schedule", schedule.schedule_id)
        current = self.read_in_transaction(connection, schedule.schedule_id)
        if current.revision != expected_revision:
            raise ProcedureScheduleConflict("procedure schedule revision changed")
        if schedule.revision != expected_revision + 1:
            raise ProcedureScheduleConflict(
                "procedure schedule revision must advance exactly once"
            )
        if (
            schedule.definition != current.definition
            or schedule.intent != current.intent
            or schedule.intent_hash != current.intent_hash
            or schedule.due_at != current.due_at
            or schedule.created_at != current.created_at
        ):
            raise ProcedureScheduleConflict("procedure schedule identity is immutable")
        try:
            updated = connection.execute(
                """
                UPDATE procedure_schedules SET
                    revision = ?, state = ?, updated_at = ?,
                    procedure_run_id = ?, schedule_json = ?
                WHERE schedule_id = ? AND revision = ?
                """,
                (
                    schedule.revision,
                    schedule.state,
                    _timestamp(schedule.updated_at),
                    _procedure_run_id(schedule),
                    schedule.model_dump_json(),
                    schedule.schedule_id,
                    expected_revision,
                ),
            ).rowcount
        except sqlite3.IntegrityError as error:
            raise ProcedureScheduleConflict(
                "procedure schedule materialization conflicts with durable state"
            ) from error
        if updated != 1:
            raise ProcedureScheduleConflict("procedure schedule revision changed")


def _procedure_run_id(schedule: ProcedureSchedule) -> str | None:
    if schedule.materialization is None:
        return None
    return schedule.materialization.procedure_run_id


def _schedule(row: sqlite3.Row) -> ProcedureSchedule:
    try:
        return ProcedureSchedule.model_validate_json(_text(row, "schedule_json"))
    except ValidationError as error:
        raise ProcedureScheduleStoreError(
            "invalid durable procedure schedule"
        ) from error


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("procedure schedule timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _all(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


def _text(row: sqlite3.Row, key: str) -> str:
    return cast("str", row[key])


def _integer(row: sqlite3.Row, key: str) -> int:
    return cast("int", row[key])


def _optional_integer(row: sqlite3.Row, key: str) -> int | None:
    return cast("int | None", row[key])


def _require_traversal_pair(
    after: int | None,
    through_sequence: int | None,
) -> None:
    if (after is None) != (through_sequence is None):
        raise ValueError(
            "due procedure schedule cursor and through_sequence must be "
            "provided together"
        )
    if after is not None and through_sequence is not None and after >= through_sequence:
        raise ValueError("due procedure schedule cursor must be below through_sequence")


__all__ = [
    "ProcedureScheduleConflict",
    "ProcedureScheduleNotFound",
    "ProcedureScheduleStoreError",
    "SQLiteProcedureScheduleStore",
    "StoredProcedureScheduleDuePage",
    "StoredProcedureSchedulePage",
]
