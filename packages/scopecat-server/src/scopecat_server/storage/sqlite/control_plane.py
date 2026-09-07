"""SQLite control plane for a daemon-owned project."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter
from scopecat.control.models import (
    ControlRun,
    ControlRunState,
    DurableEvent,
    DurableEventInput,
    EventPage,
    ExecutorLease,
    InstrumentSession,
    InventoryMigrationBlocker,
    ResourceClaim,
    ResourceKey,
    RunAdmissionRecord,
    RunExecutionSegment,
    RunExecutionSegmentCertainty,
    RunExecutionSegmentPage,
    RunExecutionSegmentResult,
    RunPage,
)

from scopecat_server.storage.sqlite.connection import SQLiteDatabase

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_STRING_TUPLE = TypeAdapter(tuple[str, ...])


class ControlPlaneError(RuntimeError):
    """Base failure from the SQLite control plane."""


class ControlPlaneNotFound(ControlPlaneError):
    """A requested control-plane record does not exist."""


class ControlPlaneConflict(ControlPlaneError):
    """A compare-and-set precondition or lifecycle invariant failed."""


class ExecutorLeaseNotHeld(ControlPlaneConflict):
    """The executor fencing token is absent, stale, or expired."""


class RunResourcesBusy(ControlPlaneConflict):
    """Another execution or instrument session owns a required resource."""


class InstrumentSessionNotActive(ControlPlaneConflict):
    """The daemon-owned interactive session is not active."""


class SQLiteControlPlane:
    """Transactional scheduler state and globally ordered durable events."""

    def __init__(self, database: SQLiteDatabase):
        self.sqlite = database
        self.path = database.path

    def admit_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        admission: RunAdmissionRecord,
        *,
        expected_config_generation: int,
    ) -> ControlRun:
        """Publish control admission through an existing daemon transaction."""

        retry_row = _one(
            connection.execute(
                """
                SELECT * FROM scheduler_runs
                WHERE submission_id = ?
                """,
                (admission.submission_id,),
            )
        )
        if retry_row is not None:
            retry = _run(retry_row)
            if admission.is_retry_of(retry.admission):
                return retry
            raise ControlPlaneConflict(
                "submission id is already admitted with different content"
            )
        self._require_config_generation(connection, expected_config_generation)
        admitted_at = _timestamp(admission.admitted_at)
        try:
            cursor = connection.execute(
                """
                INSERT INTO scheduler_runs(
                    submission_id, run_id, state, updated_at,
                    admission_json, attention_reason, cancellation_requested_at
                )
                VALUES (?, ?, 'queued', ?, ?, NULL, NULL)
                """,
                (
                    admission.submission_id,
                    admission.run_id,
                    admitted_at,
                    admission.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError as error:
            existing = self._find_admission_conflict(connection, admission)
            if existing is not None:
                return existing
            raise ControlPlaneConflict(
                "run id or submission id is already admitted"
            ) from error
        connection.executemany(
            """
            INSERT INTO run_resource_claims(
                run_id, resource_kind, resource_id
            )
            VALUES (?, ?, ?)
            """,
            [
                (admission.run_id, resource.kind, resource.id)
                for resource in admission.resource_claims
            ],
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=admission.run_id,
                kind="run_admitted",
                payload={
                    "experiment_id": admission.experiment_id,
                    "submission_id": admission.submission_id,
                },
                occurred_at=admission.admitted_at,
            ),
        )
        row = _one(
            connection.execute(
                "SELECT * FROM scheduler_runs WHERE sequence = ?",
                (cast("int", cursor.lastrowid),),
            )
        )
        assert row is not None
        return _run(row)

    def get_run(self, run_id: str) -> ControlRun:
        with self.sqlite.read_connection() as connection:
            return self.get_run_in_transaction(connection, run_id)

    def get_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> ControlRun:
        """Read a run through an existing daemon transaction."""

        return _run(self._require_run(connection, run_id))

    def find_run_by_submission_id(self, submission_id: str) -> ControlRun | None:
        """Return an admitted submission when it already exists."""

        with self.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    """
                    SELECT * FROM scheduler_runs
                    WHERE submission_id = ?
                    """,
                    (submission_id,),
                )
            )
        return None if row is None else _run(row)

    def list_runs(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ControlRunState | None = None,
        sample_id: str | None = None,
    ) -> RunPage:
        """Return newest runs first, continuing toward older admissions."""

        with self.sqlite.read_connection() as connection:
            return self.list_runs_in_transaction(
                connection,
                limit=limit,
                before=before,
                state=state,
                sample_id=sample_id,
            )

    def list_runs_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ControlRunState | None = None,
        sample_id: str | None = None,
    ) -> RunPage:
        """Read one newest-first page through an existing SQLite snapshot."""

        _page_size(limit)
        clauses: list[str] = []
        parameters: list[str | int] = []
        if before is not None:
            clauses.append("scheduler_runs.sequence < ?")
            parameters.append(before)
        if state is not None:
            clauses.append("scheduler_runs.state = ?")
            parameters.append(state)
        if sample_id is not None:
            clauses.append(
                "EXISTS ("
                "SELECT 1 FROM run_sample_bindings AS sample_binding "
                "WHERE sample_binding.run_id = scheduler_runs.run_id "
                "AND sample_binding.sample_id = ?"
                ")"
            )
            parameters.append(sample_id)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        parameters.append(limit + 1)
        rows = _all(
            connection.execute(
                f"""
                SELECT scheduler_runs.*
                FROM scheduler_runs
                {where}
                ORDER BY scheduler_runs.sequence DESC
                LIMIT ?
                """,  # noqa: S608 - fragments are fixed internal clauses
                parameters,
            )
        )
        items = tuple(_run(row) for row in rows[:limit])
        next_cursor = None if len(rows) <= limit else items[-1].sequence
        return RunPage(items=items, next_cursor=next_cursor)

    def list_run_execution_segments(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunExecutionSegmentPage:
        """Return newest execution ownership intervals for one run."""

        _page_size(limit)
        with self.sqlite.read_connection() as connection:
            self._require_run(connection, run_id)
            if before is None:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM run_execution_segments
                        WHERE run_id = ?
                        ORDER BY sequence DESC
                        LIMIT ?
                        """,
                        (run_id, limit + 1),
                    )
                )
            else:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM run_execution_segments
                        WHERE run_id = ? AND sequence < ?
                        ORDER BY sequence DESC
                        LIMIT ?
                        """,
                        (run_id, before, limit + 1),
                    )
                )
        items = tuple(_execution_segment(row) for row in rows[:limit])
        next_cursor = None if len(rows) <= limit else items[-1].sequence
        return RunExecutionSegmentPage(items=items, next_cursor=next_cursor)

    def close_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        executor_token: str | None = None,
        at: datetime | None = None,
    ) -> ControlRun:
        """Close scheduling after the terminal outcome is committed."""

        current = _run(self._require_run(connection, run_id))
        closed_at = at or datetime.now(tz=UTC)
        if current.state == "leased":
            if executor_token is None:
                raise ExecutorLeaseNotHeld(
                    "closing a leased run requires its executor token"
                )
            lease = self._live_executor(
                connection,
                executor_token,
                at=closed_at,
                run_id=run_id,
            )
            segment = self._execution_segment(connection, lease.segment_id)
            if segment.ended_at is None:
                raise ControlPlaneConflict(
                    "closing a leased run requires its execution segment to end"
                )
        elif current.state == "attention_required":
            if executor_token is not None:
                raise ControlPlaneConflict(
                    "attention-required runs no longer have an executor"
                )
        elif current.state == "queued":
            if executor_token is not None:
                raise ControlPlaneConflict("queued runs do not have an executor")
            if current.cancellation_requested_at is None:
                raise ControlPlaneConflict(
                    "queued runs can close only after cancellation is requested"
                )
        else:
            raise ControlPlaneConflict(
                "only queued, leased, or attention-required runs can close, "
                f"got {current.state}"
            )

        updated = self._update_scheduler_state_in_transaction(
            connection,
            current,
            state="closed",
            at=closed_at,
        )
        released = connection.execute(
            """
            DELETE FROM resource_claims
            WHERE owner_kind = 'run' AND owner_id = ?
            """,
            (run_id,),
        ).rowcount
        connection.execute("DELETE FROM executor_leases WHERE run_id = ?", (run_id,))
        if released:
            self._insert_event(
                connection,
                DurableEventInput(
                    run_id=run_id,
                    kind="resources_released",
                    payload={"count": released},
                    occurred_at=closed_at,
                ),
            )
        return updated

    def reject_queued_run_in_transaction(
        self, connection: sqlite3.Connection, run_id: str, *, at: datetime
    ) -> ControlRun:
        """Close an admission rejected before ownership, after its failed outcome."""
        current = _run(self._require_run(connection, run_id))
        if current.state != "queued":
            raise ControlPlaneConflict("execution rejection requires a queued run")
        return self._update_scheduler_state_in_transaction(
            connection, current, state="closed", at=at
        )

    def request_run_cancellation_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        at: datetime | None = None,
    ) -> ControlRun:
        """Persist one idempotent operator cancellation request."""

        current = _run(self._require_run(connection, run_id))
        if current.cancellation_requested_at is not None:
            return current
        requested_at = at or datetime.now(tz=UTC)
        updated = current.model_copy(
            update={
                "updated_at": requested_at,
                "cancellation_requested_at": requested_at,
            }
        )
        connection.execute(
            """
            UPDATE scheduler_runs SET
                updated_at = ?, cancellation_requested_at = ?
            WHERE run_id = ?
            """,
            (_timestamp(requested_at), _timestamp(requested_at), run_id),
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=run_id,
                kind="run_cancellation_requested",
                payload={},
                occurred_at=requested_at,
            ),
        )
        return updated

    def _update_scheduler_state_in_transaction(
        self,
        connection: sqlite3.Connection,
        current: ControlRun,
        *,
        state: ControlRunState,
        at: datetime,
        attention_reason: str | None = None,
    ) -> ControlRun:
        updated = ControlRun.model_validate(
            {
                **current.model_dump(),
                "state": state,
                "updated_at": at,
                "attention_reason": attention_reason,
            }
        )
        connection.execute(
            """
            UPDATE scheduler_runs SET
                state = ?, updated_at = ?, attention_reason = ?
            WHERE run_id = ?
            """,
            (state, _timestamp(at), attention_reason, current.run_id),
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=current.run_id,
                kind="run_state_changed",
                payload={"from": current.state, "to": state},
                occurred_at=at,
            ),
        )
        return updated

    def append_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        event: DurableEventInput,
    ) -> DurableEvent:
        """Append a replay event inside the caller's write transaction."""

        if event.run_id is not None:
            self._require_run(connection, event.run_id)
        return self._insert_event(connection, event)

    def list_events(
        self,
        *,
        limit: int = 100,
        after: int | None = None,
        run_id: str | None = None,
        latest: bool = False,
    ) -> EventPage:
        """Return a page in the single global replay order."""

        _page_size(limit)
        if latest and after is not None:
            raise ValueError("latest event snapshots do not accept an after cursor")
        cursor = after or 0
        with self.sqlite.read_connection() as connection:
            if latest and run_id is None:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM durable_events
                        ORDER BY event_id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    )
                )
            elif latest:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM durable_events
                        WHERE run_id = ?
                        ORDER BY event_id DESC
                        LIMIT ?
                        """,
                        (run_id, limit),
                    )
                )
            elif run_id is None:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM durable_events
                        WHERE event_id > ?
                        ORDER BY event_id
                        LIMIT ?
                        """,
                        (cursor, limit + 1),
                    )
                )
            else:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM durable_events
                        WHERE event_id > ? AND run_id = ?
                        ORDER BY event_id
                        LIMIT ?
                        """,
                        (cursor, run_id, limit + 1),
                    )
                )
        if latest:
            rows.reverse()
        items = tuple(_event(row) for row in rows[:limit])
        next_cursor = None if latest or len(rows) <= limit else items[-1].event_id
        return EventPage(items=items, next_cursor=next_cursor)

    def start_execution_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        executor_id: str,
        ttl: timedelta,
        at: datetime | None = None,
    ) -> ExecutorLease:
        """Lease every canonical claim and enter the leased scheduler state."""

        _ttl(ttl)
        started_at = at or datetime.now(tz=UTC)
        self._expire_one(connection, run_id, started_at)
        run = _run(self._require_run(connection, run_id))
        if run.state != "queued":
            raise ControlPlaneConflict(
                f"executor can only start a queued run, got {run.state}"
            )
        run_claims = self._run_claims(connection, run_id)
        for resource in run_claims:
            row = _one(
                connection.execute(
                    """
                    SELECT * FROM resource_claims
                    WHERE resource_kind = ? AND resource_id = ?
                    """,
                    (resource.kind, resource.id),
                )
            )
            if row is not None:
                raise RunResourcesBusy("run resources are busy")

        segment_id = f"segment-{uuid4().hex}"
        ordinal_row = _one(
            connection.execute(
                """
                SELECT COALESCE(MAX(ordinal), -1) + 1 AS ordinal
                FROM run_execution_segments
                WHERE run_id = ?
                """,
                (run_id,),
            )
        )
        assert ordinal_row is not None
        ordinal = _integer(ordinal_row, "ordinal")
        start_point_count = self._completed_point_count(connection, run_id)
        connection.execute(
            """
            INSERT INTO run_execution_segments(
                segment_id, run_id, ordinal, executor_id,
                run_contract_fingerprint, started_at, start_point_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment_id,
                run_id,
                ordinal,
                executor_id,
                run.admission.submission_content_hash,
                _timestamp(started_at),
                start_point_count,
            ),
        )
        token = uuid4().hex
        expires_at = started_at + ttl
        connection.execute(
            """
            INSERT INTO executor_leases(
                run_id, segment_id, executor_id, token,
                acquired_at, renewed_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                segment_id,
                executor_id,
                token,
                _timestamp(started_at),
                _timestamp(started_at),
                _timestamp(expires_at),
            ),
        )
        connection.executemany(
            """
            INSERT INTO resource_claims(
                resource_kind, resource_id, owner_kind, owner_id,
                status, acquired_at
            )
            VALUES (?, ?, 'run', ?, 'active', ?)
            """,
            [
                (
                    resource.kind,
                    resource.id,
                    run_id,
                    _timestamp(started_at),
                )
                for resource in run_claims
            ],
        )
        lease = ExecutorLease(
            segment_id=segment_id,
            run_id=run_id,
            executor_id=executor_id,
            token=token,
            acquired_at=started_at,
            renewed_at=started_at,
            expires_at=expires_at,
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=run_id,
                kind="executor_lease_granted",
                payload={
                    "segment_id": segment_id,
                    "executor_id": executor_id,
                    "expires_at": _timestamp(expires_at),
                },
                occurred_at=started_at,
            ),
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=run_id,
                kind="resources_claimed",
                payload={"count": len(run_claims)},
                occurred_at=started_at,
            ),
        )
        self._update_scheduler_state_in_transaction(
            connection,
            run,
            state="leased",
            at=started_at,
        )
        return lease

    def finish_execution_segment_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        token: str,
        result: RunExecutionSegmentResult,
        certainty: RunExecutionSegmentCertainty,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> RunExecutionSegment:
        """Close the exact segment still owned by one live executor lease."""

        ended_at = at or datetime.now(tz=UTC)
        lease = self._live_executor(
            connection,
            token,
            at=ended_at,
            run_id=run_id,
        )
        return self._finish_execution_segment(
            connection,
            lease,
            result=result,
            certainty=certainty,
            reason=reason,
            at=ended_at,
        )

    def renew_executor_lease(
        self,
        run_id: str,
        token: str,
        *,
        ttl: timedelta,
        at: datetime | None = None,
    ) -> ExecutorLease:
        """Renew the executor fencing lease."""

        _ttl(ttl)
        renewed_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            lease = self._live_executor(
                connection,
                token,
                at=renewed_at,
                run_id=run_id,
            )
            expires_at = renewed_at + ttl
            connection.execute(
                """
                UPDATE executor_leases SET renewed_at = ?, expires_at = ?
                WHERE token = ?
                """,
                (_timestamp(renewed_at), _timestamp(expires_at), token),
            )
            row = _one(
                connection.execute(
                    "SELECT * FROM executor_leases WHERE token = ?", (lease.token,)
                )
            )
            assert row is not None
            return _executor(row)

    def validate_executor_lease(
        self,
        run_id: str,
        *,
        token: str,
        at: datetime | None = None,
    ) -> ExecutorLease:
        """Resolve the exact live fencing token carried by a wire command."""

        checked_at = at or datetime.now(tz=UTC)
        with self.sqlite.read_connection() as connection:
            lease = self._live_executor(
                connection,
                token,
                at=checked_at,
                run_id=run_id,
            )
        return lease

    @contextmanager
    def fenced_transaction(
        self,
        run_id: str,
        *,
        token: str,
        at: datetime | None = None,
    ) -> Generator[sqlite3.Connection]:
        """Serialize lease validation with every durable executor effect."""

        with self.write_transaction() as connection:
            checked_at = at or datetime.now(tz=UTC)
            self._live_executor(
                connection,
                token,
                at=checked_at,
                run_id=run_id,
            )
            run = _run(self._require_run(connection, run_id))
            if run.state != "leased":
                raise ControlPlaneConflict(
                    f"executor effects require a leased run, got {run.state}"
                )
            yield connection

    def executor_lease_for_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> ExecutorLease | None:
        """Read the current executor lease through an existing transaction."""

        row = _one(
            connection.execute(
                "SELECT * FROM executor_leases WHERE run_id = ?",
                (run_id,),
            )
        )
        return None if row is None else _executor(row)

    def list_resource_claims_in_transaction(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[ResourceClaim, ...]:
        """Read resource claims through an existing SQLite snapshot."""

        rows = _all(
            connection.execute(
                """
                SELECT * FROM resource_claims
                ORDER BY resource_kind, resource_id
                """
            )
        )
        return tuple(_resource_claim(row) for row in rows)

    def inventory_migration_blockers_in_transaction(
        self,
        connection: sqlite3.Connection,
        affected_keys: Collection[ResourceKey],
    ) -> tuple[InventoryMigrationBlocker, ...]:
        """Return owners pinning resource identities in the current transaction.

        Run reservations are read separately because queued runs do not yet have
        live resource claims.
        """

        identities = tuple(sorted({(key.kind, key.id) for key in affected_keys}))
        if not identities:
            return ()
        affected_json = json.dumps(
            identities,
            allow_nan=False,
            separators=(",", ":"),
        )
        rows = _all(
            connection.execute(
                """
                WITH affected AS (
                    SELECT
                        json_extract(value, '$[0]') AS resource_kind,
                        json_extract(value, '$[1]') AS resource_id
                    FROM json_each(?)
                ),
                blocker_rows(
                    resource_kind,
                    resource_id,
                    owner_kind,
                    owner_id,
                    state,
                    source_priority
                ) AS (
                    SELECT
                        reserved.resource_kind,
                        reserved.resource_id,
                        'run',
                        run.run_id,
                        run.state,
                        0
                    FROM scheduler_runs AS run
                    JOIN run_resource_claims AS reserved
                        ON reserved.run_id = run.run_id
                    JOIN affected
                        ON affected.resource_kind = reserved.resource_kind
                        AND affected.resource_id = reserved.resource_id
                    WHERE run.state <> 'closed'
                    UNION ALL
                    SELECT
                        claim.resource_kind,
                        claim.resource_id,
                        claim.owner_kind,
                        claim.owner_id,
                        claim.status,
                        1
                    FROM resource_claims AS claim
                    JOIN affected
                        ON affected.resource_kind = claim.resource_kind
                        AND affected.resource_id = claim.resource_id
                )
                SELECT
                    resource_kind,
                    resource_id,
                    owner_kind,
                    owner_id,
                    state,
                    source_priority
                FROM blocker_rows
                ORDER BY
                    resource_kind,
                    resource_id,
                    owner_kind,
                    owner_id,
                    source_priority
                """,
                (affected_json,),
            )
        )
        blockers: dict[
            tuple[str, str, str, str],
            InventoryMigrationBlocker,
        ] = {}
        for row in rows:
            blocker = InventoryMigrationBlocker.model_validate(
                {
                    "key": {
                        "kind": _text(row, "resource_kind"),
                        "id": _text(row, "resource_id"),
                    },
                    "owner_kind": _text(row, "owner_kind"),
                    "owner_id": _text(row, "owner_id"),
                    "state": _text(row, "state"),
                }
            )
            identity = (
                blocker.key.kind,
                blocker.key.id,
                blocker.owner_kind,
                blocker.owner_id,
            )
            blockers.setdefault(identity, blocker)
        return tuple(blockers.values())

    def open_instrument_session(
        self,
        *,
        operation_id: str,
        actor: str,
        config_entry_id: str,
        config_content_hash: str,
        instrument_ids: tuple[str, ...],
        exclusivity_keys: tuple[str, ...],
        ttl: timedelta,
        expected_config_generation: int | None,
        at: datetime | None = None,
    ) -> InstrumentSession:
        """Atomically reserve instruments for one direct-interaction session."""

        _ttl(ttl)
        if not operation_id:
            raise ValueError("instrument session operation id must be non-empty")
        if not actor:
            raise ValueError("instrument session actor must be non-empty")
        if not config_entry_id:
            raise ValueError("instrument session config entry id must be non-empty")
        if not config_content_hash:
            raise ValueError("instrument session config hash must be non-empty")
        if not instrument_ids:
            raise ValueError("instrument session requires at least one instrument")
        if any(not instrument_id for instrument_id in instrument_ids):
            raise ValueError("instrument session ids must be non-empty")
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError("instrument session ids must be unique")
        if len(exclusivity_keys) != len(instrument_ids):
            raise ValueError(
                "instrument session ids and exclusivity keys must have equal length"
            )
        if any(not exclusivity_key for exclusivity_key in exclusivity_keys):
            raise ValueError("instrument session exclusivity keys must be non-empty")
        if len(exclusivity_keys) != len(set(exclusivity_keys)):
            raise ValueError("instrument session exclusivity keys must be unique")

        started_at = at or datetime.now(tz=UTC)
        expires_at = started_at + ttl
        session_id = f"instrument-{uuid4().hex}"
        resources = tuple(
            ResourceKey(kind="instrument", id=exclusivity_key)
            for exclusivity_key in exclusivity_keys
        )
        with self.write_transaction() as connection:
            retry_row = _one(
                connection.execute(
                    """
                    SELECT * FROM instrument_sessions
                    WHERE open_operation_id = ?
                    """,
                    (operation_id,),
                )
            )
            if retry_row is not None:
                retry = _instrument_session(retry_row)
                # Config identity is server-resolved output from the first command.
                if retry.actor != actor or retry.instrument_ids != instrument_ids:
                    raise ControlPlaneConflict(
                        "instrument session operation id has different content"
                    )
                if retry.state != "active":
                    raise ControlPlaneConflict(
                        "instrument session open retry is no longer active"
                    )
                if retry.expires_at <= started_at:
                    raise ControlPlaneConflict(
                        "instrument session open retry has expired"
                    )
                return retry
            if expected_config_generation is None:
                raise ValueError("new instrument session requires a config generation")
            self._require_config_generation(
                connection,
                expected_config_generation,
            )
            conflicts = tuple(
                resource
                for resource in resources
                if _one(
                    connection.execute(
                        """
                        SELECT 1 FROM resource_claims
                        WHERE resource_kind = ? AND resource_id = ?
                        """,
                        (resource.kind, resource.id),
                    )
                )
                is not None
            )
            if conflicts:
                raise ControlPlaneConflict("instrument session resources are busy")
            connection.execute(
                """
                INSERT INTO instrument_sessions(
                    session_id, open_operation_id, actor, config_entry_id,
                    config_content_hash,
                    instrument_ids_json, exclusivity_keys_json, state, acquired_at,
                    renewed_at, expires_at,
                    attention_reason, active_operation_id,
                    active_operation_kind, end_status
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?,
                    NULL, NULL, NULL, NULL
                )
                """,
                (
                    session_id,
                    operation_id,
                    actor,
                    config_entry_id,
                    config_content_hash,
                    json.dumps(
                        instrument_ids,
                        allow_nan=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        exclusivity_keys,
                        allow_nan=False,
                        separators=(",", ":"),
                    ),
                    _timestamp(started_at),
                    _timestamp(started_at),
                    _timestamp(expires_at),
                ),
            )
            connection.executemany(
                """
                INSERT INTO resource_claims(
                    resource_kind, resource_id, owner_kind, owner_id,
                    status, acquired_at
                )
                VALUES (?, ?, 'instrument_session', ?, 'active', ?)
                """,
                [
                    (
                        resource.kind,
                        resource.id,
                        session_id,
                        _timestamp(started_at),
                    )
                    for resource in resources
                ],
            )
            self._insert_event(
                connection,
                DurableEventInput(
                    kind="instrument_session_opened",
                    payload={
                        "session_id": session_id,
                        "operation_id": operation_id,
                        "actor": actor,
                        "instrument_ids": list(instrument_ids),
                        "config_entry_id": config_entry_id,
                    },
                    occurred_at=started_at,
                ),
            )
            row = _one(
                connection.execute(
                    "SELECT * FROM instrument_sessions WHERE session_id = ?",
                    (session_id,),
                )
            )
            assert row is not None
            return _instrument_session(row)

    def get_instrument_session_by_open_operation_id(
        self,
        operation_id: str,
    ) -> InstrumentSession:
        """Read the durable result of an instrument-session open command."""

        if not operation_id:
            raise ValueError("instrument session operation id must be non-empty")
        with self.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    """
                    SELECT * FROM instrument_sessions
                    WHERE open_operation_id = ?
                    """,
                    (operation_id,),
                )
            )
        if row is None:
            raise ControlPlaneNotFound(
                f"instrument session open operation was not found: {operation_id}"
            )
        return _instrument_session(row)

    def get_instrument_session(self, session_id: str) -> InstrumentSession:
        with self.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT * FROM instrument_sessions WHERE session_id = ?",
                    (session_id,),
                )
            )
        if row is None:
            raise ControlPlaneNotFound(
                f"instrument session was not found: {session_id}"
            )
        return _instrument_session(row)

    def list_instrument_sessions(
        self,
        *,
        state: str | None = None,
    ) -> tuple[InstrumentSession, ...]:
        with self.sqlite.read_connection() as connection:
            if state is None:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM instrument_sessions
                        ORDER BY acquired_at, session_id
                        """
                    )
                )
            else:
                rows = _all(
                    connection.execute(
                        """
                        SELECT * FROM instrument_sessions
                        WHERE state = ?
                        ORDER BY acquired_at, session_id
                        """,
                        (state,),
                    )
                )
        return tuple(_instrument_session(row) for row in rows)

    def renew_instrument_session(
        self,
        session_id: str,
        *,
        ttl: timedelta,
        at: datetime | None = None,
    ) -> InstrumentSession:
        """Renew one live direct-session lease without adding an event."""

        _ttl(ttl)
        renewed_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            self._live_instrument_session(
                connection,
                session_id=session_id,
                at=renewed_at,
            )
            connection.execute(
                """
                UPDATE instrument_sessions SET renewed_at = ?, expires_at = ?
                WHERE session_id = ? AND state = 'active'
                """,
                (
                    _timestamp(renewed_at),
                    _timestamp(renewed_at + ttl),
                    session_id,
                ),
            )
            return self._instrument_session_row(connection, session_id)

    def expired_instrument_sessions(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[InstrumentSession, ...]:
        """List active direct sessions whose leases have elapsed."""

        checked_at = at or datetime.now(tz=UTC)
        with self.sqlite.read_connection() as connection:
            rows = _all(
                connection.execute(
                    """
                    SELECT * FROM instrument_sessions
                    WHERE state = 'active' AND expires_at <= ?
                    ORDER BY expires_at, session_id
                    """,
                    (_timestamp(checked_at),),
                )
            )
        return tuple(_instrument_session(row) for row in rows)

    def validate_instrument_session(
        self,
        session_id: str,
        *,
        at: datetime | None = None,
    ) -> InstrumentSession:
        checked_at = at or datetime.now(tz=UTC)
        with self.sqlite.read_connection() as connection:
            return self._live_instrument_session(
                connection,
                session_id=session_id,
                at=checked_at,
            )

    def start_instrument_operation(
        self,
        session_id: str,
        *,
        instrument_id: str,
        operation_id: str,
        kind: str,
        at: datetime | None = None,
    ) -> InstrumentSession:
        if kind not in {"apply", "invoke", "collect"}:
            raise ValueError(f"unsupported instrument operation kind: {kind}")
        if not operation_id:
            raise ValueError("instrument operation id must be non-empty")
        started_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            session = self._live_instrument_session(
                connection,
                session_id=session_id,
                at=started_at,
            )
            if session.active_operation_id is not None:
                raise ControlPlaneConflict(
                    "instrument session already has an active operation"
                )
            connection.execute(
                """
                UPDATE instrument_sessions SET
                    active_operation_id = ?, active_operation_kind = ?
                WHERE session_id = ? AND state = 'active'
                """,
                (operation_id, kind, session_id),
            )
            self._insert_event(
                connection,
                DurableEventInput(
                    kind=f"instrument_{kind}_started",
                    payload={
                        "session_id": session_id,
                        "instrument_id": instrument_id,
                        "operation_id": operation_id,
                        "actor": session.actor,
                    },
                    occurred_at=started_at,
                ),
            )
            return self._instrument_session_row(connection, session_id)

    def finish_instrument_operation(
        self,
        session_id: str,
        *,
        instrument_id: str,
        operation_id: str,
        kind: str,
        status: str,
        at: datetime | None = None,
    ) -> InstrumentSession:
        finished_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            session = self._live_instrument_session(
                connection,
                session_id=session_id,
                at=finished_at,
            )
            if (
                session.active_operation_id != operation_id
                or session.active_operation_kind != kind
            ):
                raise ControlPlaneConflict(
                    "instrument operation finish does not match the active operation"
                )
            connection.execute(
                """
                UPDATE instrument_sessions SET
                    active_operation_id = NULL, active_operation_kind = NULL
                WHERE session_id = ? AND state = 'active'
                """,
                (session_id,),
            )
            self._insert_event(
                connection,
                DurableEventInput(
                    kind=f"instrument_{kind}_finished",
                    payload={
                        "session_id": session_id,
                        "instrument_id": instrument_id,
                        "operation_id": operation_id,
                        "actor": session.actor,
                        "status": status,
                    },
                    occurred_at=finished_at,
                ),
            )
            return self._instrument_session_row(connection, session_id)

    def close_instrument_session(
        self,
        session_id: str,
        *,
        status: str,
        at: datetime | None = None,
    ) -> InstrumentSession:
        if status not in {"closed", "aborted"}:
            raise ValueError(f"unsupported instrument session end status: {status}")
        closed_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            current = self._instrument_session_row(connection, session_id)
            if current.state == "closed":
                return current
            if current.state != "active":
                raise InstrumentSessionNotActive(
                    "instrument session requires operator attention"
                )
            if current.active_operation_id is not None:
                raise ControlPlaneConflict(
                    "instrument session cannot close during an active operation"
                )
            self._close_instrument_session_in_transaction(
                connection,
                current,
                status=status,
                at=closed_at,
                event_kind="instrument_session_closed",
            )
            return self._instrument_session_row(connection, session_id)

    def expire_instrument_session(
        self,
        session_id: str,
        *,
        at: datetime | None = None,
    ) -> InstrumentSession:
        """Close one expired idle session after its actor has been released."""

        expired_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            current = self._instrument_session_row(connection, session_id)
            if current.state == "closed":
                return current
            if current.state != "active":
                raise InstrumentSessionNotActive(
                    "instrument session requires operator attention"
                )
            if current.expires_at > expired_at:
                raise ControlPlaneConflict("instrument session lease has not expired")
            if current.active_operation_id is not None:
                raise ControlPlaneConflict(
                    "instrument session cannot expire during an active operation"
                )
            self._close_instrument_session_in_transaction(
                connection,
                current,
                status="aborted",
                at=expired_at,
                event_kind="instrument_session_lease_expired",
            )
            return self._instrument_session_row(connection, session_id)

    def mark_instrument_session_unknown(
        self,
        session_id: str,
        *,
        reason: str,
        at: datetime | None = None,
    ) -> InstrumentSession:
        if not reason:
            raise ValueError("instrument session attention reason must be non-empty")
        lost_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            current = self._instrument_session_row(connection, session_id)
            if current.state != "active":
                raise InstrumentSessionNotActive("instrument session is not active")
            self._lose_instrument_session(
                connection,
                session_id,
                lost_at,
                reason=reason,
            )
            return self._instrument_session_row(connection, session_id)

    def reconcile_instrument_sessions_after_restart(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[str, ...]:
        reconciled_at = at or datetime.now(tz=UTC)
        quarantined: list[str] = []
        with self.write_transaction() as connection:
            rows = _all(
                connection.execute(
                    """
                    SELECT * FROM instrument_sessions
                    WHERE state = 'active'
                    ORDER BY session_id
                    """
                )
            )
            for row in rows:
                session = _instrument_session(row)
                if session.active_operation_id is not None:
                    self._lose_instrument_session(
                        connection,
                        session.session_id,
                        reconciled_at,
                        reason="daemon_restarted_during_instrument_operation",
                    )
                    quarantined.append(session.session_id)
                    continue
                self._close_instrument_session_in_transaction(
                    connection,
                    session,
                    status="aborted",
                    at=reconciled_at,
                    event_kind="instrument_session_released_after_restart",
                )
        return tuple(quarantined)

    def resolve_instrument_session_attention(
        self,
        session_id: str,
        *,
        at: datetime | None = None,
    ) -> InstrumentSession:
        resolved_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            current = self._instrument_session_row(connection, session_id)
            if current.state != "attention_required":
                raise ControlPlaneConflict(
                    "only attention-required instrument sessions can be resolved"
                )
            self._close_instrument_session_in_transaction(
                connection,
                current,
                status="aborted",
                at=resolved_at,
                event_kind="instrument_session_attention_resolved",
            )
            return self._instrument_session_row(connection, session_id)

    def release_run_resources_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> int:
        """Release quarantined resources through an existing transaction."""

        run = _run(self._require_run(connection, run_id))
        if run.state not in {"attention_required", "closed"}:
            raise ControlPlaneConflict(
                "only reconciled attention-required resources may be released"
            )
        cursor = connection.execute(
            """
            DELETE FROM resource_claims
            WHERE owner_kind = 'run' AND owner_id = ?
            """,
            (run_id,),
        )
        if cursor.rowcount:
            self._insert_event(
                connection,
                DurableEventInput(
                    run_id=run_id,
                    kind="resources_released",
                    payload={"count": cursor.rowcount},
                ),
            )
        return cursor.rowcount

    def continue_run_after_attention_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        run_contract_fingerprint: str,
        at: datetime | None = None,
    ) -> tuple[ControlRun, int]:
        """Release reconciled resources and return an interrupted run to queue."""

        continued_at = at or datetime.now(tz=UTC)
        current = _run(self._require_run(connection, run_id))
        if current.state != "attention_required":
            raise ControlPlaneConflict("only an attention-required run can continue")
        if current.cancellation_requested_at is not None:
            raise ControlPlaneConflict(
                "a cancelled attention-required run cannot continue"
            )
        if run_contract_fingerprint != current.admission.submission_content_hash:
            raise ControlPlaneConflict(
                "continuation run contract does not match its admission"
            )
        released = self.release_run_resources_in_transaction(
            connection,
            run_id,
        )
        continued = self._update_scheduler_state_in_transaction(
            connection,
            current,
            state="queued",
            at=continued_at,
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=run_id,
                kind="run_continuation_authorized",
                payload={
                    "run_contract_fingerprint": run_contract_fingerprint,
                },
                occurred_at=continued_at,
            ),
        )
        return continued, released

    def expire_executor_leases(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[str, ...]:
        """Expire stale executors and quarantine resources of active runs."""

        expired_at = at or datetime.now(tz=UTC)
        with self.sqlite.read_connection() as connection:
            rows = _all(
                connection.execute(
                    """
                    SELECT run_id FROM executor_leases
                    WHERE expires_at <= ?
                    ORDER BY run_id
                    """,
                    (_timestamp(expired_at),),
                )
            )
        if not rows:
            return ()
        with self.write_transaction() as connection:
            return tuple(
                run_id
                for row in rows
                if self._expire_one(
                    connection,
                    run_id := _text(row, "run_id"),
                    expired_at,
                )
            )

    def abandon_executor_leases(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[str, ...]:
        """Fence executors from the previous daemon process immediately."""

        abandoned_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            rows = _all(
                connection.execute("SELECT run_id FROM executor_leases ORDER BY run_id")
            )
            return tuple(
                run_id
                for row in rows
                if self._expire_one(
                    connection,
                    run_id := _text(row, "run_id"),
                    abandoned_at,
                    force=True,
                    attention_reason="daemon_restarted",
                )
            )

    def mark_executor_unknown(
        self,
        run_id: str,
        *,
        token: str,
        reason: str,
        at: datetime | None = None,
    ) -> ControlRun:
        """Fence one exact executor and quarantine its run after uncertain I/O."""

        if not reason:
            raise ValueError("executor attention reason must be non-empty")
        lost_at = at or datetime.now(tz=UTC)
        with self.write_transaction() as connection:
            row = _one(
                connection.execute(
                    "SELECT * FROM executor_leases WHERE run_id = ?",
                    (run_id,),
                )
            )
            if row is None or _executor(row).token != token:
                raise ExecutorLeaseNotHeld(
                    "executor lease is absent, stale, or expired"
                )
            self._expire_one(
                connection,
                run_id,
                lost_at,
                force=True,
                attention_reason=reason,
            )
            return _run(self._require_run(connection, run_id))

    @staticmethod
    def _find_admission_conflict(
        connection: sqlite3.Connection,
        admission: RunAdmissionRecord,
    ) -> ControlRun | None:
        rows = _all(
            connection.execute(
                """
                SELECT * FROM scheduler_runs
                WHERE run_id = ? OR submission_id = ?
                ORDER BY sequence
                """,
                (admission.run_id, admission.submission_id),
            )
        )
        if len(rows) != 1:
            return None
        existing = _run(rows[0])
        return existing if admission.is_retry_of(existing.admission) else None

    @staticmethod
    def _require_config_generation(
        connection: sqlite3.Connection,
        expected_generation: int,
    ) -> None:
        row = _one(
            connection.execute(
                """
                SELECT COALESCE(MAX(generation), 0) AS generation
                FROM config_registry_activations
                """
            )
        )
        assert row is not None
        actual_generation = _integer(row, "generation")
        if actual_generation != expected_generation:
            raise ControlPlaneConflict("active configuration changed")

    def _expire_one(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        at: datetime,
        *,
        force: bool = False,
        attention_reason: str = "executor_lease_expired",
    ) -> bool:
        row = _one(
            connection.execute(
                "SELECT * FROM executor_leases WHERE run_id = ?", (run_id,)
            )
        )
        if row is None:
            return False
        lease = _executor(row)
        if not force and lease.expires_at > at:
            return False
        run = _run(self._require_run(connection, run_id))
        active = run.state == "leased"
        if active:
            quarantined = self._quarantine_run_resources(connection, run_id)
            self._finish_execution_segment(
                connection,
                lease,
                result="interrupted",
                certainty="indeterminate",
                reason=attention_reason,
                at=at,
            )
            self._update_scheduler_state_in_transaction(
                connection,
                run,
                state="attention_required",
                at=at,
                attention_reason=attention_reason,
            )
            self._insert_event(
                connection,
                DurableEventInput(
                    run_id=run_id,
                    kind="resources_quarantined",
                    payload={"count": quarantined},
                    occurred_at=at,
                ),
            )
        else:
            released = connection.execute(
                """
                DELETE FROM resource_claims
                WHERE owner_kind = 'run' AND owner_id = ?
                """,
                (run_id,),
            ).rowcount
            if released:
                self._insert_event(
                    connection,
                    DurableEventInput(
                        run_id=run_id,
                        kind="resources_released",
                        payload={"count": released},
                        occurred_at=at,
                    ),
                )
        connection.execute(
            "DELETE FROM executor_leases WHERE token = ?", (lease.token,)
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=run_id,
                kind="executor_lease_lost",
                payload={
                    "segment_id": lease.segment_id,
                    "executor_id": lease.executor_id,
                    "reason": attention_reason,
                },
                occurred_at=at,
            ),
        )
        return active

    def _finish_execution_segment(
        self,
        connection: sqlite3.Connection,
        lease: ExecutorLease,
        *,
        result: RunExecutionSegmentResult,
        certainty: RunExecutionSegmentCertainty,
        reason: str | None,
        at: datetime,
    ) -> RunExecutionSegment:
        segment = self._execution_segment(connection, lease.segment_id)
        if segment.ended_at is not None:
            if (segment.result, segment.certainty, segment.reason) != (
                result,
                certainty,
                reason,
            ):
                raise ControlPlaneConflict(
                    "execution segment already ended with a different outcome"
                )
            return segment
        if result == "interrupted" and not reason:
            raise ValueError("interrupted execution segment requires a reason")
        end_point_count = self._completed_point_count(connection, lease.run_id)
        connection.execute(
            """
            UPDATE run_execution_segments SET
                ended_at = ?, end_point_count = ?, result = ?,
                certainty = ?, reason = ?
            WHERE segment_id = ?
            """,
            (
                _timestamp(at),
                end_point_count,
                result,
                certainty,
                reason,
                segment.segment_id,
            ),
        )
        self._insert_event(
            connection,
            DurableEventInput(
                run_id=lease.run_id,
                kind="execution_segment_ended",
                payload={
                    "segment_id": segment.segment_id,
                    "result": result,
                    "certainty": certainty,
                    "end_point_count": end_point_count,
                },
                occurred_at=at,
            ),
        )
        return self._execution_segment(connection, segment.segment_id)

    @staticmethod
    def _completed_point_count(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> int:
        row = _one(
            connection.execute(
                """
                SELECT completed_point_count
                FROM execution_coverage
                WHERE run_id = ?
                """,
                (run_id,),
            )
        )
        return 0 if row is None else _integer(row, "completed_point_count")

    @staticmethod
    def _execution_segment(
        connection: sqlite3.Connection,
        segment_id: str,
    ) -> RunExecutionSegment:
        row = _one(
            connection.execute(
                "SELECT * FROM run_execution_segments WHERE segment_id = ?",
                (segment_id,),
            )
        )
        if row is None:
            raise ControlPlaneConflict("executor lease has no execution segment")
        return _execution_segment(row)

    @staticmethod
    def _quarantine_run_resources(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> int:
        return connection.execute(
            """
            UPDATE resource_claims SET
                status = 'quarantined'
            WHERE owner_kind = 'run' AND owner_id = ?
            """,
            (run_id,),
        ).rowcount

    @staticmethod
    def _lose_instrument_session(
        connection: sqlite3.Connection,
        session_id: str,
        at: datetime,
        *,
        reason: str,
    ) -> bool:
        row = _one(
            connection.execute(
                "SELECT * FROM instrument_sessions WHERE session_id = ?",
                (session_id,),
            )
        )
        if row is None:
            return False
        session = _instrument_session(row)
        if session.state != "active":
            return False
        quarantined = connection.execute(
            """
            UPDATE resource_claims SET
                status = 'quarantined'
            WHERE owner_kind = 'instrument_session'
                AND owner_id = ?
            """,
            (session_id,),
        ).rowcount
        connection.execute(
            """
            UPDATE instrument_sessions SET
                state = 'attention_required', attention_reason = ?
            WHERE session_id = ? AND state = 'active'
            """,
            (reason, session_id),
        )
        SQLiteControlPlane._insert_event(
            connection,
            DurableEventInput(
                kind="instrument_session_lost",
                payload={
                    "session_id": session_id,
                    "reason": reason,
                    "quarantined_resource_count": quarantined,
                },
                occurred_at=at,
            ),
        )
        return True

    @staticmethod
    def _live_instrument_session(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        at: datetime,
    ) -> InstrumentSession:
        row = _one(
            connection.execute(
                "SELECT * FROM instrument_sessions WHERE session_id = ?",
                (session_id,),
            )
        )
        if row is None:
            raise ControlPlaneNotFound(
                f"instrument session was not found: {session_id}"
            )
        session = _instrument_session(row)
        if session.state != "active":
            raise InstrumentSessionNotActive("instrument session is not active")
        if session.expires_at <= at:
            raise InstrumentSessionNotActive("instrument session lease has expired")
        return session

    @staticmethod
    def _instrument_session_row(
        connection: sqlite3.Connection,
        session_id: str,
    ) -> InstrumentSession:
        row = _one(
            connection.execute(
                "SELECT * FROM instrument_sessions WHERE session_id = ?",
                (session_id,),
            )
        )
        if row is None:
            raise ControlPlaneNotFound(
                f"instrument session was not found: {session_id}"
            )
        return _instrument_session(row)

    @staticmethod
    def _close_instrument_session_in_transaction(
        connection: sqlite3.Connection,
        session: InstrumentSession,
        *,
        status: str,
        at: datetime,
        event_kind: str,
    ) -> int:
        released = connection.execute(
            """
            DELETE FROM resource_claims
            WHERE owner_kind = 'instrument_session' AND owner_id = ?
            """,
            (session.session_id,),
        ).rowcount
        connection.execute(
            """
            UPDATE instrument_sessions SET
                state = 'closed', attention_reason = NULL,
                active_operation_id = NULL, active_operation_kind = NULL,
                end_status = ?
            WHERE session_id = ?
            """,
            (status, session.session_id),
        )
        SQLiteControlPlane._insert_event(
            connection,
            DurableEventInput(
                kind=event_kind,
                payload={
                    "session_id": session.session_id,
                    "instrument_ids": list(session.instrument_ids),
                    "status": status,
                    "released_resource_count": released,
                },
                occurred_at=at,
            ),
        )
        return released

    @staticmethod
    def _run_claims(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[ResourceKey, ...]:
        rows = _all(
            connection.execute(
                """
                SELECT resource_kind, resource_id
                FROM run_resource_claims
                WHERE run_id = ?
                ORDER BY resource_kind, resource_id
                """,
                (run_id,),
            )
        )
        return tuple(
            ResourceKey.model_validate(
                {
                    "kind": _text(row, "resource_kind"),
                    "id": _text(row, "resource_id"),
                }
            )
            for row in rows
        )

    @staticmethod
    def _live_executor(
        connection: sqlite3.Connection,
        token: str,
        *,
        at: datetime,
        run_id: str | None = None,
    ) -> ExecutorLease:
        row = _one(
            connection.execute(
                "SELECT * FROM executor_leases WHERE token = ?", (token,)
            )
        )
        if row is None:
            raise ExecutorLeaseNotHeld("executor token is not held")
        lease = _executor(row)
        if run_id is not None and lease.run_id != run_id:
            raise ExecutorLeaseNotHeld("executor token belongs to another run")
        if lease.expires_at <= at:
            raise ExecutorLeaseNotHeld("executor token has expired")
        return lease

    @staticmethod
    def _require_run(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> sqlite3.Row:
        row = _one(
            connection.execute(
                "SELECT * FROM scheduler_runs WHERE run_id = ?",
                (run_id,),
            )
        )
        if row is None:
            raise ControlPlaneNotFound(f"run was not found: {run_id}")
        return row

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        event: DurableEventInput,
    ) -> DurableEvent:
        cursor = connection.execute(
            """
            INSERT INTO durable_events(run_id, kind, payload_json, occurred_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                event.run_id,
                event.kind,
                _json(event.payload),
                _timestamp(event.occurred_at),
            ),
        )
        return DurableEvent(
            event_id=cast("int", cursor.lastrowid),
            run_id=event.run_id,
            kind=event.kind,
            payload=event.payload,
            occurred_at=event.occurred_at,
        )

    @contextmanager
    def write_transaction(self) -> Generator[sqlite3.Connection]:
        """Open one daemon-owned write unit spanning SQLite adapters."""

        with self.sqlite.write_transaction() as connection:
            yield connection

    @contextmanager
    def read_transaction(self) -> Generator[sqlite3.Connection]:
        """Open one consistent read snapshot without reserving the writer."""

        with self.sqlite.read_transaction() as connection:
            yield connection


def _run(row: sqlite3.Row) -> ControlRun:
    return ControlRun(
        sequence=_integer(row, "sequence"),
        admission=RunAdmissionRecord.model_validate_json(_text(row, "admission_json")),
        state=cast("ControlRunState", _text(row, "state")),
        updated_at=_datetime(_text(row, "updated_at")),
        attention_reason=_optional_text(row, "attention_reason"),
        cancellation_requested_at=(
            None
            if (value := _optional_text(row, "cancellation_requested_at")) is None
            else _datetime(value)
        ),
    )


def _event(row: sqlite3.Row) -> DurableEvent:
    return DurableEvent(
        event_id=_integer(row, "event_id"),
        run_id=_optional_text(row, "run_id"),
        kind=_text(row, "kind"),
        payload=_JSON_OBJECT.validate_json(_text(row, "payload_json")),
        occurred_at=_datetime(_text(row, "occurred_at")),
    )


def _executor(row: sqlite3.Row) -> ExecutorLease:
    return ExecutorLease(
        segment_id=_text(row, "segment_id"),
        run_id=_text(row, "run_id"),
        executor_id=_text(row, "executor_id"),
        token=_text(row, "token"),
        acquired_at=_datetime(_text(row, "acquired_at")),
        renewed_at=_datetime(_text(row, "renewed_at")),
        expires_at=_datetime(_text(row, "expires_at")),
    )


def _execution_segment(row: sqlite3.Row) -> RunExecutionSegment:
    ended_at = _optional_text(row, "ended_at")
    return RunExecutionSegment.model_validate(
        {
            "sequence": _integer(row, "sequence"),
            "segment_id": _text(row, "segment_id"),
            "run_id": _text(row, "run_id"),
            "ordinal": _integer(row, "ordinal"),
            "executor_id": _text(row, "executor_id"),
            "run_contract_fingerprint": _text(
                row,
                "run_contract_fingerprint",
            ),
            "started_at": _datetime(_text(row, "started_at")),
            "start_point_count": _integer(row, "start_point_count"),
            "ended_at": None if ended_at is None else _datetime(ended_at),
            "end_point_count": cast("int | None", row["end_point_count"]),
            "result": _optional_text(row, "result"),
            "certainty": _optional_text(row, "certainty"),
            "reason": _optional_text(row, "reason"),
        }
    )


def _resource_claim(row: sqlite3.Row) -> ResourceClaim:
    return ResourceClaim.model_validate(
        {
            "resource": {
                "kind": _text(row, "resource_kind"),
                "id": _text(row, "resource_id"),
            },
            "owner_kind": _text(row, "owner_kind"),
            "owner_id": _text(row, "owner_id"),
            "status": _text(row, "status"),
            "acquired_at": _datetime(_text(row, "acquired_at")),
        }
    )


def _instrument_session(row: sqlite3.Row) -> InstrumentSession:
    return InstrumentSession.model_validate(
        {
            "session_id": _text(row, "session_id"),
            "open_operation_id": _text(row, "open_operation_id"),
            "actor": _text(row, "actor"),
            "config_entry_id": _text(row, "config_entry_id"),
            "config_content_hash": _text(row, "config_content_hash"),
            "instrument_ids": _STRING_TUPLE.validate_json(
                _text(row, "instrument_ids_json")
            ),
            "exclusivity_keys": _STRING_TUPLE.validate_json(
                _text(row, "exclusivity_keys_json")
            ),
            "state": _text(row, "state"),
            "acquired_at": _datetime(_text(row, "acquired_at")),
            "renewed_at": _datetime(_text(row, "renewed_at")),
            "expires_at": _datetime(_text(row, "expires_at")),
            "attention_reason": _optional_text(row, "attention_reason"),
            "active_operation_id": _optional_text(row, "active_operation_id"),
            "active_operation_kind": _optional_text(row, "active_operation_kind"),
            "end_status": _optional_text(row, "end_status"),
        }
    )


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _all(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


def _text(row: sqlite3.Row, name: str) -> str:
    return cast("str", row[name])


def _optional_text(row: sqlite3.Row, name: str) -> str | None:
    return cast("str | None", row[name])


def _integer(row: sqlite3.Row, name: str) -> int:
    return cast("int", row[name])


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "control-plane timestamps must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _json(value: dict[str, JsonValue]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _page_size(limit: int) -> None:
    if not 1 <= limit <= 500:
        msg = "page size must be between 1 and 500"
        raise ValueError(msg)


def _ttl(ttl: timedelta) -> None:
    if ttl <= timedelta(0):
        msg = "lease ttl must be positive"
        raise ValueError(msg)
