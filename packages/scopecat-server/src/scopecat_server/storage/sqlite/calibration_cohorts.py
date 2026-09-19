"""SQLite persistence and consistent status reads for calibration cohorts."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import ValidationError
from scopecat.automation.calibration_wire import CalibrationPublicationReadyItem
from scopecat.automation.calibrations import (
    CalibrationAttemptStatus,
    CalibrationCohort,
    CalibrationCohortFinalization,
    CalibrationCohortFinalizationState,
    CalibrationCohortMember,
    CalibrationCohortSummary,
    CalibrationPublicationAttention,
    CalibrationPublicationCompletion,
    CalibrationPublicationFailure,
    CalibrationPublicationPolicyRef,
    CalibrationPublicationSupersession,
    CalibrationSetupSupersession,
    CalibrationStatus,
    CalibrationStatusSnapshot,
    CalibrationSuccessPublication,
    CalibrationSuccessRef,
    CalibrationWorkingPointSupersession,
)
from scopecat.automation.models import ProcedureRun
from scopecat.config.registry.records import (
    ConfigRegistryEntry,
    ContextConfigRegistrySource,
)
from scopecat.daemon.wire import CalibrationPublicationReceipt
from scopecat.records.calibration_scope import (
    CalibrationConfigSourceRef,
    WorkingPointCalibrationScope,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.scientific_scope import setup_content_hash

from scopecat_server.storage.sqlite.config_registry import (
    SQLiteConfigRegistryRepository,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase

_ACTIVE_CALIBRATION_COUNT_SQL = """
SELECT COUNT(*) AS active_count
FROM calibration_cohort_members AS members
JOIN calibration_cohorts AS cohorts
  ON cohorts.cohort_id = members.cohort_id
JOIN procedure_runs AS runs
  ON runs.procedure_run_id = members.procedure_run_id
WHERE cohorts.fanout_scope = ?
  AND runs.state IN (
      'ready', 'leased', 'waiting_for_input', 'attention_required'
  )
"""


class CalibrationCohortStoreError(RuntimeError):
    """Durable calibration cohort state could not be read or committed."""


class CalibrationCohortNotFound(CalibrationCohortStoreError):
    """A requested durable calibration cohort does not exist."""


class CalibrationCohortConflict(CalibrationCohortStoreError):
    """A calibration cohort command conflicts with durable state."""


@dataclass(frozen=True, slots=True)
class StoredCalibrationCohortPage:
    """Newest-first keyset page of calibration cohort summaries."""

    items: tuple[CalibrationCohortSummary, ...]
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class StoredCalibrationCohortMemberPage:
    """Admission-order keyset page of one cohort's members."""

    items: tuple[CalibrationCohortMember, ...]
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class StoredCalibrationPublicationReadyPage:
    """Insertion-oldest page within one finite ready-work traversal."""

    items: tuple[CalibrationPublicationReadyItem, ...]
    next_cursor: int | None = None
    through_sequence: int | None = None


