"""SQLite execution persistence backed by the shared project store."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import BaseModel, TypeAdapter
from pydantic_core import PydanticSerializationError
from scopecat.adaptive_domains import ResolvedDomainFragment
from scopecat.daemon.points import (
    AcceptedRunPointView,
    RunDomainDecisionCommand,
    RunDomainDecisionPage,
    RunDomainDecisionView,
    RunDomainEnqueueCommand,
    RunDomainQueueEntryView,
    RunDomainQueueView,
    RunPointPlanCloseCommand,
    RunPointPlanView,
)
from scopecat.daemon.wire import (
    RunDomainJobStatePage,
    RunDomainJobStateView,
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionItem,
    RunDomainJobTransitionPage,
    RunDomainJobTransitionView,
    RunRecoveryGroupPage,
    RunRecoveryGroupView,
)
from scopecat.records.execution import (
    DomainJobCheckpointTransition,
    DomainJobInvocationTransition,
    DomainJobTerminalTransition,
    DomainJobTransitionRecord,
    RecoveryGroupCompletion,
)
from scopecat.records.measurement import MeasurementDatasetSchema, MeasurementRecord
from scopecat.records.measurement_recording import (
    CANONICAL_MEASUREMENT_DATASET_REF,
    MeasurementDatasetAppend,
    MeasurementDatasetFragment,
    MeasurementDatasetHeader,
    MeasurementDatasetReceipt,
    MeasurementDatasetSeal,
    measurement_dataset_content_hash,
    measurement_fragment_content_hash,
)

from scopecat_server.storage.sqlite.object_store import ObjectStoreError, StoredObject
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


class ExecutionStateError(RuntimeError):
    """Execution state could not be read or committed safely."""


class ExecutionStateConflict(ExecutionStateError):
    """A write disagrees with already committed execution state."""


@dataclass(frozen=True, slots=True)
class PreparedExecutionRecord[TModel: BaseModel]:
    """Immutable object-store write prepared before a SQLite transaction."""

    durable: TModel
    ref: str
    stored: StoredObject


class SQLiteRunCoverage:
    """Persist the contiguous logical-point prefix completed by a run."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._run_id = run_id

    def read(self) -> int:
        with self._runs.sqlite.read_transaction() as connection:
            return self.read_in_transaction(connection)

    def read_in_transaction(self, connection: sqlite3.Connection) -> int:
        row = _one(
            connection.execute(
                """
                SELECT completed_point_count
                FROM execution_coverage
                WHERE run_id = ?
                """,
                (self._run_id,),
            )
        )
        return 0 if row is None else _integer(row, "completed_point_count")

    def advance_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        start_index: int,
        point_count: int,
    ) -> tuple[int, bool]:
        """Advance one contiguous range or accept an already covered retry."""

        if start_index < 0 or point_count < 1:
            raise ExecutionStateConflict("coverage range must be non-empty")
        completed = self.read_in_transaction(connection)
        end_index = start_index + point_count
        if end_index <= completed:
            return completed, False
        if start_index < completed:
            raise ExecutionStateConflict(
                "coverage range partially overlaps the completed prefix"
            )
        if start_index > completed:
            raise ExecutionStateConflict("coverage range is not the next prefix")
        if _measurement_header_row(connection, self._run_id) is not None:
            row = _one(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT point_index) AS count
                    FROM execution_measurement_records
                    WHERE run_id = ? AND point_index >= ? AND point_index < ?
                    """,
                    (self._run_id, start_index, end_index),
                )
            )
            assert row is not None
            if _integer(row, "count") != point_count:
                raise ExecutionStateConflict(
                    "coverage requires durably acquired measurement points"
                )
            # Exact recovery groups have already fixed their records. Publish
            # other completed points (e.g. adaptive output) using the same
            # unfixed-acquisition selection as terminal sealing.
            _finalize_measurement_projection(
                connection,
                self._run_id,
                start_index=start_index,
                end_index=end_index,
            )
        connection.execute(
            """
            INSERT INTO execution_coverage(run_id, completed_point_count)
            VALUES (?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                completed_point_count = excluded.completed_point_count
            """,
            (self._run_id, end_index),
        )
        return end_index, True


class SQLiteRecoveryGroups:
    """Persist exact recovery groups only after their outputs are publishable."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._run_id = run_id

    def read(
        self,
        *,
        limit: int,
        before: int | None = None,
    ) -> RunRecoveryGroupPage:
        with self._runs.sqlite.read_transaction() as connection:
            rows = _all(
                connection.execute(
                    """
                    SELECT sequence, run_id, segment_id, operation_id,
                           schedule_fingerprint, group_id,
                           completion_fingerprint, output_kind
                    FROM execution_recovery_groups
                    WHERE run_id = ? AND (? IS NULL OR sequence < ?)
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (self._run_id, before, before, limit + 1),
                )
            )
            selected = rows[:limit]
            items = tuple(
                self._view_in_transaction(connection, row) for row in reversed(selected)
            )
        return RunRecoveryGroupPage(
            run_id=self._run_id,
            items=items,
            next_cursor=(
                _integer(selected[-1], "sequence") if len(rows) > limit else None
            ),
        )

    def append_in_transaction(
        self,
        connection: sqlite3.Connection,
        groups: Sequence[RecoveryGroupCompletion],
        *,
        segment_id: str,
    ) -> tuple[RunRecoveryGroupView, ...]:
        """Commit one bounded idempotent batch under an existing lease fence."""

        if not groups:
            raise ExecutionStateConflict("recovery group batch must be non-empty")
        fingerprints = {group.schedule_fingerprint for group in groups}
        if len(fingerprints) != 1:
            raise ExecutionStateConflict(
                "recovery group batch references different schedules"
            )
        existing_schedule = _one(
            connection.execute(
                """
                SELECT schedule_fingerprint
                FROM execution_recovery_groups
                WHERE run_id = ?
                LIMIT 1
                """,
                (self._run_id,),
            )
        )
        schedule_fingerprint = next(iter(fingerprints))
        if (
            existing_schedule is not None
            and _text(existing_schedule, "schedule_fingerprint") != schedule_fingerprint
        ):
            raise ExecutionStateConflict(
                "recovery groups disagree with the run's durable schedule"
            )

        accepted: list[RunRecoveryGroupView] = []
        for group in groups:
            existing = _one(
                connection.execute(
                    """
                    SELECT sequence, run_id, segment_id, operation_id,
                           schedule_fingerprint, group_id,
                           completion_fingerprint, output_kind
                    FROM execution_recovery_groups
                    WHERE run_id = ? AND group_id = ?
                    """,
                    (self._run_id, group.group_id),
                )
            )
            if existing is not None:
                view = self._view_in_transaction(connection, existing)
                if view.completion != group:
                    raise ExecutionStateConflict(
                        "recovery group already has different completion evidence"
                    )
                accepted.append(view)
                continue

            acquisition_indices = self._validate_output_in_transaction(
                connection,
                group,
            )
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO execution_recovery_groups(
                        run_id, segment_id, operation_id,
                        schedule_fingerprint, group_id,
                        completion_fingerprint, output_kind
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._run_id,
                        segment_id,
                        group.operation_id,
                        group.schedule_fingerprint,
                        group.group_id,
                        group.completion_fingerprint,
                        group.output_kind,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO execution_recovery_group_points(
                        run_id, group_id, member_index,
                        point_index, record_content_hash
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    tuple(
                        (
                            self._run_id,
                            group.group_id,
                            member_index,
                            point_index,
                            (
                                None
                                if group.output_kind == "unrecorded"
                                else group.record_content_hashes[member_index]
                            ),
                        )
                        for member_index, point_index in enumerate(group.point_indices)
                    ),
                )
                if acquisition_indices:
                    projection_start = _measurement_projected_record_count(
                        connection,
                        self._run_id,
                    )
                    connection.executemany(
                        """
                        INSERT INTO execution_measurement_projection(
                            run_id, point_index, projection_index,
                            acquisition_index, group_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        tuple(
                            (
                                self._run_id,
                                point_index,
                                projection_start + member_index,
                                acquisition_index,
                                group.group_id,
                            )
                            for member_index, (
                                point_index,
                                acquisition_index,
                            ) in enumerate(
                                zip(
                                    group.point_indices,
                                    acquisition_indices,
                                    strict=True,
                                )
                            )
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise ExecutionStateConflict(
                    "recovery group conflicts with durable group identity or points"
                ) from error
            row = _one(
                connection.execute(
                    """
                    SELECT sequence, run_id, segment_id, operation_id,
                           schedule_fingerprint, group_id,
                           completion_fingerprint, output_kind
                    FROM execution_recovery_groups
                    WHERE sequence = ?
                    """,
                    (cursor.lastrowid,),
                )
            )
            assert row is not None
            accepted.append(self._view_in_transaction(connection, row))
        return tuple(accepted)

    def _validate_output_in_transaction(
        self,
        connection: sqlite3.Connection,
        group: RecoveryGroupCompletion,
    ) -> tuple[int, ...]:
        header = _measurement_header_row(connection, self._run_id)
        if group.output_kind == "unrecorded":
            if header is not None:
                raise ExecutionStateConflict(
                    "unrecorded recovery group belongs to a measurement run"
                )
            return ()
        if header is None:
            raise ExecutionStateConflict(
                "measurement recovery group requires a dataset header"
            )
        selected: list[int] = []
        for point_index, content_hash in zip(
            group.point_indices,
            group.record_content_hashes,
            strict=True,
        ):
            row = _one(
                connection.execute(
                    """
                    SELECT acquisition_index
                    FROM execution_measurement_records
                    WHERE run_id = ? AND point_index = ?
                      AND record_content_hash = ?
                    ORDER BY acquisition_index DESC
                    LIMIT 1
                    """,
                    (self._run_id, point_index, content_hash),
                )
            )
            if row is None:
                raise ExecutionStateConflict(
                    "recovery group measurements are not durably acquired"
                )
            selected.append(_integer(row, "acquisition_index"))
        return tuple(selected)

    def _view_in_transaction(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> RunRecoveryGroupView:
        point_rows = _all(
            connection.execute(
                """
                SELECT point_index, record_content_hash
                FROM execution_recovery_group_points
                WHERE run_id = ? AND group_id = ?
                ORDER BY member_index
                """,
                (self._run_id, _text(row, "group_id")),
            )
        )
        output_kind = cast(
            "Literal['unrecorded', 'measurement']",
            _text(row, "output_kind"),
        )
        completion = RecoveryGroupCompletion(
            schedule_fingerprint=_text(row, "schedule_fingerprint"),
            group_id=_text(row, "group_id"),
            point_indices=tuple(_integer(item, "point_index") for item in point_rows),
            output_kind=output_kind,
            record_content_hashes=(
                ()
                if output_kind == "unrecorded"
                else tuple(_text(item, "record_content_hash") for item in point_rows)
            ),
        )
        if completion.operation_id != _text(
            row, "operation_id"
        ) or completion.completion_fingerprint != _text(row, "completion_fingerprint"):
            raise ExecutionStateError("recovery group completion identity is corrupt")
        return RunRecoveryGroupView(
            sequence=_integer(row, "sequence"),
            run_id=_text(row, "run_id"),
            segment_id=_text(row, "segment_id"),
            completion=completion,
        )


_DOMAIN_JOB_TRANSITION: TypeAdapter[DomainJobTransitionRecord] = TypeAdapter(
    DomainJobTransitionRecord
)
_STRING_LIST = TypeAdapter(list[str])


class SQLiteDomainJobTransitions:
    """Persist invocation, pending, and terminal transitions in execution order."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._run_id = run_id

    def read(
        self,
        *,
        limit: int,
        before: int | None = None,
    ) -> RunDomainJobTransitionPage:
        with self._runs.sqlite.read_transaction() as connection:
            rows = _all(
                connection.execute(
                    """
                    SELECT sequence, run_id, logical_compute_node_id,
                           point_ordinals_json, transition_json
                    FROM execution_domain_job_transitions
                    WHERE run_id = ? AND (? IS NULL OR sequence < ?)
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (self._run_id, before, before, limit + 1),
                )
            )
        selected = rows[:limit]
        return RunDomainJobTransitionPage(
            run_id=self._run_id,
            items=tuple(_domain_job_transition_view(row) for row in reversed(selected)),
            next_cursor=(
                _integer(selected[-1], "sequence") if len(rows) > limit else None
            ),
        )

    def read_current(
        self,
        *,
        limit: int,
        before: int | None = None,
    ) -> RunDomainJobStatePage:
        with self._runs.sqlite.read_transaction() as connection:
            return self.read_current_in_transaction(
                connection,
                limit=limit,
                before=before,
            )

    def read_current_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int,
        before: int | None = None,
    ) -> RunDomainJobStatePage:
        rows = _all(
            connection.execute(
                """
                WITH current AS (
                    SELECT execution_key,
                           MAX(sequence) AS latest_sequence,
                           COUNT(*) AS transition_count
                    FROM execution_domain_job_transitions
                    WHERE run_id = ?
                    GROUP BY execution_key
                )
                SELECT latest.run_id, latest.point_ordinals_json,
                       latest.transition_json, current.latest_sequence,
                       current.transition_count,
                       invocation.sequence AS invocation_sequence,
                       invocation.transition_json AS invocation_json
                FROM current
                JOIN execution_domain_job_transitions AS latest
                  ON latest.run_id = ?
                 AND latest.execution_key = current.execution_key
                 AND latest.sequence = current.latest_sequence
                JOIN execution_domain_job_transitions AS invocation
                  ON invocation.run_id = latest.run_id
                 AND invocation.execution_key = latest.execution_key
                 AND invocation.transition_kind = 'invocation'
                WHERE (? IS NULL OR invocation.sequence < ?)
                ORDER BY invocation.sequence DESC
                LIMIT ?
                """,
                (self._run_id, self._run_id, before, before, limit + 1),
            )
        )
        selected = rows[:limit]
        return RunDomainJobStatePage(
            run_id=self._run_id,
            items=tuple(_domain_job_state_view(row) for row in reversed(selected)),
            next_cursor=(
                _integer(selected[-1], "invocation_sequence")
                if len(rows) > limit
                else None
            ),
        )

    def commit_in_transaction(
        self,
        connection: sqlite3.Connection,
        command: RunDomainJobTransitionItem,
    ) -> tuple[RunDomainJobTransitionView, bool]:
        transition = command.transition
        if isinstance(transition, DomainJobInvocationTransition) and (
            transition.execution_id.run_id != self._run_id
            or transition.execution_id.logical_compute_node_id
            != command.logical_compute_node_id
        ):
            raise ExecutionStateConflict(
                "domain job invocation identity does not match its run or node"
            )
        if isinstance(transition, DomainJobCheckpointTransition):
            existing = _one(
                connection.execute(
                    """
                    SELECT sequence, run_id, logical_compute_node_id,
                           point_ordinals_json, transition_json
                    FROM execution_domain_job_transitions
                    WHERE run_id = ? AND execution_key = ?
                      AND transition_kind = 'checkpoint' AND revision = ?
                    """,
                    (
                        self._run_id,
                        transition.execution_key,
                        transition.checkpoint.revision,
                    ),
                )
            )
        elif isinstance(transition, DomainJobInvocationTransition):
            existing = _one(
                connection.execute(
                    """
                    SELECT sequence, run_id, logical_compute_node_id,
                           point_ordinals_json, transition_json
                    FROM execution_domain_job_transitions
                    WHERE run_id = ? AND execution_key = ?
                      AND transition_kind = 'invocation'
                    """,
                    (self._run_id, transition.execution_key),
                )
            )
        else:
            existing = _one(
                connection.execute(
                    """
                    SELECT sequence, run_id, logical_compute_node_id,
                           point_ordinals_json, transition_json
                    FROM execution_domain_job_transitions
                    WHERE run_id = ? AND execution_key = ?
                      AND transition_kind = 'terminal'
                    """,
                    (self._run_id, transition.execution_key),
                )
            )
        if existing is not None:
            view = _domain_job_transition_view(existing)
            if (
                view.logical_compute_node_id != command.logical_compute_node_id
                or view.point_ordinals != command.point_ordinals
                or view.transition != transition
            ):
                raise ExecutionStateConflict(
                    "domain job transition conflicts with durable state"
                )
            return view, False

        latest = _one(
            connection.execute(
                """
                SELECT sequence, run_id, logical_compute_node_id,
                       point_ordinals_json, transition_json
                FROM execution_domain_job_transitions
                WHERE run_id = ? AND execution_key = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (self._run_id, transition.execution_key),
            )
        )
        if latest is None:
            if not isinstance(transition, DomainJobInvocationTransition):
                raise ExecutionStateConflict(
                    "domain job transition requires a durable invocation"
                )
        else:
            previous = _domain_job_transition_view(latest)
            if (
                previous.logical_compute_node_id != command.logical_compute_node_id
                or previous.point_ordinals != command.point_ordinals
            ):
                raise ExecutionStateConflict(
                    "domain job transition changed its durable identity"
                )
            if isinstance(previous.transition, DomainJobTerminalTransition):
                raise ExecutionStateConflict(
                    "domain job transition follows its terminal receipt"
                )
            if isinstance(transition, DomainJobInvocationTransition):
                raise ExecutionStateConflict(
                    "domain job invocation is not the first transition"
                )
            if isinstance(transition, DomainJobCheckpointTransition) and isinstance(
                previous.transition, DomainJobCheckpointTransition
            ):
                previous_checkpoint = previous.transition.checkpoint
                checkpoint = transition.checkpoint
                if checkpoint.job_id != previous_checkpoint.job_id:
                    raise ExecutionStateConflict(
                        "domain job checkpoint changed its job identity"
                    )
                if checkpoint.revision <= previous_checkpoint.revision:
                    raise ExecutionStateConflict(
                        "domain job checkpoint revision is not increasing"
                    )

        job_id = None
        revision = None
        if isinstance(transition, DomainJobCheckpointTransition):
            job_id = transition.checkpoint.job_id
            revision = transition.checkpoint.revision

        inserted = connection.execute(
            """
            INSERT INTO execution_domain_job_transitions(
                run_id, logical_compute_node_id, execution_key, transition_kind,
                job_id, revision, point_ordinals_json, transition_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._run_id,
                command.logical_compute_node_id,
                transition.execution_key,
                transition.kind,
                job_id,
                revision,
                json.dumps(command.point_ordinals, separators=(",", ":")),
                _DOMAIN_JOB_TRANSITION.dump_json(transition).decode(),
            ),
        )
        return (
            RunDomainJobTransitionView(
                sequence=cast("int", inserted.lastrowid),
                run_id=self._run_id,
                logical_compute_node_id=command.logical_compute_node_id,
                point_ordinals=command.point_ordinals,
                transition=transition,
            ),
            True,
        )

    def commit_batch_in_transaction(
        self,
        connection: sqlite3.Connection,
        command: RunDomainJobTransitionBatchCommand,
    ) -> tuple[RunDomainJobTransitionView, ...]:
        return tuple(
            self.commit_in_transaction(connection, item)[0] for item in command.items
        )


class SQLiteRunPointLedger:
    """Persist dynamic point decisions and the final plan closure."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._run_id = run_id

    def read(self) -> RunPointPlanView | None:
        with self._runs.sqlite.read_transaction() as connection:
            return self.read_in_transaction(connection)

    def read_in_transaction(
        self,
        connection: sqlite3.Connection,
    ) -> RunPointPlanView | None:
        row = _one(
            connection.execute(
                """
                SELECT initial_point_count, accepted_point_count, point_limit,
                       plan_closed, stop_reason,
                       (
                           SELECT COUNT(*)
                           FROM execution_domain_decisions AS decisions
                           WHERE decisions.run_id = execution_point_plans.run_id
                       ) AS decision_count,
                       (
                           SELECT COUNT(*)
                           FROM execution_domain_decisions AS decisions
                           WHERE decisions.run_id = execution_point_plans.run_id
                             AND json_extract(
                                 decisions.decision_json,
                                 '$.proposal.source'
                             ) = 'optimizer'
                       ) AS optimizer_attempt_count,
                       (
                           SELECT COUNT(*)
                           FROM execution_domain_queue AS requests
                           WHERE requests.run_id = execution_point_plans.run_id
                       ) AS operator_request_count
                FROM execution_point_plans
                WHERE run_id = ?
                """,
                (self._run_id,),
            )
        )
        if row is None:
            return None
        return RunPointPlanView(
            run_id=self._run_id,
            initial_point_count=_integer(row, "initial_point_count"),
            accepted_point_count=_integer(row, "accepted_point_count"),
            point_limit=_integer(row, "point_limit"),
            decision_count=_integer(row, "decision_count"),
            optimizer_attempt_count=_integer(row, "optimizer_attempt_count"),
            operator_request_count=_integer(row, "operator_request_count"),
            plan_closed=bool(_integer(row, "plan_closed")),
            stop_reason=(
                None if row["stop_reason"] is None else _text(row, "stop_reason")
            ),
        )

    def queue(self) -> RunDomainQueueView:
        with self._runs.sqlite.read_transaction() as connection:
            return self.queue_in_transaction(connection)

    def queue_in_transaction(
        self,
        connection: sqlite3.Connection,
    ) -> RunDomainQueueView:
        rows = _all(
            connection.execute(
                """
                SELECT entry_json
                FROM execution_domain_queue
                WHERE run_id = ?
                ORDER BY queue_index
                """,
                (self._run_id,),
            )
        )
        return RunDomainQueueView(
            run_id=self._run_id,
            items=tuple(
                RunDomainQueueEntryView.model_validate_json(_text(row, "entry_json"))
                for row in rows
            ),
        )

    def decisions(
        self,
        *,
        limit: int,
        before: int | None = None,
    ) -> RunDomainDecisionPage:
        with self._runs.sqlite.read_transaction() as connection:
            rows = _all(
                connection.execute(
                    """
                    SELECT proposal_index, decision_json
                    FROM execution_domain_decisions
                    WHERE run_id = ?
                      AND (? IS NULL OR proposal_index < ?)
                    ORDER BY proposal_index DESC
                    LIMIT ?
                    """,
                    (self._run_id, before, before, limit + 1),
                )
            )
        selected = rows[:limit]
        items = tuple(
            RunDomainDecisionView.model_validate_json(_text(row, "decision_json"))
            for row in reversed(selected)
        )
        return RunDomainDecisionPage(
            run_id=self._run_id,
            items=items,
            next_cursor=(
                _integer(selected[-1], "proposal_index") if len(rows) > limit else None
            ),
        )

    def next_pending(self) -> RunDomainQueueEntryView | None:
        with self._runs.sqlite.read_transaction() as connection:
            row = _one(
                connection.execute(
                    """
                    SELECT entry_json
                    FROM execution_domain_queue
                    WHERE run_id = ? AND status = 'pending'
                    ORDER BY queue_index
                    LIMIT 1
                    """,
                    (self._run_id,),
                )
            )
        return (
            None
            if row is None
            else RunDomainQueueEntryView.model_validate_json(_text(row, "entry_json"))
        )

    def enqueue_in_transaction(
        self,
        connection: sqlite3.Connection,
        command: RunDomainEnqueueCommand,
        *,
        resolved_fragment: ResolvedDomainFragment,
        region_count: int,
    ) -> tuple[RunDomainQueueEntryView, bool]:
        request = command.domain_request(
            resolved_fragment,
            region_count=region_count,
        )
        existing = _one(
            connection.execute(
                """
                SELECT entry_json
                FROM execution_domain_queue
                WHERE run_id = ? AND request_id = ?
                """,
                (self._run_id, command.request_id),
            )
        )
        if existing is not None:
            entry = RunDomainQueueEntryView.model_validate_json(
                _text(existing, "entry_json")
            )
            if entry.request != request:
                raise ExecutionStateConflict(
                    "operator domain request conflicts with durable state"
                )
            return entry, False
        plan = self.read_in_transaction(connection)
        if plan is None:
            raise ExecutionStateConflict("point plan is not initialized")
        if plan.plan_closed:
            raise ExecutionStateConflict("point plan is already closed")
        pending_count = sum(
            entry.request.request().total_point_count
            for entry in self.queue_in_transaction(connection).items
            if entry.status == "pending"
        )
        if (
            pending_count + request.request().total_point_count
            > plan.point_limit - plan.accepted_point_count
        ):
            raise ExecutionStateConflict("domain queue exceeds the remaining budget")
        queue_index = _scalar_int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM execution_domain_queue
                WHERE run_id = ?
                """,
                (self._run_id,),
            )
        )
        entry = RunDomainQueueEntryView(
            queue_index=queue_index,
            occurred_at=datetime.now(UTC),
            request=request,
            status="pending",
        )
        connection.execute(
            """
            INSERT INTO execution_domain_queue(
                run_id, queue_index, request_id, status, entry_json
            )
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (
                self._run_id,
                entry.queue_index,
                entry.request.request_id,
                entry.model_dump_json(),
            ),
        )
        return entry, True

    def initialize_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        initial_point_count: int,
        point_limit: int,
        plan_closed: bool,
    ) -> RunPointPlanView:
        existing = _one(
            connection.execute(
                """
                SELECT initialize_operation_id, initial_point_count, point_limit,
                       plan_closed
                FROM execution_point_plans
                WHERE run_id = ?
                """,
                (self._run_id,),
            )
        )
        if existing is not None:
            if (
                _text(existing, "initialize_operation_id") != operation_id
                or _integer(existing, "initial_point_count") != initial_point_count
                or _integer(existing, "point_limit") != point_limit
                or bool(_integer(existing, "plan_closed")) != plan_closed
            ):
                raise ExecutionStateConflict(
                    "point-plan initialization conflicts with durable state"
                )
            view = self.read_in_transaction(connection)
            assert view is not None
            return view
        stop_operation_id = f"{operation_id}.static" if plan_closed else None
        stop_reason = "static point plan" if plan_closed else None
        connection.execute(
            """
            INSERT INTO execution_point_plans(
                run_id, initialize_operation_id, initial_point_count,
                accepted_point_count, point_limit, plan_closed,
                stop_operation_id, stop_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._run_id,
                operation_id,
                initial_point_count,
                initial_point_count,
                point_limit,
                int(plan_closed),
                stop_operation_id,
                stop_reason,
            ),
        )
        view = self.read_in_transaction(connection)
        assert view is not None
        return view

    def append_decision_in_transaction(
        self,
        connection: sqlite3.Connection,
        command: RunDomainDecisionCommand,
    ) -> RunDomainDecisionView:
        existing = _one(
            connection.execute(
                """
                SELECT run_id, decision_json
                FROM execution_domain_decisions
                WHERE run_id = ? AND operation_id = ?
                """,
                (self._run_id, command.operation_id),
            )
        )
        if existing is not None:
            decision = RunDomainDecisionView.model_validate_json(
                _text(existing, "decision_json")
            )
            if (
                decision.proposal != command.proposal
                or decision.outcome != command.outcome
                or decision.accepted_point_start
                != _accepted_point_start(command.accepted_points)
                or decision.accepted_point_count != len(command.accepted_points)
                or self._decision_points_in_transaction(
                    connection,
                    command.operation_id,
                )
                != command.accepted_points
                or decision.reason != command.reason
                or decision.operator_request_id != command.operator_request_id
            ):
                raise ExecutionStateConflict(
                    "point decision operation conflicts with durable state"
                )
            return decision
        plan = self.read_in_transaction(connection)
        if plan is None:
            raise ExecutionStateConflict("point plan is not initialized")
        if plan.plan_closed:
            raise ExecutionStateConflict("point plan is already closed")
        queued_entry = self._queued_entry_for_decision_in_transaction(
            connection,
            command,
        )
        accepted_points = command.accepted_points
        if command.outcome == "accepted":
            if plan.accepted_point_count + len(accepted_points) > plan.point_limit:
                raise ExecutionStateConflict("domain decision exceeds the point budget")
            if tuple(point.point_index for point in accepted_points) != tuple(
                range(
                    plan.accepted_point_count,
                    plan.accepted_point_count + len(accepted_points),
                )
            ):
                raise ExecutionStateConflict(
                    "accepted domain points must extend the durable point prefix"
                )
            if any(
                point.domain_proposal_fingerprint
                != command.proposal.proposal_fingerprint
                for point in accepted_points
            ):
                raise ExecutionStateConflict(
                    "accepted points do not match their domain proposal"
                )
        decision = RunDomainDecisionView(
            operation_id=command.operation_id,
            operator_request_id=command.operator_request_id,
            proposal_index=plan.decision_count,
            occurred_at=datetime.now(UTC),
            proposal=command.proposal,
            outcome=command.outcome,
            accepted_point_start=_accepted_point_start(accepted_points),
            accepted_point_count=len(accepted_points),
            reason=command.reason,
        )
        connection.execute(
            """
            INSERT INTO execution_domain_decisions(
                run_id, proposal_index, operation_id, decision_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                self._run_id,
                decision.proposal_index,
                decision.operation_id,
                decision.model_dump_json(),
            ),
        )
        if accepted_points:
            connection.executemany(
                """
                INSERT INTO execution_run_points(
                    run_id, point_index, decision_operation_id, point_json
                )
                VALUES (?, ?, ?, ?)
                """,
                tuple(
                    (
                        self._run_id,
                        accepted_point.point_index,
                        decision.operation_id,
                        accepted_point.model_dump_json(),
                    )
                    for accepted_point in accepted_points
                ),
            )
            connection.execute(
                """
                UPDATE execution_point_plans
                SET accepted_point_count = accepted_point_count + ?
                WHERE run_id = ?
                """,
                (len(accepted_points), self._run_id),
            )
        if queued_entry is not None:
            self._resolve_queue_entry_in_transaction(
                connection,
                queued_entry,
                decision,
            )
        return decision

    def _decision_points_in_transaction(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> tuple[AcceptedRunPointView, ...]:
        rows = _all(
            connection.execute(
                """
                SELECT point_json
                FROM execution_run_points
                WHERE run_id = ? AND decision_operation_id = ?
                ORDER BY point_index
                """,
                (self._run_id, operation_id),
            )
        )
        return tuple(
            AcceptedRunPointView.model_validate_json(_text(row, "point_json"))
            for row in rows
        )

    def _queued_entry_for_decision_in_transaction(
        self,
        connection: sqlite3.Connection,
        command: RunDomainDecisionCommand,
    ) -> RunDomainQueueEntryView | None:
        if command.operator_request_id is None:
            return None
        row = _one(
            connection.execute(
                """
                SELECT entry_json
                FROM execution_domain_queue
                WHERE run_id = ? AND request_id = ?
                """,
                (self._run_id, command.operator_request_id),
            )
        )
        if row is None:
            raise ExecutionStateConflict("operator domain request does not exist")
        entry = RunDomainQueueEntryView.model_validate_json(_text(row, "entry_json"))
        if entry.status != "pending":
            raise ExecutionStateConflict("operator domain request is already resolved")
        if entry.request.fragment != command.proposal.fragment:
            raise ExecutionStateConflict(
                "domain proposal does not match its operator request"
            )
        return entry

    def _resolve_queue_entry_in_transaction(
        self,
        connection: sqlite3.Connection,
        entry: RunDomainQueueEntryView,
        decision: RunDomainDecisionView,
    ) -> None:
        resolved = RunDomainQueueEntryView(
            queue_index=entry.queue_index,
            occurred_at=entry.occurred_at,
            request=entry.request,
            status=decision.outcome,
            decision_operation_id=decision.operation_id,
            accepted_point_start=decision.accepted_point_start,
            accepted_point_count=decision.accepted_point_count,
            reason=decision.reason,
        )
        connection.execute(
            """
            UPDATE execution_domain_queue
            SET status = ?, decision_operation_id = ?, entry_json = ?
            WHERE run_id = ? AND request_id = ?
            """,
            (
                resolved.status,
                resolved.decision_operation_id,
                resolved.model_dump_json(),
                self._run_id,
                resolved.request.request_id,
            ),
        )

    def close_in_transaction(
        self,
        connection: sqlite3.Connection,
        command: RunPointPlanCloseCommand,
        *,
        completed_point_count: int,
    ) -> RunPointPlanView:
        row = _one(
            connection.execute(
                """
                SELECT stop_operation_id, stop_reason
                FROM execution_point_plans
                WHERE run_id = ?
                """,
                (self._run_id,),
            )
        )
        if row is None:
            raise ExecutionStateConflict("point plan is not initialized")
        if row["stop_operation_id"] is not None:
            if (
                _text(row, "stop_operation_id") != command.operation_id
                or _text(row, "stop_reason") != command.reason
            ):
                raise ExecutionStateConflict(
                    "point-plan closure conflicts with durable state"
                )
            view = self.read_in_transaction(connection)
            assert view is not None
            return view
        plan = self.read_in_transaction(connection)
        assert plan is not None
        if (
            command.based_on_completed_point_count != completed_point_count
            or completed_point_count != plan.accepted_point_count
        ):
            raise ExecutionStateConflict(
                "point plan can close only at its durable accepted prefix"
            )
        connection.execute(
            """
            UPDATE execution_point_plans
            SET plan_closed = 1, stop_operation_id = ?, stop_reason = ?
            WHERE run_id = ?
            """,
            (command.operation_id, command.reason, self._run_id),
        )
        self._cancel_pending_queue_in_transaction(
            connection,
            reason=f"point plan closed: {command.reason}",
        )
        view = self.read_in_transaction(connection)
        assert view is not None
        return view

    def abandon_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        reason: str,
    ) -> RunPointPlanView | None:
        """Close an unfinished adaptive plan after a non-successful terminal result."""

        plan = self.read_in_transaction(connection)
        if plan is None or plan.plan_closed:
            return plan
        connection.execute(
            """
            UPDATE execution_point_plans
            SET plan_closed = 1, stop_operation_id = ?, stop_reason = ?
            WHERE run_id = ?
            """,
            (operation_id, reason, self._run_id),
        )
        self._cancel_pending_queue_in_transaction(
            connection,
            reason=f"point plan abandoned: {reason}",
        )
        view = self.read_in_transaction(connection)
        assert view is not None
        return view

    def _cancel_pending_queue_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        reason: str,
    ) -> None:
        pending = tuple(
            entry
            for entry in self.queue_in_transaction(connection).items
            if entry.status == "pending"
        )
        for entry in pending:
            cancelled = RunDomainQueueEntryView(
                queue_index=entry.queue_index,
                occurred_at=entry.occurred_at,
                request=entry.request,
                status="cancelled",
                reason=reason,
            )
            connection.execute(
                """
                UPDATE execution_domain_queue
                SET status = 'cancelled', entry_json = ?
                WHERE run_id = ? AND request_id = ?
                """,
                (
                    cancelled.model_dump_json(),
                    self._run_id,
                    cancelled.request.request_id,
                ),
            )


class SQLiteMeasurementDatasetRepository:
    """Append and seal canonical measurement ranges with database CAS."""

    def __init__(self, runs: SQLiteRunRepository, *, run_id: str) -> None:
        self._runs = runs
        self._run_id = run_id
        self._dataset_schema: MeasurementDatasetSchema | None = None
        self._dataset_schema_hash: str | None = None

    def prepare_header(
        self,
        header: MeasurementDatasetHeader,
    ) -> PreparedExecutionRecord[MeasurementDatasetHeader]:
        """Publish the immutable dataset contract before entering a transaction."""

        durable = header
        if durable.run_id != self._run_id:
            raise ExecutionStateConflict(
                "measurement run_id does not match its execution repository"
            )
        ref = f"{CANONICAL_MEASUREMENT_DATASET_REF}/header.json"
        return PreparedExecutionRecord(
            durable=durable,
            ref=ref,
            stored=_store_model(self._runs, durable),
        )

    def header_prepared_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedExecutionRecord[MeasurementDatasetHeader],
        *,
        segment_id: str,
    ) -> tuple[MeasurementDatasetReceipt, bool, bool]:
        """Publish the run header and initialize one segment-owned fragment."""

        durable = prepared.durable
        try:
            existing = _one(
                connection.execute(
                    """
                    SELECT operation_id, content_hash
                    FROM execution_measurement_headers
                    WHERE run_id = ?
                    """,
                    (self._run_id,),
                )
            )
            header_created = existing is None
            if existing is not None:
                if (
                    _text(existing, "operation_id") != durable.operation_id
                    or _text(existing, "content_hash") != durable.content_hash
                ):
                    raise ExecutionStateConflict(
                        "measurement dataset header already has different content"
                    )
            else:
                if _measurement_rows(connection, self._run_id) or _dataset_sealed(
                    connection, self._run_id
                ):
                    raise ExecutionStateConflict(
                        "measurement dataset content exists without its header"
                    )
                _publish_ref(connection, self._run_id, prepared.ref, prepared.stored)
                connection.execute(
                    """
                    INSERT INTO execution_measurement_headers(
                        run_id, operation_id, content_hash,
                        contract_fingerprint, expected_record_count,
                        record_count_limit, ref
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._run_id,
                        durable.operation_id,
                        durable.content_hash,
                        durable.recording_contract_fingerprint,
                        durable.expected_record_count,
                        durable.record_count_limit,
                        prepared.ref,
                    ),
                )
            fragment_created = _ensure_measurement_fragment(
                connection,
                run_id=self._run_id,
                segment_id=segment_id,
                header_content_hash=durable.content_hash,
            )
            self._remember_measurement_schema(durable.dataset_schema)
            return (
                _header_receipt(
                    durable,
                    acquisition_record_count=_measurement_record_count(
                        connection,
                        self._run_id,
                    ),
                ),
                header_created,
                fragment_created,
            )
        except ExecutionStateError:
            raise
        except Exception as error:
            raise ExecutionStateError(
                f"failed to initialize measurement dataset: {error}"
            ) from error

    def prepare_append(
        self,
        append: MeasurementDatasetAppend,
        *,
        dataset_schema: MeasurementDatasetSchema | None = None,
    ) -> PreparedExecutionRecord[MeasurementDatasetAppend]:
        """Publish immutable append content before entering the write transaction."""

        durable = append
        if durable.run_id != self._run_id:
            raise ExecutionStateConflict(
                "measurement run_id does not match its execution repository"
            )
        ref = (
            f"{CANONICAL_MEASUREMENT_DATASET_REF}/chunks/"
            f"{durable.acquisition_start:020d}.arrow"
        )
        schema_assets = (
            self._remember_measurement_schema(dataset_schema)
            if dataset_schema is not None
            else self._measurement_schema_assets()
        )
        if schema_assets is None:
            raise ExecutionStateConflict(
                "measurement dataset append requires a registered schema"
            )
        selected_schema, selected_schema_hash = schema_assets
        return PreparedExecutionRecord(
            durable=durable,
            ref=ref,
            stored=_store_measurement_append(
                self._runs,
                durable,
                dataset_schema=selected_schema,
                dataset_schema_hash=selected_schema_hash,
            ),
        )

    def append_prepared_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedExecutionRecord[MeasurementDatasetAppend],
        *,
        segment_id: str,
    ) -> tuple[MeasurementDatasetReceipt, bool]:
        """Publish prepared append metadata in an existing transaction."""

        durable = prepared.durable
        ref = prepared.ref
        try:
            existing = _one(
                connection.execute(
                    """
                    SELECT segment_id, operation_id, content_hash, ref
                    FROM execution_measurement_appends
                    WHERE run_id = ? AND acquisition_start = ?
                    """,
                    (self._run_id, durable.acquisition_start),
                )
            )
            if existing is not None:
                if (
                    _text(existing, "segment_id") != segment_id
                    or _text(existing, "operation_id") != durable.operation_id
                    or _text(existing, "content_hash") != durable.content_hash
                ):
                    raise ExecutionStateConflict(
                        "measurement dataset append already has different content"
                    )
                return (
                    MeasurementDatasetReceipt(
                        operation_id=_text(existing, "operation_id"),
                        dataset_content_hash=_text(existing, "content_hash"),
                        acquisition_record_count=(
                            durable.acquisition_start + len(durable.records)
                        ),
                    ),
                    False,
                )
            if _dataset_sealed(connection, self._run_id):
                raise ExecutionStateConflict("measurement dataset is already sealed")
            fragment = _measurement_fragment_row(connection, segment_id)
            if fragment is None or _text(fragment, "run_id") != self._run_id:
                raise ExecutionStateConflict(
                    "measurement dataset append requires its execution fragment"
                )
            header = _measurement_header_row(connection, self._run_id)
            if header is None:
                raise ExecutionStateConflict(
                    "measurement dataset append requires a header"
                )
            if _text(header, "content_hash") != durable.header_content_hash:
                raise ExecutionStateConflict(
                    "measurement dataset append references a different header"
                )
            record_count = _measurement_record_count(connection, self._run_id)
            if durable.acquisition_start != record_count:
                raise ExecutionStateConflict(
                    "measurement append is not the next acquisition-log range"
                )
            record_count_limit = _integer(header, "record_count_limit")
            if any(
                record.point_index >= record_count_limit for record in durable.records
            ):
                raise ExecutionStateConflict(
                    "measurement append references a point outside its declared limit"
                )
            _publish_ref(connection, self._run_id, ref, prepared.stored)
            connection.execute(
                """
                INSERT INTO execution_measurement_appends(
                    run_id, segment_id, acquisition_start, operation_id,
                    content_hash, header_content_hash,
                    record_content_hashes_json, record_count,
                    ref
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._run_id,
                    segment_id,
                    durable.acquisition_start,
                    durable.operation_id,
                    durable.content_hash,
                    durable.header_content_hash,
                    json.dumps(durable.record_content_hashes),
                    len(durable.records),
                    ref,
                ),
            )
            connection.executemany(
                """
                INSERT INTO execution_measurement_records(
                    run_id, acquisition_index, acquisition_start,
                    row_offset, point_index, record_content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    (
                        self._run_id,
                        durable.acquisition_start + row_offset,
                        durable.acquisition_start,
                        row_offset,
                        record.point_index,
                        durable.record_content_hashes[row_offset],
                    )
                    for row_offset, record in enumerate(durable.records)
                ),
            )
            return _append_receipt(durable), True
        except ExecutionStateError:
            raise
        except Exception as error:
            raise ExecutionStateError(
                f"failed to append measurement dataset: {error}"
            ) from error

    def prepare_seal(
        self,
        seal: MeasurementDatasetSeal,
    ) -> MeasurementDatasetSeal:
        """Validate seal content before entering the write transaction."""

        durable = seal
        if durable.run_id != self._run_id:
            raise ExecutionStateConflict(
                "measurement run_id does not match its execution repository"
            )
        return durable

    def seal_prepared_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: MeasurementDatasetSeal,
        *,
        segment_id: str,
    ) -> tuple[MeasurementDatasetReceipt, bool]:
        """Publish prepared seal metadata in an existing transaction."""

        durable = prepared
        try:

            def commit_prepared() -> tuple[MeasurementDatasetReceipt, bool]:
                existing = _one(
                    connection.execute(
                        """
                        SELECT
                            segment_id,
                            operation_id,
                            content_hash,
                            dataset_content_hash
                        FROM execution_measurement_seals
                        WHERE run_id = ?
                        """,
                        (self._run_id,),
                    )
                )
                if existing is not None:
                    if (
                        _text(existing, "segment_id") != segment_id
                        or _text(existing, "operation_id") != durable.operation_id
                        or _text(existing, "content_hash") != durable.content_hash
                    ):
                        raise ExecutionStateConflict(
                            "measurement dataset seal already has different content"
                        )
                    return (
                        MeasurementDatasetReceipt(
                            operation_id=_text(existing, "operation_id"),
                            dataset_content_hash=_text(
                                existing,
                                "dataset_content_hash",
                            ),
                            acquisition_record_count=_measurement_record_count(
                                connection,
                                self._run_id,
                            ),
                        ),
                        False,
                    )
                header = _measurement_header_row(connection, self._run_id)
                if header is None:
                    raise ExecutionStateConflict(
                        "measurement dataset seal requires a header"
                    )
                if _text(header, "content_hash") != durable.header_content_hash:
                    raise ExecutionStateConflict(
                        "measurement dataset seal references a different header"
                    )
                fragment = _measurement_fragment_row(connection, segment_id)
                if fragment is None or _text(fragment, "run_id") != self._run_id:
                    raise ExecutionStateConflict(
                        "measurement dataset seal requires its execution fragment"
                    )
                if durable.record_count > _integer(header, "record_count_limit"):
                    raise ExecutionStateConflict(
                        "measurement dataset seal exceeds its declared point count"
                    )
                _finalize_measurement_projection(
                    connection,
                    self._run_id,
                )
                if (
                    _measurement_projected_record_count(
                        connection,
                        self._run_id,
                    )
                    != durable.record_count
                ):
                    raise ExecutionStateConflict(
                        "measurement logical projection is incomplete"
                    )
                fragment_record_hashes = tuple(
                    record_hash
                    for row in _measurement_fragment_rows(connection, segment_id)
                    for record_hash in cast(
                        "list[str]",
                        json.loads(_text(row, "record_content_hashes_json")),
                    )
                )
                if len(fragment_record_hashes) != durable.fragment_record_count:
                    raise ExecutionStateConflict(
                        "measurement fragment record count does not match appends"
                    )
                actual_fragment_hash = measurement_fragment_content_hash(
                    header_content_hash=durable.header_content_hash,
                    record_content_hashes=fragment_record_hashes,
                )
                if actual_fragment_hash != durable.fragment_content_hash:
                    raise ExecutionStateConflict(
                        "measurement fragment seal content hash does not match appends"
                    )
                projected_rows = _all(
                    connection.execute(
                        """
                        SELECT projection.point_index, record.record_content_hash
                        FROM execution_measurement_projection AS projection
                        JOIN execution_measurement_records AS record
                          ON record.run_id = projection.run_id
                         AND record.acquisition_index = projection.acquisition_index
                        WHERE projection.run_id = ?
                        ORDER BY projection.point_index
                        """,
                        (self._run_id,),
                    )
                )
                if tuple(
                    _integer(row, "point_index") for row in projected_rows
                ) != tuple(range(durable.record_count)):
                    raise ExecutionStateConflict(
                        "measurement logical projection is not canonical coverage"
                    )
                dataset_content_hash = measurement_dataset_content_hash(
                    header_content_hash=durable.header_content_hash,
                    record_content_hashes=tuple(
                        _text(row, "record_content_hash") for row in projected_rows
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO execution_measurement_seals(
                        run_id, segment_id, operation_id, content_hash,
                        dataset_content_hash
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self._run_id,
                        segment_id,
                        durable.operation_id,
                        durable.content_hash,
                        dataset_content_hash,
                    ),
                )
                return (
                    MeasurementDatasetReceipt(
                        operation_id=durable.operation_id,
                        dataset_content_hash=dataset_content_hash,
                        acquisition_record_count=_measurement_record_count(
                            connection,
                            self._run_id,
                        ),
                    ),
                    True,
                )

            return commit_prepared()
        except ExecutionStateError:
            raise
        except Exception as error:
            raise ExecutionStateError(
                f"failed to seal measurement dataset: {error}"
            ) from error

    def measurements(
        self,
    ) -> tuple[MeasurementRecord, ...]:
        try:
            record_count = self.measurement_record_count()
            records, _next_offset, _schema, _snapshot = self.measurement_page(
                limit=max(1, record_count),
                offset=0,
                snapshot_size=record_count,
                include_schema=False,
            )
            return records
        except Exception as error:
            raise ExecutionStateError(
                f"failed to read measurement dataset: {error}"
            ) from error

    def measurement_fragments(self) -> tuple[MeasurementDatasetFragment, ...]:
        """Project segment-owned ranges without opening Arrow append blobs."""

        try:
            with self._runs.sqlite.read_connection() as connection:
                fragments = _all(
                    connection.execute(
                        """
                        SELECT
                            fragment.segment_id,
                            fragment.run_id,
                            fragment.header_content_hash,
                            fragment.acquisition_start,
                            seal.dataset_content_hash
                        FROM execution_measurement_fragments AS fragment
                        JOIN run_execution_segments AS segment
                          ON segment.segment_id = fragment.segment_id
                        LEFT JOIN execution_measurement_seals AS seal
                          ON seal.segment_id = fragment.segment_id
                        WHERE fragment.run_id = ?
                        ORDER BY segment.ordinal
                        """,
                        (self._run_id,),
                    )
                )
                result: list[MeasurementDatasetFragment] = []
                for fragment in fragments:
                    record_hashes = tuple(
                        record_hash
                        for row in _measurement_fragment_rows(
                            connection,
                            _text(fragment, "segment_id"),
                        )
                        for record_hash in cast(
                            "list[str]",
                            json.loads(_text(row, "record_content_hashes_json")),
                        )
                    )
                    acquisition_start = _integer(
                        fragment,
                        "acquisition_start",
                    )
                    header_content_hash = _text(fragment, "header_content_hash")
                    result.append(
                        MeasurementDatasetFragment(
                            segment_id=_text(fragment, "segment_id"),
                            run_id=self._run_id,
                            header_content_hash=header_content_hash,
                            acquisition_start=acquisition_start,
                            record_count=len(record_hashes),
                            fragment_content_hash=measurement_fragment_content_hash(
                                header_content_hash=header_content_hash,
                                record_content_hashes=record_hashes,
                            ),
                            dataset_content_hash=(
                                None
                                if fragment["dataset_content_hash"] is None
                                else _text(fragment, "dataset_content_hash")
                            ),
                        )
                    )
                return tuple(result)
        except Exception as error:
            raise ExecutionStateError(
                f"failed to read measurement fragments: {error}"
            ) from error

    def measurement_schema(self) -> MeasurementDatasetSchema | None:
        """Read the canonical schema without loading any measurement append."""

        if self._dataset_schema is not None:
            return self._dataset_schema
        try:
            with self._runs.sqlite.read_connection() as connection:
                header_row = _measurement_header_row(connection, self._run_id)
            if header_row is None:
                return None
            dataset_schema = self._runs.read_model(
                self._run_id,
                _text(header_row, "ref"),
                MeasurementDatasetHeader,
            ).dataset_schema
            self._remember_measurement_schema(dataset_schema)
            return dataset_schema
        except Exception as error:
            raise ExecutionStateError(
                f"failed to read measurement dataset schema: {error}"
            ) from error

    def measurement_preview(
        self,
        *,
        limit: int,
    ) -> tuple[
        tuple[MeasurementRecord, ...], int | None, MeasurementDatasetSchema | None
    ]:
        """Read durable output, including the retained tail beyond recovery coverage.

        Fixed group projections win over later acquisitions. An unprojected
        point uses its latest acquired record, without publishing a recovery
        proof or changing the stable logical-page watermark.
        """
        with self._runs.sqlite.read_connection() as connection:
            rows = _all(
                connection.execute(
                    """
                    SELECT record.point_index, record.acquisition_index,
                           record.row_offset, append.acquisition_start, append.ref
                    FROM execution_measurement_records AS record
                    JOIN execution_measurement_appends AS append
                      ON append.run_id = record.run_id
                     AND append.acquisition_start = record.acquisition_start
                    WHERE record.run_id = ?
                      AND record.acquisition_index = COALESCE(
                        (SELECT projection.acquisition_index
                         FROM execution_measurement_projection AS projection
                         WHERE projection.run_id = record.run_id
                           AND projection.point_index = record.point_index),
                        (SELECT MAX(acquired.acquisition_index)
                         FROM execution_measurement_records AS acquired
                         WHERE acquired.run_id = record.run_id
                           AND acquired.point_index = record.point_index)
                      )
                    ORDER BY record.point_index
                    LIMIT ?
                    """,
                    (self._run_id, limit + 1),
                )
            )
        return (
            self._records_from_locations(rows[:limit], variable_ids=None),
            limit if len(rows) > limit else None,
            self.measurement_schema(),
        )

    def measurement_page(
        self,
        *,
        limit: int,
        offset: int,
        snapshot_size: int | None = None,
        include_schema: bool = True,
        variable_ids: Sequence[str] | None = None,
    ) -> tuple[
        tuple[MeasurementRecord, ...],
        int | None,
        MeasurementDatasetSchema | None,
        int,
    ]:
        """Read one logical-point page against a projection-stable snapshot.

        The first call selects the current projection watermark; subsequent
        calls pass it back as ``snapshot_size`` so concurrent appends never
        extend the read. Logical rows are ordered by ``point_index`` while the
        immutable Arrow blobs retain physical acquisition order.
        """

        try:
            with self._runs.sqlite.read_connection() as connection:
                total = _measurement_projected_record_count(
                    connection,
                    self._run_id,
                )
                selected_size = total if snapshot_size is None else snapshot_size
                if selected_size > total:
                    raise ValueError(
                        "measurement snapshot is larger than the available dataset"
                    )
                if offset > selected_size:
                    raise ValueError("measurement page offset exceeds its snapshot")
                rows = _all(
                    connection.execute(
                        """
                        SELECT projection.point_index,
                               projection.acquisition_index,
                               record.row_offset,
                               append.acquisition_start,
                               append.ref
                        FROM execution_measurement_projection AS projection
                        JOIN execution_measurement_records AS record
                          ON record.run_id = projection.run_id
                         AND record.acquisition_index = projection.acquisition_index
                        JOIN execution_measurement_appends AS append
                          ON append.run_id = record.run_id
                         AND append.acquisition_start = record.acquisition_start
                        WHERE projection.run_id = ?
                          AND projection.projection_index < ?
                        ORDER BY projection.point_index
                        LIMIT ? OFFSET ?
                        """,
                        (self._run_id, selected_size, limit, offset),
                    )
                )
            items = self._records_from_locations(rows, variable_ids=variable_ids)
            next_offset = (
                offset + len(items) if offset + len(items) < selected_size else None
            )
            return (
                items,
                next_offset,
                self.measurement_schema() if include_schema else None,
                selected_size,
            )
        except Exception as error:
            raise ExecutionStateError(
                f"failed to read measurement dataset page: {error}"
            ) from error

    def measurement_record_count(self) -> int:
        """Read the current durable point-row count without opening append blobs."""

        try:
            with self._runs.sqlite.read_connection() as connection:
                return _measurement_projected_record_count(connection, self._run_id)
        except Exception as error:
            raise ExecutionStateError(
                f"failed to read measurement dataset size: {error}"
            ) from error

    def acquisition_record_count(self) -> int:
        """Read physical acquisition-log rows, including retries and orphans."""

        try:
            with self._runs.sqlite.read_connection() as connection:
                return _measurement_record_count(connection, self._run_id)
        except Exception as error:
            raise ExecutionStateError(
                f"failed to read measurement acquisition-log size: {error}"
            ) from error

    def measurement_records_at(
        self,
        point_indices: tuple[int, ...],
        *,
        variable_ids: Sequence[str] | None = None,
    ) -> tuple[MeasurementRecord, ...]:
        """Read selected logical points from their acquired Arrow rows."""

        selected = tuple(sorted(set(point_indices)))
        if not selected:
            return ()
        try:
            with self._runs.sqlite.read_connection() as connection:
                rows = _all(
                    connection.execute(
                        """
                        SELECT projection.point_index,
                               projection.acquisition_index,
                               record.row_offset,
                               append.acquisition_start,
                               append.ref
                        FROM execution_measurement_projection AS projection
                        JOIN execution_measurement_records AS record
                          ON record.run_id = projection.run_id
                         AND record.acquisition_index = projection.acquisition_index
                        JOIN execution_measurement_appends AS append
                          ON append.run_id = record.run_id
                         AND append.acquisition_start = record.acquisition_start
                        WHERE projection.run_id = ?
                          AND projection.point_index IN (
                              SELECT value FROM json_each(?)
                          )
                        ORDER BY projection.point_index
                        """,
                        (self._run_id, json.dumps(selected)),
                    )
                )
            records = self._records_from_locations(rows, variable_ids=variable_ids)
            records_by_index = {record.point_index: record for record in records}
            return tuple(
                records_by_index[point_index]
                for point_index in point_indices
                if point_index in records_by_index
            )
        except Exception as error:
            raise ExecutionStateError(
                f"failed to read selected measurement records: {error}"
            ) from error

    def _records_from_locations(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        variable_ids: Sequence[str] | None,
    ) -> tuple[MeasurementRecord, ...]:
        from scopecat.measurements.recording_arrow import (
            decode_measurement_record_indices,
        )

        if not rows:
            return ()
        schema_assets = self._measurement_schema_assets()
        if schema_assets is None:
            return ()
        dataset_schema, dataset_schema_hash = schema_assets
        grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
        for row in rows:
            key = (_integer(row, "acquisition_start"), _text(row, "ref"))
            grouped.setdefault(key, []).append(row)
        records_by_acquisition: dict[int, MeasurementRecord] = {}
        for (_acquisition_start, ref), selected_rows in grouped.items():
            decoded = decode_measurement_record_indices(
                self._runs.read_bytes(self._run_id, ref),
                dataset_schema,
                tuple(_integer(row, "row_offset") for row in selected_rows),
                variable_ids=variable_ids,
                dataset_schema_hash=dataset_schema_hash,
            )
            records_by_acquisition.update(
                zip(
                    (_integer(row, "acquisition_index") for row in selected_rows),
                    decoded,
                    strict=True,
                )
            )
        return tuple(
            records_by_acquisition[_integer(row, "acquisition_index")] for row in rows
        )

    def _measurement_schema_assets(
        self,
    ) -> tuple[MeasurementDatasetSchema, str] | None:
        dataset_schema = self.measurement_schema()
        if dataset_schema is None:
            return None
        assert self._dataset_schema_hash is not None
        return dataset_schema, self._dataset_schema_hash

    def _remember_measurement_schema(
        self,
        dataset_schema: MeasurementDatasetSchema,
    ) -> tuple[MeasurementDatasetSchema, str]:
        if dataset_schema != self._dataset_schema:
            from scopecat.measurements.recording_arrow import (
                measurement_dataset_schema_hash,
            )

            self._dataset_schema = dataset_schema
            self._dataset_schema_hash = measurement_dataset_schema_hash(dataset_schema)
        assert self._dataset_schema_hash is not None
        return dataset_schema, self._dataset_schema_hash


def _header_receipt(
    header: MeasurementDatasetHeader,
    *,
    acquisition_record_count: int,
) -> MeasurementDatasetReceipt:
    return MeasurementDatasetReceipt(
        operation_id=header.operation_id,
        dataset_content_hash=header.content_hash,
        acquisition_record_count=acquisition_record_count,
    )


def _append_receipt(
    append: MeasurementDatasetAppend,
) -> MeasurementDatasetReceipt:
    return MeasurementDatasetReceipt(
        operation_id=append.operation_id,
        dataset_content_hash=append.content_hash,
        acquisition_record_count=(append.acquisition_start + len(append.records)),
    )


def _dataset_sealed(
    connection: sqlite3.Connection,
    run_id: str,
) -> bool:
    return (
        _one(
            connection.execute(
                """
                SELECT 1 AS sealed FROM execution_measurement_seals
                WHERE run_id = ?
                """,
                (run_id,),
            )
        )
        is not None
    )


def _measurement_rows(
    connection: sqlite3.Connection,
    run_id: str,
) -> list[sqlite3.Row]:
    return _all(
        connection.execute(
            """
            SELECT * FROM execution_measurement_appends
            WHERE run_id = ?
            ORDER BY acquisition_start
            """,
            (run_id,),
        )
    )


def _measurement_fragment_rows(
    connection: sqlite3.Connection,
    segment_id: str,
) -> list[sqlite3.Row]:
    return _all(
        connection.execute(
            """
            SELECT * FROM execution_measurement_appends
            WHERE segment_id = ?
            ORDER BY acquisition_start
            """,
            (segment_id,),
        )
    )


def _measurement_fragment_row(
    connection: sqlite3.Connection,
    segment_id: str,
) -> sqlite3.Row | None:
    return _one(
        connection.execute(
            """
            SELECT * FROM execution_measurement_fragments
            WHERE segment_id = ?
            """,
            (segment_id,),
        )
    )


def _ensure_measurement_fragment(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    segment_id: str,
    header_content_hash: str,
) -> bool:
    existing = _measurement_fragment_row(connection, segment_id)
    if existing is not None:
        if (
            _text(existing, "run_id") != run_id
            or _text(existing, "header_content_hash") != header_content_hash
        ):
            raise ExecutionStateConflict(
                "measurement fragment already has different content"
            )
        return False
    segment = _one(
        connection.execute(
            """
            SELECT run_id
            FROM run_execution_segments
            WHERE segment_id = ?
            """,
            (segment_id,),
        )
    )
    if segment is None or _text(segment, "run_id") != run_id:
        raise ExecutionStateConflict(
            "measurement fragment requires its execution segment"
        )
    acquisition_start = _measurement_record_count(connection, run_id)
    connection.execute(
        """
        INSERT INTO execution_measurement_fragments(
            segment_id, run_id, header_content_hash, acquisition_start
        )
        VALUES (?, ?, ?, ?)
        """,
        (segment_id, run_id, header_content_hash, acquisition_start),
    )
    return True


def _measurement_header_row(
    connection: sqlite3.Connection,
    run_id: str,
) -> sqlite3.Row | None:
    return _one(
        connection.execute(
            """
            SELECT * FROM execution_measurement_headers
            WHERE run_id = ?
            """,
            (run_id,),
        )
    )


def _measurement_record_count(
    connection: sqlite3.Connection,
    run_id: str,
) -> int:
    row = _one(
        connection.execute(
            """
            SELECT acquisition_start, record_count
            FROM execution_measurement_appends
            WHERE run_id = ?
            ORDER BY acquisition_start DESC
            LIMIT 1
            """,
            (run_id,),
        )
    )
    return (
        0
        if row is None
        else _integer(row, "acquisition_start") + _integer(row, "record_count")
    )


def _measurement_projected_record_count(
    connection: sqlite3.Connection,
    run_id: str,
) -> int:
    row = _one(
        connection.execute(
            """
            SELECT projection_index
            FROM execution_measurement_projection
            WHERE run_id = ?
            ORDER BY projection_index DESC
            LIMIT 1
            """,
            (run_id,),
        )
    )
    return 0 if row is None else _integer(row, "projection_index") + 1


def _finalize_measurement_projection(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    start_index: int = 0,
    end_index: int | None = None,
) -> None:
    """Select the latest acquisition for points not fixed by recovery groups."""

    rows = _all(
        connection.execute(
            """
            SELECT
                record.point_index,
                MAX(record.acquisition_index) AS acquisition_index
            FROM execution_measurement_records AS record
            LEFT JOIN execution_measurement_projection AS projection
              ON projection.run_id = record.run_id
             AND projection.point_index = record.point_index
            WHERE record.run_id = ? AND projection.point_index IS NULL
              AND record.point_index >= ?
              AND (? IS NULL OR record.point_index < ?)
            GROUP BY record.point_index
            ORDER BY record.point_index
            """,
            (run_id, start_index, end_index, end_index),
        )
    )
    if not rows:
        return
    projection_start = _measurement_projected_record_count(connection, run_id)
    connection.executemany(
        """
        INSERT INTO execution_measurement_projection(
            run_id, point_index, projection_index,
            acquisition_index, group_id
        )
        VALUES (?, ?, ?, ?, NULL)
        """,
        tuple(
            (
                run_id,
                _integer(row, "point_index"),
                projection_start + offset,
                _integer(row, "acquisition_index"),
            )
            for offset, row in enumerate(rows)
        ),
    )


def _store_model(runs: SQLiteRunRepository, model: BaseModel) -> StoredObject:
    try:
        content = (
            json.dumps(
                model.model_dump(mode="json"),
                allow_nan=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        return runs.objects.put(content)
    except (
        ObjectStoreError,
        PydanticSerializationError,
        TypeError,
        ValueError,
    ) as error:
        raise ExecutionStateError(
            f"execution record is not durably serializable: {error}"
        ) from error


def _store_measurement_append(
    runs: SQLiteRunRepository,
    append: MeasurementDatasetAppend,
    *,
    dataset_schema: MeasurementDatasetSchema,
    dataset_schema_hash: str,
) -> StoredObject:
    from scopecat.measurements.recording_arrow import (
        MeasurementArrowCodecError,
        encode_measurement_append,
    )

    try:
        return runs.objects.put(
            encode_measurement_append(
                append,
                dataset_schema,
                dataset_schema_hash=dataset_schema_hash,
            )
        )
    except (MeasurementArrowCodecError, ObjectStoreError) as error:
        raise ExecutionStateError(
            f"measurement append is not durably serializable: {error}"
        ) from error


def _publish_ref(
    connection: sqlite3.Connection,
    run_id: str,
    ref: str,
    stored: StoredObject,
) -> None:
    connection.execute(
        """
        INSERT INTO run_repository_refs(run_id, ref, digest)
        VALUES (?, ?, ?)
        ON CONFLICT(run_id, ref) DO UPDATE SET
            digest = excluded.digest
        """,
        (run_id, ref, stored.digest),
    )


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _scalar_int(cursor: sqlite3.Cursor) -> int:
    row = _one(cursor)
    assert row is not None
    return cast("int", row[0])


def _all(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


def _text(row: sqlite3.Row, column: str) -> str:
    return cast("str", row[column])


def _integer(row: sqlite3.Row, column: str) -> int:
    return cast("int", row[column])


def _string_tuple(row: sqlite3.Row, column: str) -> tuple[str, ...]:
    try:
        value = _STRING_LIST.validate_json(_text(row, column))
    except ValueError as error:
        raise ExecutionStateError(f"invalid string tuple in {column}") from error
    return tuple(value)


def _domain_job_transition_view(row: sqlite3.Row) -> RunDomainJobTransitionView:
    return RunDomainJobTransitionView(
        sequence=_integer(row, "sequence"),
        run_id=_text(row, "run_id"),
        logical_compute_node_id=_text(row, "logical_compute_node_id"),
        point_ordinals=tuple(
            cast("list[int]", json.loads(_text(row, "point_ordinals_json")))
        ),
        transition=_DOMAIN_JOB_TRANSITION.validate_json(_text(row, "transition_json")),
    )


def _domain_job_state_view(row: sqlite3.Row) -> RunDomainJobStateView:
    invocation = _DOMAIN_JOB_TRANSITION.validate_json(_text(row, "invocation_json"))
    if not isinstance(invocation, DomainJobInvocationTransition):
        raise ExecutionStateError("domain job state is missing its invocation")
    latest = _DOMAIN_JOB_TRANSITION.validate_json(_text(row, "transition_json"))
    state = cast(
        "Literal['invocation_unknown', 'pending', 'terminal']",
        {
            "invocation": "invocation_unknown",
            "checkpoint": "pending",
            "terminal": "terminal",
        }[latest.kind],
    )
    return RunDomainJobStateView(
        run_id=_text(row, "run_id"),
        invocation=invocation,
        point_ordinals=tuple(
            cast("list[int]", json.loads(_text(row, "point_ordinals_json")))
        ),
        state=state,
        invocation_sequence=_integer(row, "invocation_sequence"),
        latest_sequence=_integer(row, "latest_sequence"),
        transition_count=_integer(row, "transition_count"),
        latest_transition=latest,
    )


def _accepted_point_start(points: tuple[AcceptedRunPointView, ...]) -> int | None:
    return None if not points else points[0].point_index
