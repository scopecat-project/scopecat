"""SQLite persistence for durable multi-run procedures."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from pydantic import ValidationError
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureRunState,
    ProcedureStepAttempt,
)

from scopecat_server.storage.sqlite.connection import SQLiteDatabase


class AutomationStoreError(RuntimeError):
    """Durable procedure state could not be read or committed."""


class AutomationNotFound(AutomationStoreError):
    """A requested durable procedure object does not exist."""


class AutomationConflict(AutomationStoreError):
    """A procedure command conflicts with current durable state."""


@dataclass(frozen=True, slots=True)
class ProcedureLeaseRecord:
    """Server-owned fencing authority for one procedure worker."""

    procedure_run_id: str
    worker_id: str
    token: str
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProcedureRunPage:
    """Newest-first keyset page of durable procedure runs."""

    items: tuple[ProcedureRun, ...]
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class ProcedureStepAttemptPage:
    """Newest-first keyset page of durable procedure step attempts."""

    items: tuple[ProcedureStepAttempt, ...]
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class RunnableProcedureRunPage:
    """Oldest-first procedure runs compatible with one worker."""

    items: tuple[ProcedureRun, ...]
    has_more: bool = False


class SQLiteAutomationStore:
    """Revisioned procedure snapshots and short-lived worker leases."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.sqlite = database
        self.database = database.path

    @contextmanager
    def write_transaction(self) -> Generator[sqlite3.Connection]:
        with self.sqlite.write_transaction() as connection:
            yield connection

    def read_run(self, procedure_run_id: str) -> ProcedureRun:
        with self.sqlite.read_connection() as connection:
            return self.read_run_in_transaction(connection, procedure_run_id)

    def read_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
    ) -> ProcedureRun:
        row = _one(
            connection.execute(
                "SELECT run_json FROM procedure_runs WHERE procedure_run_id = ?",
                (procedure_run_id,),
            )
        )
        if row is None:
            raise AutomationNotFound(f"procedure run was not found: {procedure_run_id}")
        return _run(row)

    def find_run_by_request(
        self,
        definition_id: str,
        request_key: str,
    ) -> ProcedureRun | None:
        with self.sqlite.read_connection() as connection:
            return self.find_run_by_request_in_transaction(
                connection,
                definition_id,
                request_key,
            )

    def find_run_by_request_in_transaction(
        self,
        connection: sqlite3.Connection,
        definition_id: str,
        request_key: str,
    ) -> ProcedureRun | None:
        row = _one(
            connection.execute(
                """
                SELECT run_json FROM procedure_runs
                WHERE definition_id = ? AND request_key = ?
                """,
                (definition_id, request_key),
            )
        )
        return None if row is None else _run(row)

    def list_runs(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ProcedureRunState | None = None,
    ) -> ProcedureRunPage:
        if not 1 <= limit <= 500:
            raise ValueError("procedure run page size must be between 1 and 500")
        if before is not None and before < 1:
            raise ValueError("procedure run cursor must be positive")
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
                        SELECT sequence, run_json FROM procedure_runs
                        {where}
                        ORDER BY sequence DESC
                        LIMIT ?
                        """,  # noqa: S608 - clauses are fixed internal fragments
                        parameters,
                    )
                )
            selected = rows[:limit]
            return ProcedureRunPage(
                items=tuple(_run(row) for row in selected),
                next_cursor=(
                    _integer(selected[-1], "sequence") if len(rows) > limit else None
                ),
            )
        except (sqlite3.Error, ValidationError) as error:
            raise AutomationStoreError("failed to list procedure runs") from error

    def list_runnable(
        self,
        definitions: tuple[ProcedureDefinitionRef, ...],
        *,
        at: datetime,
        limit: int = 50,
    ) -> RunnableProcedureRunPage:
        """List ready or expired-leased runs for exact worker capabilities."""

        if not 1 <= limit <= 500:
            raise ValueError("runnable procedure page size must be between 1 and 500")
        if not definitions:
            return RunnableProcedureRunPage(items=())
        values = ", ".join("(?, ?, ?)" for _definition in definitions)
        parameters: list[str | int] = []
        for definition in definitions:
            parameters.extend(
                (definition.id, definition.version, definition.fingerprint)
            )
        parameters.extend((_timestamp(at), limit + 1))
        try:
            with self.sqlite.read_connection() as connection:
                rows = _all(
                    connection.execute(
                        f"""
                        WITH capabilities(
                            definition_id,
                            definition_version,
                            definition_fingerprint
                        ) AS (VALUES {values})
                        SELECT runs.run_json
                        FROM procedure_runs AS runs
                        JOIN capabilities AS capabilities
                          ON capabilities.definition_id = runs.definition_id
                         AND capabilities.definition_version = runs.definition_version
                         AND capabilities.definition_fingerprint =
                             runs.definition_fingerprint
                        LEFT JOIN procedure_leases AS leases
                          ON leases.procedure_run_id = runs.procedure_run_id
                        WHERE (runs.state = 'ready'
                           OR (
                               runs.state = 'leased'
                               AND leases.expires_at <= ?
                           ))
                           AND (
                             json_extract(runs.run_json,
                               '$.resource_wait.run_id') IS NULL
                             OR EXISTS (
                               SELECT 1 FROM scheduler_runs AS child
                               WHERE child.run_id = json_extract(
                                 runs.run_json, '$.resource_wait.run_id')
                               AND (child.state = 'closed' OR (
                                 child.state = 'queued'
                                 AND NOT EXISTS (
                                   SELECT 1 FROM run_execution_segments AS segment
                                   WHERE segment.run_id = child.run_id
                                 )
                                 AND NOT EXISTS (
                                   SELECT 1 FROM run_resource_claims AS needed
                                   JOIN resource_claims AS held
                                     USING (resource_kind, resource_id)
                                   WHERE needed.run_id = child.run_id
                                 )
                               ))
                             )
                           )
                        ORDER BY runs.sequence ASC
                        LIMIT ?
                        """,  # noqa: S608 - VALUES placeholders are generated internally
                        parameters,
                    )
                )
            selected = rows[:limit]
            return RunnableProcedureRunPage(
                items=tuple(_run(row) for row in selected),
                has_more=len(rows) > limit,
            )
        except (sqlite3.Error, ValidationError) as error:
            raise AutomationStoreError("failed to list runnable procedures") from error

    def insert_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        run: ProcedureRun,
    ) -> None:
        try:
            connection.execute(
                """
                INSERT INTO procedure_runs(
                    procedure_run_id, definition_id, definition_version,
                    definition_fingerprint, request_key, intent_hash, revision,
                    state, closure_status, closed_at, created_at, updated_at,
                    run_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.procedure_run_id,
                    run.definition.id,
                    run.definition.version,
                    run.definition.fingerprint,
                    run.request_key,
                    run.intent_hash,
                    run.revision,
                    run.state,
                    None if run.closure is None else run.closure.status,
                    (
                        None
                        if run.closure is None
                        else _timestamp(run.closure.closed_at)
                    ),
                    _timestamp(run.created_at),
                    _timestamp(run.updated_at),
                    run.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise AutomationConflict(
                "procedure run id or request key is already durable"
            ) from error

    def replace_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        run: ProcedureRun,
        *,
        expected_revision: int,
    ) -> None:
        current = self.read_run_in_transaction(connection, run.procedure_run_id)
        if current.revision != expected_revision:
            raise AutomationConflict("procedure run revision changed")
        if run.revision != expected_revision + 1:
            raise AutomationConflict("procedure run revision must advance exactly once")
        if (
            run.request_key != current.request_key
            or run.definition != current.definition
            or run.intent != current.intent
            or run.intent_hash != current.intent_hash
            or run.created_at != current.created_at
        ):
            raise AutomationConflict("procedure run identity is immutable")
        updated = connection.execute(
            """
            UPDATE procedure_runs SET
                revision = ?, state = ?, closure_status = ?, closed_at = ?,
                updated_at = ?, run_json = ?
            WHERE procedure_run_id = ? AND revision = ?
            """,
            (
                run.revision,
                run.state,
                None if run.closure is None else run.closure.status,
                (None if run.closure is None else _timestamp(run.closure.closed_at)),
                _timestamp(run.updated_at),
                run.model_dump_json(),
                run.procedure_run_id,
                expected_revision,
            ),
        ).rowcount
        if updated != 1:
            raise AutomationConflict("procedure run revision changed")

    def list_step_attempts(
        self,
        procedure_run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ProcedureStepAttemptPage:
        if not 1 <= limit <= 500:
            raise ValueError("procedure step page size must be between 1 and 500")
        if before is not None and before < 1:
            raise ValueError("procedure step cursor must be positive")
        with self.sqlite.read_connection() as connection:
            self.read_run_in_transaction(connection, procedure_run_id)
            rows = _all(
                connection.execute(
                    """
                    SELECT sequence, attempt_json FROM procedure_step_attempts
                    WHERE procedure_run_id = ?
                      AND (? IS NULL OR sequence < ?)
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (procedure_run_id, before, before, limit + 1),
                )
            )
        try:
            selected = rows[:limit]
            return ProcedureStepAttemptPage(
                items=tuple(_attempt(row) for row in selected),
                next_cursor=(
                    _integer(selected[-1], "sequence") if len(rows) > limit else None
                ),
            )
        except ValidationError as error:
            raise AutomationStoreError(
                f"invalid procedure step state: {procedure_run_id}"
            ) from error

    def latest_step_attempt_in_transaction(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
        step_key: str,
    ) -> ProcedureStepAttempt | None:
        row = _one(
            connection.execute(
                """
                SELECT attempt_json FROM procedure_step_attempts
                WHERE procedure_run_id = ? AND step_key = ?
                ORDER BY attempt DESC
                LIMIT 1
                """,
                (procedure_run_id, step_key),
            )
        )
        return None if row is None else _attempt(row)

    def running_step_attempt_in_transaction(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
    ) -> ProcedureStepAttempt | None:
        row = _one(
            connection.execute(
                """
                SELECT attempt_json FROM procedure_step_attempts
                WHERE procedure_run_id = ? AND state = 'running'
                """,
                (procedure_run_id,),
            )
        )
        return None if row is None else _attempt(row)

    def insert_step_attempt_in_transaction(
        self,
        connection: sqlite3.Connection,
        attempt: ProcedureStepAttempt,
    ) -> None:
        try:
            connection.execute(
                """
                INSERT INTO procedure_step_attempts(
                    procedure_run_id, step_key, attempt, operation, intent_hash,
                    revision, state, started_at, updated_at, attempt_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.procedure_run_id,
                    attempt.step_key,
                    attempt.attempt,
                    attempt.operation,
                    attempt.intent_hash,
                    attempt.revision,
                    attempt.state,
                    _timestamp(attempt.started_at),
                    _timestamp(attempt.updated_at),
                    attempt.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise AutomationConflict(
                "procedure step attempt conflicts with durable state"
            ) from error

    def replace_step_attempt_in_transaction(
        self,
        connection: sqlite3.Connection,
        attempt: ProcedureStepAttempt,
        *,
        expected_revision: int,
    ) -> None:
        current = self.read_step_attempt_in_transaction(
            connection,
            attempt.procedure_run_id,
            attempt.step_key,
            attempt.attempt,
        )
        if current.revision != expected_revision:
            raise AutomationConflict("procedure step revision changed")
        if attempt.revision != expected_revision + 1:
            raise AutomationConflict(
                "procedure step revision must advance exactly once"
            )
        if (
            attempt.procedure_run_id != current.procedure_run_id
            or attempt.step_key != current.step_key
            or attempt.attempt != current.attempt
            or attempt.operation != current.operation
            or attempt.intent_hash != current.intent_hash
            or attempt.inputs != current.inputs
            or attempt.started_at != current.started_at
        ):
            raise AutomationConflict("procedure step identity is immutable")
        updated = connection.execute(
            """
            UPDATE procedure_step_attempts SET
                revision = ?, state = ?, updated_at = ?, attempt_json = ?
            WHERE procedure_run_id = ? AND step_key = ? AND attempt = ?
              AND revision = ?
            """,
            (
                attempt.revision,
                attempt.state,
                _timestamp(attempt.updated_at),
                attempt.model_dump_json(),
                attempt.procedure_run_id,
                attempt.step_key,
                attempt.attempt,
                expected_revision,
            ),
        ).rowcount
        if updated != 1:
            raise AutomationConflict("procedure step revision changed")

    def read_lease_in_transaction(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
    ) -> ProcedureLeaseRecord | None:
        row = _one(
            connection.execute(
                "SELECT * FROM procedure_leases WHERE procedure_run_id = ?",
                (procedure_run_id,),
            )
        )
        return None if row is None else _lease(row)

    def put_lease_in_transaction(
        self,
        connection: sqlite3.Connection,
        lease: ProcedureLeaseRecord,
    ) -> None:
        try:
            connection.execute(
                """
                INSERT INTO procedure_leases(
                    procedure_run_id, worker_id, token,
                    acquired_at, renewed_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(procedure_run_id) DO UPDATE SET
                    worker_id = excluded.worker_id,
                    token = excluded.token,
                    acquired_at = excluded.acquired_at,
                    renewed_at = excluded.renewed_at,
                    expires_at = excluded.expires_at
                """,
                (
                    lease.procedure_run_id,
                    lease.worker_id,
                    lease.token,
                    _timestamp(lease.acquired_at),
                    _timestamp(lease.renewed_at),
                    _timestamp(lease.expires_at),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise AutomationConflict("procedure lease token already exists") from error

    def delete_lease_in_transaction(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
        *,
        token: str | None = None,
    ) -> None:
        if token is None:
            connection.execute(
                "DELETE FROM procedure_leases WHERE procedure_run_id = ?",
                (procedure_run_id,),
            )
            return
        deleted = connection.execute(
            """
            DELETE FROM procedure_leases
            WHERE procedure_run_id = ? AND token = ?
            """,
            (procedure_run_id, token),
        ).rowcount
        if deleted != 1:
            raise AutomationConflict("procedure lease is not held")

    def read_step_attempt_in_transaction(
        self,
        connection: sqlite3.Connection,
        procedure_run_id: str,
        step_key: str,
        attempt: int,
    ) -> ProcedureStepAttempt:
        row = _one(
            connection.execute(
                """
                SELECT attempt_json FROM procedure_step_attempts
                WHERE procedure_run_id = ? AND step_key = ? AND attempt = ?
                """,
                (procedure_run_id, step_key, attempt),
            )
        )
        if row is None:
            raise AutomationNotFound(
                "procedure step attempt was not found: "
                f"{procedure_run_id}/{step_key}/{attempt}"
            )
        return _attempt(row)


def _run(row: sqlite3.Row) -> ProcedureRun:
    try:
        return ProcedureRun.model_validate_json(_text(row, "run_json"))
    except ValidationError as error:
        raise AutomationStoreError("invalid durable procedure run") from error


def _attempt(row: sqlite3.Row) -> ProcedureStepAttempt:
    try:
        return ProcedureStepAttempt.model_validate_json(_text(row, "attempt_json"))
    except ValidationError as error:
        raise AutomationStoreError("invalid durable procedure step attempt") from error


def _lease(row: sqlite3.Row) -> ProcedureLeaseRecord:
    return ProcedureLeaseRecord(
        procedure_run_id=_text(row, "procedure_run_id"),
        worker_id=_text(row, "worker_id"),
        token=_text(row, "token"),
        acquired_at=datetime.fromisoformat(_text(row, "acquired_at")),
        renewed_at=datetime.fromisoformat(_text(row, "renewed_at")),
        expires_at=datetime.fromisoformat(_text(row, "expires_at")),
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("automation timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _all(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


def _text(row: sqlite3.Row, key: str) -> str:
    return cast("str", row[key])


def _integer(row: sqlite3.Row, key: str) -> int:
    return cast("int", row[key])


__all__ = [
    "AutomationConflict",
    "AutomationNotFound",
    "AutomationStoreError",
    "ProcedureLeaseRecord",
    "ProcedureRunPage",
    "ProcedureStepAttemptPage",
    "RunnableProcedureRunPage",
    "SQLiteAutomationStore",
]
