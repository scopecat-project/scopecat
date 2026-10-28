"""Narrow exit boundary over retained work and independent safety evidence."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from sqlite3 import Cursor
from threading import Lock
from typing import cast
from uuid import uuid4

from scopecat.automation.models import (
    ProcedureRun,
    ProcedureStepAttempt,
)
from scopecat.automation.wire import procedure_step_operation_id
from scopecat.daemon.calibration_tasks import CalibrationTaskRecord
from scopecat.daemon.exit import ExitReport, ExitWork

from scopecat_server.storage.sqlite.connection import SQLiteDatabase

from .background_rounds import BackgroundRounds
from .project_workers import ProjectProcedureWorkers


class ExitDrain:
    def __init__(self, sqlite: SQLiteDatabase) -> None:
        self.sqlite = sqlite
        self._publication_lock = Lock()
        self._publications: dict[str, str] = {}

    @contextmanager
    def analysis_publication(self, run_id: str) -> Generator[str]:
        identity = uuid4().hex
        with self._publication_lock:
            self._publications[identity] = run_id
        try:
            yield identity
        finally:
            with self._publication_lock:
                del self._publications[identity]

    def retained_publication(self, identity: str, run_id: str) -> bool:
        with self._publication_lock:
            return self._publications.get(identity) == run_id

    def deferred_procedure(self, identity: str) -> bool:
        with self.sqlite.read_connection() as connection:
            rows = _text_rows(
                connection.execute(
                    "SELECT run_json FROM procedure_runs WHERE procedure_run_id = ?",
                    (identity,),
                )
            )
            return bool(rows) and ProcedureRun.model_validate_json(
                rows[0][0]
            ).exit_scope in {"subsequent", "maintenance"}

    def live_effect(self, operation_id: str) -> bool:
        # The operation id identifies an existing effect. It does not grant a new
        # task or stage, and is never used as an authentication credential.
        with self.sqlite.read_transaction() as connection:
            rows = _text_rows(
                connection.execute(
                    "SELECT s.attempt_json, l.expires_at "
                    "FROM procedure_step_attempts s "
                    "JOIN procedure_leases l USING (procedure_run_id) "
                    "JOIN procedure_runs r USING (procedure_run_id) "
                    "WHERE s.state = 'running' AND r.state = 'leased'"
                )
            )
        for row in rows:
            step = ProcedureStepAttempt.model_validate_json(row[0])
            if procedure_step_operation_id(
                step.procedure_run_id, step.step_key
            ) == operation_id and datetime.fromisoformat(row[1]) > datetime.now(UTC):
                return True
        return False

    def report(
        self, workers: ProjectProcedureWorkers, rounds: BackgroundRounds, requests: int
    ) -> ExitReport:
        finishing: list[ExitWork] = []
        deferred: list[ExitWork] = []
        blockers: list[ExitWork] = []
        draining = rounds.draining
        with self.sqlite.read_transaction() as connection:
            procedures = [
                ProcedureRun.model_validate_json(row[0])
                for row in _text_rows(
                    connection.execute(
                        "SELECT run_json FROM procedure_runs WHERE state <> 'closed'"
                    )
                )
            ]
            leases = {
                row[0]
                for row in _text_rows(
                    connection.execute("SELECT procedure_run_id FROM procedure_leases")
                )
            }
            unsettled: dict[str, list[ProcedureStepAttempt]] = {}
            for row in _text_rows(
                connection.execute(
                    "SELECT s.attempt_json FROM procedure_step_attempts s "
                    "JOIN procedure_runs r USING (procedure_run_id) "
                    "WHERE r.state <> 'closed' "
                    "AND s.state IN ('running', 'waiting_for_input', "
                    "'attention_required') "
                    "AND NOT EXISTS (SELECT 1 FROM procedure_step_attempts newer "
                    "WHERE newer.procedure_run_id = s.procedure_run_id "
                    "AND newer.step_key = s.step_key AND newer.attempt > s.attempt)"
                )
            ):
                step = ProcedureStepAttempt.model_validate_json(row[0])
                unsettled.setdefault(step.procedure_run_id, []).append(step)
            safe_waits: set[str] = set()
            for run in procedures:
                identity = run.procedure_run_id
                steps = unsettled.get(identity, [])
                item = ExitWork(kind="procedure", identity=identity, reason=run.state)
                if run.exit_scope is None:
                    blockers.append(
                        item.model_copy(
                            update={
                                "reason": (
                                    "Legacy task has no declared exit "
                                    "scope; review required"
                                )
                            }
                        )
                    )
                elif run.state == "attention_required" or run.resource_wait is not None:
                    blockers.append(
                        item.model_copy(
                            update={
                                "reason": run.attention_reason
                                or "Waiting for resources is not a human checkpoint"
                            }
                        )
                    )
                elif run.state == "waiting_for_input":
                    reason = _checkpoint_blocker(run, steps, leases, workers)
                    if reason is None and draining:
                        safe_waits.add(identity)
                        deferred.append(
                            item.model_copy(
                                update={
                                    "reason": (
                                        "Exact question, inputs, "
                                        "source and prior results retained; "
                                        "still waiting for an answer"
                                    )
                                }
                            )
                        )
                    else:
                        blockers.append(
                            item.model_copy(
                                update={
                                    "reason": (
                                        reason
                                        or "Human checkpoint retained; enable exit wait"
                                    )
                                }
                            )
                        )
                elif run.state == "leased" and workers.process_exited(identity):
                    blockers.append(
                        item.model_copy(
                            update={
                                "reason": (
                                    "Worker exited without procedure closure; "
                                    "retained steps require review. Inspect the child "
                                    "run's saved data and interruption before explicit "
                                    "disposition; closing that run does not complete "
                                    "this procedure."
                                )
                            }
                        )
                    )
                elif (
                    worker := workers.snapshot(identity)
                ).management == "paused" or worker.failure is not None:
                    blockers.append(
                        item.model_copy(
                            update={
                                "reason": worker.failure.message
                                if worker.failure is not None
                                else "Task is paused; explicit review required"
                            }
                        )
                    )
                elif (
                    draining
                    and run.exit_scope in {"subsequent", "maintenance"}
                    and run.state == "ready"
                    and identity not in leases
                    and not steps
                    and workers.process_settled(identity)
                ):
                    deferred.append(
                        item.model_copy(
                            update={
                                "reason": (
                                    "Declared subsequent work; no new worker admitted"
                                )
                            }
                        )
                    )
                else:
                    finishing.append(item)
            for row in _text_rows(
                connection.execute(
                    "SELECT task_id, record_json FROM calibration_tasks WHERE "
                    "mode IN ('running', 'paused')"
                )
            ):
                task = CalibrationTaskRecord.model_validate_json(row[1])
                item = ExitWork(
                    kind="calibration_task", identity=row[0], reason=task.mode
                )
                child_ids = set(task.executions.values())
                if task.finalization_run_id:
                    child_ids.add(task.finalization_run_id)
                active_children = {
                    run.procedure_run_id for run in procedures
                } & child_ids
                if (
                    task.mode == "paused"
                    or task.exit_scope is None
                    or task.dispatch_errors
                    or task.finalization_error
                ):
                    blockers.append(
                        item.model_copy(
                            update={
                                "reason": (
                                    "Paused, failed or legacy task requires review"
                                )
                            }
                        )
                    )
                elif draining and (
                    task.exit_scope in {"subsequent", "maintenance"}
                    or (bool(active_children) and active_children <= safe_waits)
                ):
                    deferred.append(
                        item.model_copy(
                            update={
                                "reason": (
                                    "Remaining plan retained without "
                                    "cancellation or automatic answers"
                                )
                            }
                        )
                    )
                else:
                    finishing.append(item)
            for row in _text_rows(
                connection.execute(
                    "SELECT schedule_id FROM procedure_schedules "
                    "WHERE state = 'pending'"
                )
            ):
                item = ExitWork(
                    kind="schedule",
                    identity=row[0],
                    reason=(
                        "Plan retained; materialization waits until "
                        "normal admission resumes"
                    ),
                )
                (deferred if draining else finishing).append(item)
            for table, key, states in (
                (
                    "scheduler_runs",
                    "run_id",
                    "'queued', 'leased', 'attention_required'",
                ),
                ("instrument_sessions", "session_id", "'active', 'attention_required'"),
            ):
                for row in _text_rows(
                    connection.execute(
                        f"SELECT {key}, state, COALESCE(attention_reason, state) "  # noqa: S608 - fixed table literals
                        f"FROM {table} "
                        f"WHERE state IN ({states})"
                    )
                ):
                    item = ExitWork(
                        kind=table, identity=row[0], reason=row[2] or row[1]
                    )
                    (blockers if row[1] == "attention_required" else finishing).append(
                        item
                    )
            for row in _text_rows(
                connection.execute(
                    "SELECT resource_kind, resource_id, owner_id, status FROM "
                    "resource_claims"
                )
            ):
                blockers.append(
                    ExitWork(
                        kind="resource",
                        identity=f"{row[0]}:{row[1]}",
                        reason=(
                            f"{row[3]} resource owned by {row[2]}; "
                            f"disconnect is not proof of safe output"
                        ),
                    )
                )
            for row in _text_rows(
                connection.execute(
                    "SELECT id, state FROM analysis_follows WHERE state IN "
                    "('running', 'attention')"
                )
            ):
                item = ExitWork(
                    kind="analysis_follow",
                    identity=row[0],
                    reason="Unresolved background issue"
                    if row[1] == "attention"
                    else "Subscription retained; next round deferred",
                )
                (blockers if row[1] == "attention" else deferred).append(item)
        for identity in workers.unsettled_processes():
            finishing.append(
                ExitWork(
                    kind="worker",
                    identity=identity,
                    reason=(
                        "Registered worker has not exited; cleanup may still be running"
                    ),
                )
            )
        finishing.extend(_transient_work(requests, rounds.active))
        return ExitReport(
            draining=draining,
            finishing=tuple(finishing),
            deferred=tuple(deferred),
            blockers=tuple(blockers),
        )


def _transient_work(requests: int, active: int) -> list[ExitWork]:
    finishing: list[ExitWork] = []
    if requests:
        finishing.append(
            ExitWork(
                kind="requests",
                identity=str(requests),
                reason="Admitted writes are finishing",
            )
        )
    if active:
        finishing.append(
            ExitWork(
                kind="background",
                identity=str(active),
                reason="Admitted background rounds are finishing",
            )
        )
    return finishing


def _checkpoint_blocker(
    run: ProcedureRun,
    steps: list[ProcedureStepAttempt],
    leases: set[str],
    workers: ProjectProcedureWorkers,
) -> str | None:
    if run.source is None:
        return "No retained author source for this human checkpoint"
    if run.procedure_run_id in leases:
        return "Procedure worker lease is still present"
    if len(steps) != 1:
        return "Other latest steps remain unsettled at the human checkpoint"
    step = steps[0]
    if step.operation != "interpretation" or step.state != "waiting_for_input":
        return "Latest unsettled effect is not a durable human question"
    return workers.checkpoint_blocker(step)


def _text_rows(cursor: Cursor) -> list[tuple[str, ...]]:
    """These queries select non-null text columns (nullable reasons are coalesced)."""
    return cast("list[tuple[str, ...]]", cursor.fetchall())