class SQLiteCalibrationCohortStore:
    """Immutable cohorts joined to live durable ProcedureRun state."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.sqlite = database
        self.database = database.path

    @contextmanager
    def read_transaction(self) -> Generator[sqlite3.Connection]:
        with self.sqlite.read_transaction() as connection:
            yield connection

    @contextmanager
    def write_transaction(self) -> Generator[sqlite3.Connection]:
        with self.sqlite.write_transaction() as connection:
            yield connection

    def read(self, cohort_id: str) -> CalibrationCohort:
        with self.sqlite.read_connection() as connection:
            return self.read_in_transaction(connection, cohort_id)

    def read_in_transaction(
        self,
        connection: sqlite3.Connection,
        cohort_id: str,
    ) -> CalibrationCohort:
        try:
            row = _one(
                connection.execute(
                    """
                    SELECT cohort_id AS stored_cohort_id,
                           fanout_scope AS stored_fanout_scope,
                           cohort_json
                    FROM calibration_cohorts
                    WHERE cohort_id = ?
                    """,
                    (cohort_id,),
                )
            )
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                f"failed to read calibration cohort: {cohort_id}"
            ) from error
        if row is None:
            raise CalibrationCohortNotFound(
                f"calibration cohort was not found: {cohort_id}"
            )
        return _cohort(row)

    def list(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        fanout_scope: str | None = None,
    ) -> StoredCalibrationCohortPage:
        if not 1 <= limit <= 200:
            raise ValueError("calibration cohort page size must be between 1 and 200")
        if before is not None and before < 1:
            raise ValueError("calibration cohort cursor must be positive")
        clauses: list[str] = []
        parameters: list[str | int] = []
        if before is not None:
            clauses.append("sequence < ?")
            parameters.append(before)
        if fanout_scope is not None:
            clauses.append("fanout_scope = ?")
            parameters.append(fanout_scope)
        where = "" if not clauses else f"WHERE {' AND '.join(clauses)}"
        parameters.append(limit + 1)
        try:
            with self.sqlite.read_connection() as connection:
                rows = _all(
                    connection.execute(
                        f"""
                        SELECT sequence,
                               cohort_id AS stored_cohort_id,
                               fanout_scope AS stored_fanout_scope,
                               cohort_json
                        FROM calibration_cohorts
                        {where}
                        ORDER BY sequence DESC
                        LIMIT ?
                        """,  # noqa: S608 - clauses are fixed internal fragments
                        parameters,
                    )
                )
            selected = rows[:limit]
            return StoredCalibrationCohortPage(
                items=tuple(
                    CalibrationCohortSummary.from_cohort(_cohort(row))
                    for row in selected
                ),
                next_cursor=(
                    _integer(selected[-1], "sequence") if len(rows) > limit else None
                ),
            )
        except (sqlite3.Error, ValidationError) as error:
            raise CalibrationCohortStoreError(
                "failed to list calibration cohorts"
            ) from error

    def list_members(
        self,
        cohort_id: str,
        *,
        limit: int = 50,
        after: int | None = None,
    ) -> StoredCalibrationCohortMemberPage:
        if not 1 <= limit <= 200:
            raise ValueError(
                "calibration cohort member page size must be between 1 and 200"
            )
        if after is not None and after < 0:
            raise ValueError("calibration cohort member cursor cannot be negative")
        try:
            with self.sqlite.read_transaction() as connection:
                self.read_in_transaction(connection, cohort_id)
                rows = _all(
                    connection.execute(
                        """
                        SELECT members.member_index,
                               members.cohort_id AS stored_member_cohort_id,
                               members.member_index AS stored_member_index,
                               members.member_id AS stored_member_id,
                               members.calibration_key AS stored_calibration_key,
                               members.procedure_run_id
                                   AS stored_procedure_run_id,
                               members.member_json
                        FROM calibration_cohort_members AS members
                        WHERE members.cohort_id = ?
                          AND (? IS NULL OR members.member_index > ?)
                        ORDER BY members.member_index ASC
                        LIMIT ?
                        """,
                        (cohort_id, after, after, limit + 1),
                    )
                )
            selected = rows[:limit]
            return StoredCalibrationCohortMemberPage(
                items=tuple(_member(row) for row in selected),
                next_cursor=(
                    _integer(selected[-1], "member_index")
                    if len(rows) > limit
                    else None
                ),
            )
        except (sqlite3.Error, ValidationError) as error:
            raise CalibrationCohortStoreError(
                f"failed to list calibration cohort members: {cohort_id}"
            ) from error

    def list_members_in_transaction(
        self,
        connection: sqlite3.Connection,
        cohort_id: str,
    ) -> tuple[CalibrationCohortMember, ...]:
        try:
            rows = _all(
                connection.execute(
                    """
                    SELECT members.cohort_id AS stored_member_cohort_id,
                           members.member_index AS stored_member_index,
                           members.member_id AS stored_member_id,
                           members.calibration_key AS stored_calibration_key,
                           members.procedure_run_id AS stored_procedure_run_id,
                           members.member_json
                    FROM calibration_cohort_members AS members
                    WHERE members.cohort_id = ?
                    ORDER BY members.member_index ASC
                    """,
                    (cohort_id,),
                )
            )
            return tuple(_member(row) for row in rows)
        except (sqlite3.Error, ValidationError) as error:
            raise CalibrationCohortStoreError(
                f"failed to read calibration cohort members: {cohort_id}"
            ) from error

    def read_finalization(self, cohort_id: str) -> CalibrationCohortFinalization:
        with self.sqlite.read_connection() as connection:
            return self.read_finalization_in_transaction(connection, cohort_id)

    def read_finalization_in_transaction(
        self,
        connection: sqlite3.Connection,
        cohort_id: str,
    ) -> CalibrationCohortFinalization:
        try:
            row = _one(
                connection.execute(
                    """
                    SELECT finalizations.*,
                           cohorts.cohort_id AS stored_cohort_id,
                           cohorts.fanout_scope AS stored_fanout_scope,
                           cohorts.cohort_json
                    FROM calibration_cohort_finalizations AS finalizations
                    CROSS JOIN calibration_cohorts AS cohorts
                      ON cohorts.cohort_id = finalizations.cohort_id
                    WHERE finalizations.cohort_id = ?
                    """,
                    (cohort_id,),
                )
            )
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                f"failed to read calibration publication: {cohort_id}"
            ) from error
        if row is None:
            raise CalibrationCohortNotFound(
                f"calibration publication was not found: {cohort_id}"
            )
        return _finalization(row)

    def list_ready_publications(
        self,
        capabilities: tuple[CalibrationPublicationPolicyRef, ...],
        *,
        at: datetime,
        limit: int = 50,
        after: int | None = None,
        through_sequence: int | None = None,
    ) -> StoredCalibrationPublicationReadyPage:
        if not 1 <= limit <= 200:
            raise ValueError(
                "calibration publication page size must be between 1 and 200"
            )
        if after is not None and after < 1:
            raise ValueError("calibration publication cursor must be positive")
        _require_traversal_pair(after, through_sequence)
        if not capabilities:
            return StoredCalibrationPublicationReadyPage(items=())

        query, capability_parameters = _ready_publication_rows_query(capabilities)
        try:
            with self.sqlite.read_transaction() as connection:
                traversal_end = through_sequence
                if traversal_end is None:
                    row = _one(
                        connection.execute(
                            """
                            SELECT MAX(sequence) AS sequence
                            FROM calibration_publication_ready_queue
                            """
                        )
                    )
                    traversal_end = (
                        None if row is None else _optional_integer(row, "sequence")
                    )
                if traversal_end is None:
                    return StoredCalibrationPublicationReadyPage(items=())
                rows = _all(
                    connection.execute(
                        query,
                        (
                            _timestamp(at),
                            after,
                            after,
                            traversal_end,
                            *capability_parameters,
                            limit + 1,
                        ),
                    )
                )
            selected = rows[:limit]
            has_next = len(rows) > limit
            return StoredCalibrationPublicationReadyPage(
                items=tuple(_ready_item(row) for row in selected),
                next_cursor=(_integer(selected[-1], "sequence") if has_next else None),
                through_sequence=traversal_end if has_next else None,
            )
        except (sqlite3.Error, ValidationError) as error:
            raise CalibrationCohortStoreError(
                "failed to list ready calibration publications"
            ) from error

    def require_publication_attention_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        cohort_id: str,
        policy: CalibrationPublicationPolicyRef,
        expected_revision: int,
        actor: str,
        reason: str,
        at: datetime,
    ) -> CalibrationCohortFinalization:
        current = self._require_publication_transition(
            connection,
            cohort_id=cohort_id,
            policy=policy,
            expected_revision=expected_revision,
            expected_state="ready",
        )
        try:
            updated = connection.execute(
                """
                UPDATE calibration_cohort_finalizations
                SET revision = revision + 1,
                    state = 'attention_required',
                    updated_at = ?,
                    available_at = NULL,
                    attention_actor = ?,
                    attention_reason = ?,
                    attention_required_at = ?
                WHERE cohort_id = ? AND revision = ? AND state = 'ready'
                """,
                (
                    _timestamp(at),
                    actor,
                    reason,
                    _timestamp(at),
                    cohort_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise CalibrationCohortConflict(
                    "calibration publication finalization changed"
                )
            connection.execute(
                "DELETE FROM calibration_publication_ready_queue WHERE cohort_id = ?",
                (cohort_id,),
            )
        except sqlite3.IntegrityError as error:
            raise CalibrationCohortConflict(
                "calibration publication attention conflicts with durable state"
            ) from error
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                "failed to require calibration publication attention"
            ) from error
        del current
        return self.read_finalization_in_transaction(connection, cohort_id)

    def retry_publication_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        cohort_id: str,
        policy: CalibrationPublicationPolicyRef,
        expected_revision: int,
        at: datetime,
    ) -> CalibrationCohortFinalization:
        self._require_publication_transition(
            connection,
            cohort_id=cohort_id,
            policy=policy,
            expected_revision=expected_revision,
            expected_state="attention_required",
        )
        try:
            updated = connection.execute(
                """
                UPDATE calibration_cohort_finalizations
                SET revision = revision + 1,
                    state = 'ready',
                    updated_at = ?,
                    ready_at = ?,
                    available_at = ?,
                    attention_actor = NULL,
                    attention_reason = NULL,
                    attention_required_at = NULL
                WHERE cohort_id = ?
                  AND revision = ?
                  AND state = 'attention_required'
                """,
                (
                    _timestamp(at),
                    _timestamp(at),
                    _timestamp(at),
                    cohort_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise CalibrationCohortConflict(
                    "calibration publication finalization changed"
                )
            connection.execute(
                """
                INSERT INTO calibration_publication_ready_queue(
                    cohort_id, enqueued_at
                ) VALUES (?, ?)
                """,
                (
                    cohort_id,
                    _timestamp(at),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CalibrationCohortConflict(
                "calibration publication retry conflicts with durable state"
            ) from error
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                "failed to retry calibration publication"
            ) from error
        return self.read_finalization_in_transaction(connection, cohort_id)

    def defer_publication_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        cohort_id: str,
        policy: CalibrationPublicationPolicyRef,
        expected_revision: int,
        retry_after_seconds: int,
        at: datetime,
    ) -> CalibrationCohortFinalization:
        self._require_publication_transition(
            connection,
            cohort_id=cohort_id,
            policy=policy,
            expected_revision=expected_revision,
            expected_state="ready",
        )
        available_at = at + timedelta(seconds=retry_after_seconds)
        try:
            updated = connection.execute(
                """
                UPDATE calibration_cohort_finalizations
                SET revision = revision + 1,
                    attempt_count = attempt_count + 1,
                    updated_at = ?,
                    available_at = ?
                WHERE cohort_id = ? AND revision = ? AND state = 'ready'
                """,
                (
                    _timestamp(at),
                    _timestamp(available_at),
                    cohort_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise CalibrationCohortConflict(
                    "calibration publication finalization changed"
                )
        except sqlite3.IntegrityError as error:
            raise CalibrationCohortConflict(
                "calibration publication defer conflicts with durable state"
            ) from error
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                "failed to defer calibration publication"
            ) from error
        return self.read_finalization_in_transaction(connection, cohort_id)

    def complete_publication_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        cohort_id: str,
        policy: CalibrationPublicationPolicyRef,
        expected_revision: int,
        operation_id: str,
        at: datetime,
    ) -> CalibrationCohortFinalization:
        current = self.read_finalization_in_transaction(connection, cohort_id)
        if current.policy != policy:
            raise CalibrationCohortConflict("calibration publication policy changed")
        if current.revision != expected_revision or current.state != "ready":
            raise CalibrationCohortConflict(
                "calibration publication is not eligible for completion"
            )
        if current.available_at is None or current.available_at > at:
            raise CalibrationCohortConflict(
                "calibration publication is not yet available for completion"
            )
        try:
            updated = connection.execute(
                """
                UPDATE calibration_cohort_finalizations
                SET revision = revision + 1,
                    state = 'published',
                    updated_at = ?,
                    available_at = NULL,
                    attention_actor = NULL,
                    attention_reason = NULL,
                    attention_required_at = NULL,
                    publication_operation_id = ?,
                    published_at = ?
                WHERE cohort_id = ? AND revision = ? AND state = 'ready'
                  AND available_at <= ?
                """,
                (
                    _timestamp(at),
                    operation_id,
                    _timestamp(at),
                    cohort_id,
                    expected_revision,
                    _timestamp(at),
                ),
            ).rowcount
            if updated != 1:
                raise CalibrationCohortConflict(
                    "calibration publication finalization changed"
                )
            connection.execute(
                "DELETE FROM calibration_publication_ready_queue WHERE cohort_id = ?",
                (cohort_id,),
            )
        except sqlite3.IntegrityError as error:
            raise CalibrationCohortConflict(
                "calibration publication completion conflicts with durable state"
            ) from error
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                "failed to complete calibration publication"
            ) from error
        return self.read_finalization_in_transaction(connection, cohort_id)

    def supersede_working_point_in_transaction(
        self, connection: sqlite3.Connection, *, entry: ConfigRegistryEntry
    ) -> int:
        source = entry.source
        assert isinstance(source, ContextConfigRegistrySource)
        context = source.context
        if (
            SQLiteConfigRegistryRepository(connection).context_head(
                context.workspace_id
            )
            != entry.id
        ):
            return 0
        scope = WorkingPointCalibrationScope(
            workspace_id=context.workspace_id, sample=context.sample
        )
        replacement = CalibrationConfigSourceRef(
            entry_id=entry.id,
            config_ref=entry.config_ref,
            content_hash=entry.content_hash,
            scope=scope,
        )
        rows = _all(
            connection.execute(
                """SELECT cohort_id FROM calibration_cohort_finalizations
                WHERE owner_workspace_id = ? AND base_entry_id != ?
                AND state IN ('waiting','ready','attention_required')""",
                (scope.workspace_id, entry.id),
            )
        )
        evidence = CalibrationPublicationSupersession(
            evidence=CalibrationWorkingPointSupersession(superseded_by=replacement),
            superseded_at=entry.recorded_at,
        )
        return self._supersede_rows(connection, rows, evidence)

    def supersede_setup_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        config: ConfigProfileSnapshot,
        at: datetime,
    ) -> int:
        current = setup_content_hash(config)
        rows = _all(
            connection.execute(
                """SELECT cohort_id FROM calibration_cohort_finalizations
                WHERE setup_content_hash != ?
                AND state IN ('waiting','ready','attention_required')""",
                (current,),
            )
        )
        evidence = CalibrationPublicationSupersession(
            evidence=CalibrationSetupSupersession(setup_content_hash=current),
            superseded_at=at,
        )
        return self._supersede_rows(connection, rows, evidence)

    def _supersede_rows(
        self,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
        evidence: CalibrationPublicationSupersession,
    ) -> int:
        for row in rows:
            cohort_id = _text(row, "cohort_id")
            connection.execute(
                """UPDATE calibration_cohort_finalizations
                SET revision = revision + 1, state = 'superseded', updated_at = ?,
                available_at = NULL, attention_actor = NULL, attention_reason = NULL,
                attention_required_at = NULL, supersession_json = ?, superseded_at = ?
                WHERE cohort_id = ?""",
                (
                    _timestamp(evidence.superseded_at),
                    evidence.model_dump_json(),
                    _timestamp(evidence.superseded_at),
                    cohort_id,
                ),
            )
            connection.execute(
                "DELETE FROM calibration_publication_ready_queue WHERE cohort_id = ?",
                (cohort_id,),
            )
        return len(rows)

    def _require_publication_transition(
        self,
        connection: sqlite3.Connection,
        *,
        cohort_id: str,
        policy: CalibrationPublicationPolicyRef,
        expected_revision: int,
        expected_state: str,
    ) -> CalibrationCohortFinalization:
        current = self.read_finalization_in_transaction(connection, cohort_id)
        if current.policy != policy:
            raise CalibrationCohortConflict("calibration publication policy changed")
        if current.revision != expected_revision:
            raise CalibrationCohortConflict(
                "calibration publication finalization revision changed"
            )
        if current.state != expected_state:
            raise CalibrationCohortConflict(
                f"calibration publication requires {expected_state} state"
            )
        return current

    def status_snapshot(
        self,
        calibration_keys: tuple[str, ...],
        *,
        fanout_scope: str,
        clock: Callable[[], datetime],
    ) -> CalibrationStatusSnapshot:
        with self.sqlite.read_transaction() as connection:
            return self.status_snapshot_in_transaction(
                connection,
                calibration_keys,
                fanout_scope=fanout_scope,
                clock=clock,
            )

    def status_snapshot_in_transaction(
        self,
        connection: sqlite3.Connection,
        calibration_keys: tuple[str, ...],
        *,
        fanout_scope: str,
        clock: Callable[[], datetime],
    ) -> CalibrationStatusSnapshot:
        try:
            rows = self._status_rows_in_transaction(connection, calibration_keys)
            statuses = _statuses(calibration_keys, rows)
            active_row = _one(
                connection.execute(
                    _ACTIVE_CALIBRATION_COUNT_SQL,
                    (fanout_scope,),
                )
            )
            assert active_row is not None
            return CalibrationStatusSnapshot(
                # SQLite fixes a deferred read transaction at its first SELECT.
                # Sample the server clock only after every projection has been
                # read from that snapshot, so no included transition can follow
                # the advertised observation time.
                observed_at=clock(),
                fanout_scope=fanout_scope,
                fanout_active_count=_integer(active_row, "active_count"),
                statuses=statuses,
            )
        except (sqlite3.Error, ValidationError) as error:
            raise CalibrationCohortStoreError(
                "failed to read calibration status snapshot"
            ) from error

    def _status_rows_in_transaction(
        self,
        connection: sqlite3.Connection,
        calibration_keys: tuple[str, ...],
    ) -> list[sqlite3.Row]:
        if not calibration_keys:
            return []
        query, parameters = _status_rows_query(calibration_keys)
        return _all(
            connection.execute(
                query,
                parameters,
            )
        )

    def insert_cohort_in_transaction(
        self,
        connection: sqlite3.Connection,
        cohort: CalibrationCohort,
    ) -> None:
        policy = cohort.spec.automatic_publication
        try:
            connection.execute(
                """
                INSERT INTO calibration_cohorts(
                    cohort_id, fanout_scope, cohort_json
                )
                VALUES (?, ?, ?)
                """,
                (
                    cohort.cohort_id,
                    cohort.spec.fanout_scope,
                    cohort.model_dump_json(),
                ),
            )
            if policy is not None:
                selected_composition = policy.composition_policy
                base = cohort.spec.config_source
                assert isinstance(base.scope, WorkingPointCalibrationScope)
                config = SQLiteConfigRegistryRepository(connection).read_config(
                    base.config_ref
                )
                connection.execute(
                    """
                    INSERT INTO calibration_cohort_finalizations(
                        cohort_id, spec_hash, policy_id, policy_version,
                        policy_fingerprint, policy_json,
                        calibration_definition_id,
                        calibration_definition_version,
                        calibration_definition_fingerprint,
                        composition_policy_id,
                        composition_policy_version,
                        composition_policy_fingerprint, base_entry_id,
                        owner_workspace_id, setup_content_hash,
                        revision, state, attempt_count, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                            'waiting', 0, ?, ?)
                    """,
                    (
                        cohort.cohort_id,
                        cohort.spec_hash,
                        policy.id,
                        policy.version,
                        policy.fingerprint,
                        policy.model_dump_json(),
                        policy.calibration.id,
                        policy.calibration.version,
                        policy.calibration.fingerprint,
                        selected_composition.id,
                        selected_composition.version,
                        selected_composition.fingerprint,
                        base.entry_id,
                        base.scope.workspace_id,
                        setup_content_hash(config),
                        _timestamp(cohort.created_at),
                        _timestamp(cohort.created_at),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise CalibrationCohortConflict(
                "calibration cohort id already has durable state"
            ) from error
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                "failed to insert calibration cohort"
            ) from error

    def insert_member_in_transaction(
        self,
        connection: sqlite3.Connection,
        member: CalibrationCohortMember,
    ) -> None:
        try:
            inserted = connection.execute(
                """
                INSERT INTO calibration_cohort_members(
                    cohort_id, member_index, member_id, calibration_key,
                    procedure_run_id, closure_status, closed_at, member_json
                )
                SELECT ?, ?, ?, ?, ?, runs.closure_status, runs.closed_at, ?
                FROM procedure_runs AS runs
                WHERE runs.procedure_run_id = ?
                """,
                (
                    member.cohort_id,
                    member.index,
                    member.spec.member_id,
                    member.spec.calibration_key,
                    member.procedure_run_id,
                    member.model_dump_json(),
                    member.procedure_run_id,
                ),
            ).rowcount
            if inserted != 1:
                raise CalibrationCohortConflict(
                    "calibration cohort member procedure run was not found"
                )
        except sqlite3.IntegrityError as error:
            raise CalibrationCohortConflict(
                "calibration cohort member conflicts with durable state"
            ) from error
        except sqlite3.Error as error:
            raise CalibrationCohortStoreError(
                "failed to insert calibration cohort member"
            ) from error

    def insert_success_publication_in_transaction(
        self,
        connection: sqlite3.Connection,
        success: CalibrationSuccessRef,
    ) -> None:
        """Attach one config publication to its exact successful cohort member."""

        publication = success.publication
        if publication is None:
            raise ValueError("calibration success publication is required")
        try:
            row = _one(
                connection.execute(
                    """
                    SELECT members.cohort_id AS stored_member_cohort_id,
                           members.member_index AS stored_member_index,
                           members.member_id AS stored_member_id,
                           members.calibration_key AS stored_calibration_key,
                           members.procedure_run_id AS stored_procedure_run_id,
                           members.member_json, members.closure_status,
                           members.closed_at,
                           cohorts.cohort_id AS stored_cohort_id,
                           cohorts.fanout_scope AS stored_fanout_scope,
                           cohorts.cohort_json, runs.run_json
                    FROM calibration_cohort_members AS members
                    CROSS JOIN calibration_cohorts AS cohorts
                      ON cohorts.cohort_id = members.cohort_id
                    CROSS JOIN procedure_runs AS runs
                      ON runs.procedure_run_id = members.procedure_run_id
                    WHERE members.procedure_run_id = ?
                    """,
                    (success.attempt.procedure_run_id,),
                )
            )
            if row is None:
                raise CalibrationCohortConflict(
                    "calibration publication member was not found"
                )
            member = _member(row)
            cohort = _cohort(row)
            run = _run(row)
            if (
                member.cohort_id != cohort.cohort_id
                or success.attempt != member.attempt_ref
                or success.base_config_source != cohort.spec.config_source
                or run.state != "closed"
                or run.closure is None
                or run.closure.status != "succeeded"
                or success.succeeded_at != run.closure.closed_at
            ):
                raise CalibrationCohortConflict(
                    "calibration publication does not match its exact member success"
                )
            operation_row = _one(
                connection.execute(
                    """
                    SELECT receipt_json
                    FROM config_operations
                    WHERE operation_id = ? AND kind = 'publish_calibration'
                    """,
                    (publication.operation_id,),
                )
            )
            if operation_row is None:
                raise CalibrationCohortConflict(
                    "calibration publication config operation was not found"
                )
            receipt = CalibrationPublicationReceipt.model_validate_json(
                _text(operation_row, "receipt_json")
            )
            recorded_success = next(
                (
                    item
                    for item in receipt.calibration_successes
                    if item.attempt.procedure_run_id == success.attempt.procedure_run_id
                ),
                None,
            )
            result_source = publication.result_config_source
            base_source = success.base_config_source
            if (
                recorded_success != success
                or receipt.operation.operation_id != publication.operation_id
                or receipt.operation.source_intent_hash
                != publication.source_intent_hash
                or receipt.operation.base != base_source
                or receipt.entry.id != result_source.entry_id
                or receipt.entry.config_ref != result_source.config_ref
                or receipt.entry.content_hash != result_source.content_hash
                or receipt.operation.recorded_at != publication.published_at
                or result_source.scope != base_source.scope
            ):
                raise CalibrationCohortConflict(
                    "calibration publication does not match its exact config receipt"
                )
            connection.execute(
                """
                INSERT INTO calibration_success_publications(
                    procedure_run_id, cohort_id, member_id, calibration_key,
                    operation_id, source_intent_hash, result_input_fingerprint,
                    result_freshness_fingerprint, result_entry_id,
                    result_config_ref, result_content_hash,
                    published_at, publication_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    success.attempt.procedure_run_id,
                    success.attempt.cohort_id,
                    success.attempt.member_id,
                    success.attempt.calibration_key,
                    publication.operation_id,
                    publication.source_intent_hash,
                    publication.result_input_fingerprint,
                    publication.result_freshness_fingerprint,
                    result_source.entry_id,
                    result_source.config_ref,
                    result_source.content_hash,
                    _timestamp(publication.published_at),
                    publication.model_dump_json(),
                ),
            )
        except CalibrationCohortConflict:
            raise
        except sqlite3.IntegrityError as error:
            raise CalibrationCohortConflict(
                "calibration success already has a different publication"
            ) from error
        except (sqlite3.Error, ValidationError) as error:
            raise CalibrationCohortStoreError(
                "failed to insert calibration success publication"
            ) from error


