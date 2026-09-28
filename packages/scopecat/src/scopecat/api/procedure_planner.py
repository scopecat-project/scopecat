"""Project-side interval planning into durable exact one-shot schedules."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event
from typing import Protocol

import httpx2

from scopecat.api._config import LabConfigOperations
from scopecat.automation import (
    IntervalOccurrence,
    ProcedureSchedule,
    ProcedureScheduleRegistry,
    RegisteredProcedure,
    RegisteredProcedureSchedule,
)
from scopecat.daemon.client import (
    DaemonClientError,
    DaemonConflictError,
    DaemonNotFoundError,
)
from scopecat.daemon.views import ConfigEntryView
from scopecat.kernel.run_outcome import utc_now

_TRANSIENT_CLIENT_STATUSES = frozenset({408, 425, 429})
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcedurePlanningConfig:
    """Read-only exact configuration views available to intent builders."""

    _operations: LabConfigOperations = field(repr=False, compare=False)

    def entry(self, entry_id: str) -> ConfigEntryView:
        return self._operations.entry(entry_id)


@dataclass(frozen=True, slots=True)
class ProcedurePlanningContext:
    """Narrow read-only project context passed to interval intent builders."""

    config: ProcedurePlanningConfig


class ProcedureSchedulePlanningOperations(Protocol):
    """Exact one-shot operations required by the project interval planner."""

    def get_schedule(self, schedule_id: str) -> ProcedureSchedule: ...

    def create_schedule(
        self,
        definition: RegisteredProcedure,
        intent: object,
        *,
        schedule_id: str,
        due_at: datetime,
    ) -> ProcedureSchedule: ...


@dataclass(frozen=True, slots=True)
class ProcedurePlannerCycleResult:
    """Bounded interval planning outcomes from one coherent clock snapshot."""

    definitions: int
    eligible_occurrences: int
    existing_schedules: int
    created_schedules: int
    reconciled_schedules: int
    drifted_schedules: int
    failures: int
    has_more: bool


class ProjectProcedureIntervalPlanner:
    """Materialize latest-only interval slots as exact durable one-shots."""

    __slots__ = ("_clock", "_context", "_operations", "_registry")

    def __init__(
        self,
        operations: ProcedureSchedulePlanningOperations,
        registry: ProcedureScheduleRegistry[ProcedurePlanningContext],
        context: ProcedurePlanningContext,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._operations = operations
        self._registry = registry
        self._context = context
        self._clock = clock

    def cycle(self, stop: Event | None = None) -> ProcedurePlannerCycleResult:
        """Ensure at most the latest due occurrence for every active definition."""

        if stop is not None and stop.is_set():
            return ProcedurePlannerCycleResult(
                definitions=0,
                eligible_occurrences=0,
                existing_schedules=0,
                created_schedules=0,
                reconciled_schedules=0,
                drifted_schedules=0,
                failures=0,
                has_more=bool(self._registry),
            )
        evaluated_at = self._clock()
        definitions = 0
        eligible = 0
        existing = 0
        created = 0
        reconciled = 0
        drifted = 0
        failures = 0
        has_more = False

        for definition in self._registry.values():
            if stop is not None and stop.is_set():
                has_more = True
                break
            definitions += 1
            occurrence = definition.latest_occurrence(evaluated_at)
            if occurrence is None:
                continue
            eligible += 1

            try:
                schedule = self._operations.get_schedule(occurrence.schedule_id)
            except DaemonNotFoundError:
                if stop is not None and stop.is_set():
                    has_more = True
                    break
            else:
                existing += 1
                drifted += int(
                    not _matches_fixed_shell(schedule, definition, occurrence)
                )
                continue

            try:
                intent = definition.build_intent(self._context, occurrence)
            except Exception as error:
                if _is_transient_control_error(error):
                    raise
                failures += 1
                _LOGGER.exception(
                    "interval schedule %s intent builder failed for ordinal %d",
                    definition.id,
                    occurrence.ordinal,
                )
                continue
            if stop is not None and stop.is_set():
                has_more = True
                break

            try:
                schedule = self._operations.create_schedule(
                    definition.procedure,
                    intent,
                    schedule_id=occurrence.schedule_id,
                    due_at=occurrence.due_at,
                )
            except DaemonConflictError:
                try:
                    schedule = self._operations.get_schedule(occurrence.schedule_id)
                except (DaemonClientError, httpx2.HTTPError) as error:
                    if _is_transient_control_error(error):
                        raise
                    failures += 1
                    _LOGGER.exception(
                        "interval schedule %s create conflict could not reopen an "
                        "exact winner",
                        definition.id,
                    )
                    continue
                reconciled += 1
            except httpx2.TransportError as error:
                try:
                    schedule = self._operations.get_schedule(occurrence.schedule_id)
                except DaemonNotFoundError:
                    raise error from None
                reconciled += 1
            except (DaemonClientError, httpx2.HTTPError) as error:
                if _is_transient_control_error(error):
                    raise
                failures += 1
                _LOGGER.exception(
                    "interval schedule %s could not create ordinal %d",
                    definition.id,
                    occurrence.ordinal,
                )
                continue
            else:
                created += 1

            drifted += int(not _matches_fixed_shell(schedule, definition, occurrence))

        return ProcedurePlannerCycleResult(
            definitions=definitions,
            eligible_occurrences=eligible,
            existing_schedules=existing,
            created_schedules=created,
            reconciled_schedules=reconciled,
            drifted_schedules=drifted,
            failures=failures,
            has_more=has_more,
        )


def _matches_fixed_shell(
    schedule: ProcedureSchedule,
    definition: RegisteredProcedureSchedule[ProcedurePlanningContext],
    occurrence: IntervalOccurrence,
) -> bool:
    if not (
        schedule.schedule_id == occurrence.schedule_id
        and schedule.due_at == occurrence.due_at
        and schedule.definition == definition.procedure.ref
    ):
        return False
    try:
        definition.procedure.validate_intent(schedule.intent)
    except TypeError, ValueError:
        return False
    return True


def _is_transient_control_error(error: Exception) -> bool:
    if isinstance(error, httpx2.TransportError):
        return True
    if isinstance(error, (DaemonClientError, httpx2.HTTPStatusError)):
        status = error.response.status_code
        return status == 503 or status >= 500 or status in _TRANSIENT_CLIENT_STATUSES
    return False


__all__ = [
    "ProcedurePlannerCycleResult",
    "ProcedurePlanningConfig",
    "ProcedurePlanningContext",
    "ProcedureSchedulePlanningOperations",
    "ProjectProcedureIntervalPlanner",
]
