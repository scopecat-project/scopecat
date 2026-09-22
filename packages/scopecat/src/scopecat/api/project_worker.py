"""Project-owned resident automation orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Literal, Protocol, cast
from uuid import uuid4

import httpx2

from scopecat.api.procedure_planner import ProcedurePlannerCycleResult
from scopecat.api.procedures import ProcedureHandle
from scopecat.automation import (
    ProcedureControlError,
    ProcedureLeaseLostError,
    ProcedureRun,
    ProcedureRunnablePage,
    ProcedureSchedule,
    ProcedureScheduleDuePage,
)
from scopecat.daemon.client import DaemonClientError, DaemonConflictError

_DEFAULT_BATCH_LIMIT = 50
_DEFAULT_BACKOFF_INITIAL_SECONDS = 0.25
_DEFAULT_BACKOFF_MAX_SECONDS = 10.0
_TRANSIENT_CLIENT_STATUSES = frozenset({408, 425, 429})

_LOGGER = logging.getLogger(__name__)


class _ProjectAutomationOperations(Protocol):
    """High-level durable operations consumed by project automation."""

    def list_due_schedules(
        self,
        *,
        limit: int = 50,
        cursor: int | None = None,
        through_sequence: int | None = None,
    ) -> ProcedureScheduleDuePage: ...

    def get_schedule(self, schedule_id: str) -> ProcedureSchedule: ...

    def materialize_schedule(
        self,
        schedule_id: str,
        *,
        expected_revision: int,
    ) -> ProcedureSchedule: ...

    def list_runnable(self, *, limit: int = 50) -> ProcedureRunnablePage: ...

    def resume_snapshot(
        self,
        run: ProcedureRun,
        *,
        worker_id: str | None = None,
        should_yield: Callable[[], bool] | None = None,
    ) -> ProcedureHandle: ...


class _ProcedureIntervalPlanner(Protocol):
    """Project-side latest-only planner invoked before due discovery."""

    def cycle(self, stop: Event | None = None) -> ProcedurePlannerCycleResult: ...


@dataclass(frozen=True, slots=True)
class ScheduleMaterializationCycleResult:
    """Bounded due-schedule materialization outcomes."""

    discovered: int
    materialized: int
    conflicts: int
    failures: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class ProcedureDispatchCycleResult:
    """Bounded runnable-procedure dispatch outcomes."""

    discovered: int
    dispatched: int
    failures: int
    conflicts: int
    lease_conflicts: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class ProjectAutomationCycleResult:
    """Exact phase outcomes observed during one project automation cycle."""

    intervals: ProcedurePlannerCycleResult
    schedules: ScheduleMaterializationCycleResult
    procedures: ProcedureDispatchCycleResult

    @property
    def failure_count(self) -> int:
        """Count deterministic failures and drift requiring operator review."""

        return (
            self.intervals.failures
            + self.intervals.drifted_schedules
            + self.schedules.failures
            + self.procedures.failures
            + self.procedures.conflicts
        )

    @property
    def needs_review(self) -> bool:
        return self.failure_count > 0

    @property
    def benign_conflicts(self) -> int:
        """Count reconciled or retryable concurrency races across all phases."""

        return self.schedules.conflicts + self.procedures.lease_conflicts

    @property
    def has_more(self) -> bool:
        return (
            self.intervals.has_more
            or self.schedules.has_more
            or self.procedures.has_more
        )


type _ScheduleOutcome = Literal["materialized", "conflict", "failure"]


class ProjectAutomationWorker:
    """Plan intervals and dispatch durable procedures and schedules."""

    __slots__ = (
        "_backoff_initial_seconds",
        "_backoff_max_seconds",
        "_due_traversal",
        "_operations",
        "_planner",
        "_runnable_limit",
        "_schedule_limit",
        "_worker_id",
    )

    def __init__(
        self,
        operations: _ProjectAutomationOperations,
        *,
        planner: _ProcedureIntervalPlanner | None = None,
        worker_id: str | None = None,
        schedule_limit: int = _DEFAULT_BATCH_LIMIT,
        runnable_limit: int = _DEFAULT_BATCH_LIMIT,
        backoff_initial_seconds: float = _DEFAULT_BACKOFF_INITIAL_SECONDS,
        backoff_max_seconds: float = _DEFAULT_BACKOFF_MAX_SECONDS,
    ) -> None:
        if not 1 <= schedule_limit <= 200:
            raise ValueError("procedure schedule batch limit must be between 1 and 200")
        if not 1 <= runnable_limit <= 200:
            raise ValueError("runnable procedure batch limit must be between 1 and 200")
        if backoff_initial_seconds <= 0:
            raise ValueError("automation worker initial backoff must be positive")
        if backoff_max_seconds < backoff_initial_seconds:
            raise ValueError(
                "automation worker maximum backoff must not be below "
                "its initial backoff"
            )
        selected_worker_id = worker_id or f"project-automation-{uuid4().hex}"
        if not selected_worker_id.strip():
            raise ValueError("automation worker id must be non-empty")

        self._operations = operations
        self._planner = planner
        self._worker_id = selected_worker_id
        self._schedule_limit = schedule_limit
        self._runnable_limit = runnable_limit
        self._backoff_initial_seconds = backoff_initial_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._due_traversal: tuple[int, int] | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def cycle(self, stop: Event | None = None) -> ProjectAutomationCycleResult:
        """Plan intervals, materialize schedules and dispatch bounded work."""

        planning = _empty_planner_cycle()
        schedules = _empty_schedule_cycle()
        procedures = _empty_procedure_cycle()
        if (stop is None or not stop.is_set()) and self._planner is not None:
            planning = self._planner.cycle(stop)
        if stop is None or not stop.is_set():
            schedules = self._materialize_due(stop)
        if stop is None or not stop.is_set():
            procedures = self._dispatch_runnable(stop)
        return ProjectAutomationCycleResult(
            intervals=planning,
            schedules=schedules,
            procedures=procedures,
        )

    def _materialize_due(
        self,
        stop: Event | None,
    ) -> ScheduleMaterializationCycleResult:
        cursor, through_sequence = self._due_traversal or (None, None)
        page = self._operations.list_due_schedules(
            limit=self._schedule_limit,
            cursor=cursor,
            through_sequence=through_sequence,
        )
        outcomes: list[_ScheduleOutcome] = []
        for schedule in page.items:
            if stop is not None and stop.is_set():
                break
            outcomes.append(self._materialize_one(schedule))
        if stop is None or not stop.is_set():
            self._due_traversal = (
                None
                if page.next_cursor is None
                else (page.next_cursor, cast("int", page.through_sequence))
            )
        return ScheduleMaterializationCycleResult(
            discovered=len(page.items),
            materialized=outcomes.count("materialized"),
            conflicts=outcomes.count("conflict"),
            failures=outcomes.count("failure"),
            has_more=(page.next_cursor is not None or len(outcomes) < len(page.items)),
        )

    def _materialize_one(self, schedule: ProcedureSchedule) -> _ScheduleOutcome:
        try:
            self._operations.materialize_schedule(
                schedule.schedule_id,
                expected_revision=schedule.revision,
            )
        except DaemonConflictError:
            try:
                observed = self._operations.get_schedule(schedule.schedule_id)
            except Exception as error:
                if _is_deterministic_client_error(error):
                    return "failure"
                raise
            if observed.state != "pending" or observed.revision != schedule.revision:
                return "conflict"
            return "failure"
        except (DaemonClientError, httpx2.HTTPError) as error:
            if _is_deterministic_client_error(error):
                return "failure"
            raise
        return "materialized"

    def _dispatch_runnable(
        self,
        stop: Event | None,
    ) -> ProcedureDispatchCycleResult:
        page = self._operations.list_runnable(limit=self._runnable_limit)
        dispatched = 0
        failures = 0
        conflicts = 0
        lease_conflicts = 0
        processed = 0
        for run in page.items:
            if stop is not None and stop.is_set():
                break
            processed += 1
            try:
                self._operations.resume_snapshot(
                    run,
                    worker_id=self._worker_id,
                    should_yield=(None if stop is None else stop.is_set),
                )
            except ProcedureControlError as error:
                if error.operation == "acquire_procedure_worker_lease":
                    if _error_status(error) == 409:
                        lease_conflicts += 1
                        continue
                    raise
                if _is_deterministic_client_error(error):
                    conflicts += 1
                    continue
                raise
            except ProcedureLeaseLostError as error:
                if _error_status(error) == 409:
                    lease_conflicts += 1
                    continue
                if _is_deterministic_client_error(error):
                    conflicts += 1
                    continue
                raise
            except Exception:
                failures += 1
                _LOGGER.exception(
                    "procedure %s failed after acquiring worker authority",
                    run.procedure_run_id,
                )
            else:
                dispatched += 1
        return ProcedureDispatchCycleResult(
            discovered=len(page.items),
            dispatched=dispatched,
            failures=failures,
            conflicts=conflicts,
            lease_conflicts=lease_conflicts,
            has_more=(page.has_more or processed < len(page.items)),
        )

    def run_forever(
        self,
        stop: Event,
        *,
        poll_seconds: float = 1.0,
        on_cycle: Callable[[ProjectAutomationCycleResult], None] | None = None,
        on_retry: Callable[[Exception, float], None] | None = None,
    ) -> None:
        """Poll until stopped, backing off retryable control-plane failures."""

        if poll_seconds <= 0:
            raise ValueError("automation worker poll interval must be positive")
        backoff_seconds = self._backoff_initial_seconds
        while not stop.is_set():
            try:
                result = self.cycle(stop)
            except _automation_control_errors() as error:
                if not _is_retryable_control_error(error):
                    raise
                if on_retry is not None:
                    on_retry(error, backoff_seconds)
                if stop.wait(backoff_seconds):
                    return
                backoff_seconds = min(
                    backoff_seconds * 2,
                    self._backoff_max_seconds,
                )
                continue

            backoff_seconds = self._backoff_initial_seconds
            if on_cycle is not None:
                on_cycle(result)
            wait_seconds = 0.0 if result.has_more else poll_seconds
            if stop.wait(wait_seconds):
                return


def _is_deterministic_client_error(error: Exception) -> bool:
    status = _error_status(error)
    return (
        status is not None
        and 400 <= status < 500
        and status not in _TRANSIENT_CLIENT_STATUSES
    )


def _empty_planner_cycle() -> ProcedurePlannerCycleResult:
    return ProcedurePlannerCycleResult(
        definitions=0,
        eligible_occurrences=0,
        existing_schedules=0,
        created_schedules=0,
        reconciled_schedules=0,
        drifted_schedules=0,
        failures=0,
        has_more=False,
    )


def _empty_schedule_cycle() -> ScheduleMaterializationCycleResult:
    return ScheduleMaterializationCycleResult(
        discovered=0,
        materialized=0,
        conflicts=0,
        failures=0,
        has_more=False,
    )


def _empty_procedure_cycle() -> ProcedureDispatchCycleResult:
    return ProcedureDispatchCycleResult(
        discovered=0,
        dispatched=0,
        failures=0,
        conflicts=0,
        lease_conflicts=0,
        has_more=False,
    )


def _is_retryable_control_error(error: Exception) -> bool:
    cause = _control_cause(error)
    if isinstance(cause, httpx2.TransportError):
        return True
    status = _error_status(cause)
    return status is not None and (
        status == 503 or status >= 500 or status in _TRANSIENT_CLIENT_STATUSES
    )


def _control_cause(error: Exception) -> Exception:
    selected = error
    while True:
        cause: BaseException | None = None
        if isinstance(
            selected,
            (
                ProcedureControlError,
                ProcedureLeaseLostError,
            ),
        ):
            cause = selected.cause
        if not isinstance(cause, Exception):
            return selected
        selected = cause


def _error_status(error: Exception) -> int | None:
    selected = _control_cause(error)
    if isinstance(selected, (DaemonClientError, httpx2.HTTPStatusError)):
        return selected.response.status_code
    return None


def _automation_control_errors() -> tuple[type[Exception], ...]:
    return (
        ProcedureControlError,
        ProcedureLeaseLostError,
        DaemonClientError,
        httpx2.HTTPError,
    )


__all__ = [
    "ProcedureDispatchCycleResult",
    "ProjectAutomationCycleResult",
    "ProjectAutomationWorker",
    "ScheduleMaterializationCycleResult",
]