def _publication_policy_routing_key(
    policy: CalibrationPublicationPolicyRef,
) -> tuple[str, ...]:
    calibration = policy.calibration
    composition = policy.composition_policy
    return (
        policy.id,
        policy.version,
        policy.fingerprint,
        calibration.id,
        calibration.version,
        calibration.fingerprint,
        composition.id,
        composition.version,
        composition.fingerprint,
    )


def _ready_publication_rows_query(
    capabilities: tuple[CalibrationPublicationPolicyRef, ...],
) -> tuple[str, tuple[str, ...]]:
    if not capabilities:
        raise ValueError("ready publication query requires capabilities")
    capability_clause = " OR ".join(
        """(
            finalizations.policy_id = ?
            AND finalizations.policy_version = ?
            AND finalizations.policy_fingerprint = ?
            AND finalizations.calibration_definition_id = ?
            AND finalizations.calibration_definition_version = ?
            AND finalizations.calibration_definition_fingerprint = ?
            AND finalizations.composition_policy_id = ?
            AND finalizations.composition_policy_version = ?
            AND finalizations.composition_policy_fingerprint = ?
        )"""
        for _capability in capabilities
    )
    parameters = tuple(
        component
        for capability in capabilities
        for component in _publication_policy_routing_key(capability)
    )
    query = f"""
    SELECT queue.sequence, queue.enqueued_at,
           finalizations.*,
           cohorts.cohort_id AS stored_cohort_id,
           cohorts.fanout_scope AS stored_fanout_scope,
           cohorts.cohort_json
    FROM calibration_cohort_finalizations AS finalizations
    JOIN calibration_publication_ready_queue AS queue
      ON queue.cohort_id = finalizations.cohort_id
    JOIN calibration_cohorts AS cohorts
      ON cohorts.cohort_id = finalizations.cohort_id
    WHERE finalizations.state = 'ready'
      AND finalizations.available_at <= ?
      AND (? IS NULL OR queue.sequence > ?)
      AND queue.sequence <= ?
      AND ({capability_clause})
    ORDER BY queue.sequence ASC
    LIMIT ?
    """  # noqa: S608 - capability clause contains generated placeholders only
    return query, parameters


def _status_rows_query(
    calibration_keys: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    if not calibration_keys:
        raise ValueError("calibration status row query requires at least one key")
    requested = ", ".join("(?)" for _key in calibration_keys)
    query = f"""
    WITH requested(calibration_key) AS MATERIALIZED (
        VALUES {requested}
    ),
    chosen AS MATERIALIZED (
        SELECT requested.calibration_key,
               (
                   SELECT latest_attempt.sequence
                   FROM calibration_cohort_members AS latest_attempt
                   WHERE latest_attempt.calibration_key = requested.calibration_key
                   ORDER BY latest_attempt.sequence DESC
                   LIMIT 1
               ) AS latest_attempt_sequence,
               (
                   SELECT latest_success.sequence
                   FROM calibration_cohort_members AS latest_success
                   WHERE latest_success.calibration_key = requested.calibration_key
                     AND latest_success.closure_status = 'succeeded'
                   ORDER BY latest_success.sequence DESC
                   LIMIT 1
               ) AS latest_success_sequence
        FROM requested
    ),
    selected(sequence) AS (
        SELECT latest_attempt_sequence
        FROM chosen
        WHERE latest_attempt_sequence IS NOT NULL
        UNION
        SELECT latest_success_sequence
        FROM chosen
        WHERE latest_success_sequence IS NOT NULL
    )
    SELECT members.calibration_key,
           members.cohort_id AS stored_member_cohort_id,
           members.member_index AS stored_member_index,
           members.member_id AS stored_member_id,
           members.calibration_key AS stored_calibration_key,
           members.procedure_run_id AS stored_procedure_run_id,
           members.member_json,
           cohorts.cohort_id AS stored_cohort_id,
           cohorts.fanout_scope AS stored_fanout_scope,
           cohorts.cohort_json,
           members.sequence AS member_sequence,
           runs.run_json, members.closure_status, members.closed_at,
           publications.publication_json
    FROM selected
    CROSS JOIN calibration_cohort_members AS members
      ON members.sequence = selected.sequence
    CROSS JOIN procedure_runs AS runs
      ON runs.procedure_run_id = members.procedure_run_id
    CROSS JOIN calibration_cohorts AS cohorts
      ON cohorts.cohort_id = members.cohort_id
    LEFT JOIN calibration_success_publications AS publications
      ON publications.procedure_run_id = members.procedure_run_id
    ORDER BY members.sequence DESC
    """  # noqa: S608 - requested contains only generated placeholders
    return query, calibration_keys


def _statuses(
    calibration_keys: tuple[str, ...],
    rows: list[sqlite3.Row],
) -> tuple[CalibrationStatus, ...]:
    latest_attempts: dict[str, CalibrationAttemptStatus] = {}
    latest_successes: dict[str, CalibrationSuccessRef] = {}
    for row in rows:
        key = _text(row, "calibration_key")
        member = _member(row)
        cohort = _cohort(row)
        if member.cohort_id != cohort.cohort_id:
            raise CalibrationCohortStoreError(
                "calibration status member does not match its cohort"
            )
        run = _run(row)
        attempt = CalibrationAttemptStatus(
            attempt=member.attempt_ref,
            procedure_state=run.state,
            procedure_revision=run.revision,
            updated_at=run.updated_at,
            closure=run.closure,
        )
        latest_attempts.setdefault(key, attempt)
        if _optional_text(row, "closure_status") == "succeeded":
            closed_at = _optional_text(row, "closed_at")
            if closed_at is None:
                raise CalibrationCohortStoreError(
                    "successful calibration attempt is missing closed_at"
                )
            latest_successes.setdefault(
                key,
                CalibrationSuccessRef(
                    attempt=member.attempt_ref,
                    base_config_source=cohort.spec.config_source,
                    succeeded_at=datetime.fromisoformat(closed_at),
                    publication=_publication(row),
                ),
            )
    return tuple(
        CalibrationStatus(
            calibration_key=key,
            latest_attempt=latest_attempts.get(key),
            latest_success=latest_successes.get(key),
        )
        for key in calibration_keys
    )


def _cohort(row: sqlite3.Row) -> CalibrationCohort:
    try:
        cohort = CalibrationCohort.model_validate_json(_text(row, "cohort_json"))
    except ValidationError as error:
        raise CalibrationCohortStoreError(
            "invalid durable calibration cohort"
        ) from error
    if (
        _text(row, "stored_cohort_id") != cohort.cohort_id
        or _text(row, "stored_fanout_scope") != cohort.spec.fanout_scope
    ):
        raise CalibrationCohortStoreError(
            "durable calibration cohort query projection drifted"
        )
    return cohort


def _member(row: sqlite3.Row) -> CalibrationCohortMember:
    try:
        member = CalibrationCohortMember.model_validate_json(_text(row, "member_json"))
    except ValidationError as error:
        raise CalibrationCohortStoreError(
            "invalid durable calibration cohort member"
        ) from error
    if (
        _text(row, "stored_member_cohort_id") != member.cohort_id
        or _integer(row, "stored_member_index") != member.index
        or _text(row, "stored_member_id") != member.spec.member_id
        or _text(row, "stored_calibration_key") != member.spec.calibration_key
        or _text(row, "stored_procedure_run_id") != member.procedure_run_id
    ):
        raise CalibrationCohortStoreError(
            "durable calibration cohort member query projection drifted"
        )
    return member


def _run(row: sqlite3.Row) -> ProcedureRun:
    try:
        return ProcedureRun.model_validate_json(_text(row, "run_json"))
    except ValidationError as error:
        raise CalibrationCohortStoreError(
            "invalid durable calibration procedure run"
        ) from error


def _publication(row: sqlite3.Row) -> CalibrationSuccessPublication | None:
    publication_json = _optional_text(row, "publication_json")
    if publication_json is None:
        return None
    try:
        return CalibrationSuccessPublication.model_validate_json(publication_json)
    except ValidationError as error:
        raise CalibrationCohortStoreError(
            "invalid durable calibration success publication"
        ) from error


def _finalization(row: sqlite3.Row) -> CalibrationCohortFinalization:
    cohort = _cohort(row)
    policy = cohort.spec.automatic_publication
    if policy is None:
        raise CalibrationCohortStoreError(
            "durable calibration finalization has no pinned publication policy"
        )
    composition = policy.composition_policy
    if (
        _text(row, "spec_hash") != cohort.spec_hash
        or _text(row, "policy_id") != policy.id
        or _text(row, "policy_version") != policy.version
        or _text(row, "policy_fingerprint") != policy.fingerprint
        or _text(row, "policy_json") != policy.model_dump_json()
        or _text(row, "calibration_definition_id") != policy.calibration.id
        or _text(row, "calibration_definition_version") != policy.calibration.version
        or _text(row, "calibration_definition_fingerprint")
        != policy.calibration.fingerprint
        or _text(row, "composition_policy_id") != composition.id
        or _text(row, "composition_policy_version") != composition.version
        or _text(row, "composition_policy_fingerprint") != composition.fingerprint
        or _text(row, "base_entry_id") != cohort.spec.config_source.entry_id
        or not isinstance(cohort.spec.config_source.scope, WorkingPointCalibrationScope)
        or _text(row, "owner_workspace_id")
        != cohort.spec.config_source.scope.workspace_id
    ):
        raise CalibrationCohortStoreError(
            "durable calibration finalization identity drifted"
        )
    try:
        attention_at = _optional_datetime(row, "attention_required_at")
        attention_actor = _optional_text(row, "attention_actor")
        attention_reason = _optional_text(row, "attention_reason")
        failed_at = _optional_datetime(row, "failed_at")
        superseded_at = _optional_datetime(row, "superseded_at")
        supersession_json = _optional_text(row, "supersession_json")
        published_at = _optional_datetime(row, "published_at")
        operation_id = _optional_text(row, "publication_operation_id")
        return CalibrationCohortFinalization(
            cohort_id=cohort.cohort_id,
            spec_hash=cohort.spec_hash,
            policy=policy,
            base_config_source=cohort.spec.config_source,
            revision=_integer(row, "revision"),
            state=cast("CalibrationCohortFinalizationState", _text(row, "state")),
            attempt_count=_integer(row, "attempt_count"),
            created_at=_datetime(row, "created_at"),
            updated_at=_datetime(row, "updated_at"),
            ready_at=_optional_datetime(row, "ready_at"),
            available_at=_optional_datetime(row, "available_at"),
            attention=(
                None
                if attention_at is None
                or attention_actor is None
                or attention_reason is None
                else CalibrationPublicationAttention(
                    actor=attention_actor,
                    reason=attention_reason,
                    required_at=attention_at,
                )
            ),
            failure=(
                None
                if failed_at is None
                else CalibrationPublicationFailure(failed_at=failed_at)
            ),
            supersession=(
                None
                if superseded_at is None or supersession_json is None
                else CalibrationPublicationSupersession.model_validate_json(
                    supersession_json
                )
            ),
            publication=(
                None
                if published_at is None or operation_id is None
                else CalibrationPublicationCompletion(
                    operation_id=operation_id,
                    published_at=published_at,
                )
            ),
        )
    except ValidationError as error:
        raise CalibrationCohortStoreError(
            "invalid durable calibration publication finalization"
        ) from error


def _ready_item(row: sqlite3.Row) -> CalibrationPublicationReadyItem:
    cohort = _cohort(row)
    try:
        return CalibrationPublicationReadyItem(
            sequence=_integer(row, "sequence"),
            cohort=CalibrationCohortSummary.from_cohort(cohort),
            finalization=_finalization(row),
            enqueued_at=_datetime(row, "enqueued_at"),
        )
    except ValidationError as error:
        raise CalibrationCohortStoreError(
            "invalid durable calibration publication ready item"
        ) from error


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calibration cohort timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _datetime(row: sqlite3.Row, key: str) -> datetime:
    return datetime.fromisoformat(_text(row, key)).astimezone(UTC)


def _optional_datetime(row: sqlite3.Row, key: str) -> datetime | None:
    value = _optional_text(row, key)
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _all(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


def _text(row: sqlite3.Row, key: str) -> str:
    return cast("str", row[key])


def _optional_text(row: sqlite3.Row, key: str) -> str | None:
    return cast("str | None", row[key])


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
            "calibration publication cursor and through_sequence must be "
            "provided together"
        )
    if after is not None and through_sequence is not None and after >= through_sequence:
        raise ValueError(
            "calibration publication cursor must be below through_sequence"
        )


__all__ = [
    "CalibrationCohortConflict",
    "CalibrationCohortNotFound",
    "CalibrationCohortStoreError",
    "SQLiteCalibrationCohortStore",
    "StoredCalibrationCohortMemberPage",
    "StoredCalibrationCohortPage",
    "StoredCalibrationPublicationReadyPage",
]
