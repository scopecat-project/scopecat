from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast

import httpx2
import pytest
from pydantic import BaseModel, ConfigDict

from scopecat.api._config import LabConfigOperations
from scopecat.api.procedure_planner import (
    ProcedurePlanningConfig,
    ProcedurePlanningContext,
    ProjectProcedureIntervalPlanner,
)
from scopecat.automation import (
    IntervalOccurrence,
    IntervalTrigger,
    ProcedureDefinition,
    ProcedureSchedule,
    ProcedureScheduleCreateCommand,
    ProcedureScheduleDefinition,
    ProcedureScheduleRegistry,
    RegisteredProcedure,
    procedure,
    procedure_intent_hash,
)
from scopecat.daemon.client import (
    DaemonClientError,
    DaemonConflictError,
    DaemonNotFoundError,
)


class _PlannerIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    ordinal: int


@procedure(id="tests.planner-target", version="1", intent=_PlannerIntent)
def _target(_context: object, _intent: _PlannerIntent) -> None:
    pass


@procedure(id="tests.changed-planner-target", version="2", intent=_PlannerIntent)
def _changed_target(_context: object, _intent: _PlannerIntent) -> None:
    pass


_BUILDS: list[IntervalOccurrence] = []


def _build_intent(
    context: ProcedurePlanningContext,
    occurrence: IntervalOccurrence,
) -> _PlannerIntent:
    _BUILDS.append(occurrence)
    return _PlannerIntent(
        source=cast("str", cast("object", context.config.entry("seed"))),
        ordinal=occurrence.ordinal,
    )


def _fail_intent(
    _context: ProcedurePlanningContext,
    _occurrence: IntervalOccurrence,
) -> _PlannerIntent:
    raise ValueError("invalid project planning state")


def _transient_intent(
    _context: ProcedurePlanningContext,
    _occurrence: IntervalOccurrence,
) -> _PlannerIntent:
    raise httpx2.ReadError("planning read lost")


_ANCHOR = datetime(2026, 8, 18, tzinfo=UTC)
_NOW = _ANCHOR + timedelta(hours=7, minutes=30)


@dataclass(slots=True)
class _FakeConfigOperations:
    active_value: str = "active-generation-7"

    def entry(self, _entry_id: str) -> object:
        return self.active_value


@dataclass(slots=True)
class _FakePlanningOperations:
    now: datetime = _NOW
    schedules: dict[str, ProcedureSchedule] = field(default_factory=dict)
    get_calls: list[str] = field(default_factory=list)
    create_commands: list[ProcedureScheduleCreateCommand] = field(default_factory=list)
    create_error: Exception | None = None
    conflict_winner: ProcedureSchedule | None = None
    stop_after_missing_get: Event | None = None

    def get_schedule(self, schedule_id: str) -> ProcedureSchedule:
        self.get_calls.append(schedule_id)
        try:
            return self.schedules[schedule_id]
        except KeyError:
            if self.stop_after_missing_get is not None:
                self.stop_after_missing_get.set()
            raise _daemon_error(DaemonNotFoundError, 404, "missing") from None

    def create_schedule(
        self,
        definition: RegisteredProcedure,
        intent: object,
        *,
        schedule_id: str,
        due_at: datetime,
    ) -> ProcedureSchedule:
        encoded = definition.encode_intent(intent)
        command = ProcedureScheduleCreateCommand(
            schedule_id=schedule_id,
            definition=definition.ref,
            intent=encoded,
            due_at=due_at,
        )
        self.create_commands.append(command)
        if self.conflict_winner is not None:
            self.schedules[schedule_id] = self.conflict_winner
        if self.create_error is not None:
            error = self.create_error
            self.create_error = None
            raise error
        schedule = _schedule(
            schedule_id=schedule_id,
            definition=definition,
            intent=encoded,
            due_at=due_at,
            now=self.now,
        )
        self.schedules[schedule_id] = schedule
        return schedule


def _definition(
    *,
    id: str = "tests.interval-planner",
    version: str = "1",
    target: ProcedureDefinition[_PlannerIntent] = _target,
    builder: Callable[
        [ProcedurePlanningContext, IntervalOccurrence], _PlannerIntent
    ] = _build_intent,
) -> ProcedureScheduleDefinition[ProcedurePlanningContext, _PlannerIntent]:
    return ProcedureScheduleDefinition(
        id=id,
        version=version,
        procedure=target,
        trigger=IntervalTrigger(anchor=_ANCHOR, every=timedelta(hours=2)),
        _build_intent=builder,
    )


def _planner(
    operations: _FakePlanningOperations,
    *definitions: ProcedureScheduleDefinition[ProcedurePlanningContext, _PlannerIntent],
    clock: Callable[[], datetime] = lambda: _NOW,
) -> ProjectProcedureIntervalPlanner:
    config = ProcedurePlanningConfig(
        cast("LabConfigOperations", cast("object", _FakeConfigOperations()))
    )
    return ProjectProcedureIntervalPlanner(
        operations,
        ProcedureScheduleRegistry(definitions),
        ProcedurePlanningContext(config=config),
        clock=clock,
    )


def test_planner_creates_only_the_latest_due_occurrence() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()
    definition = _definition()

    result = _planner(operations, definition).cycle()

    assert result.definitions == 1
    assert result.eligible_occurrences == 1
    assert result.created_schedules == 1
    assert result.existing_schedules == 0
    assert result.reconciled_schedules == 0
    assert result.drifted_schedules == 0
    assert result.failures == 0
    assert result.has_more is False
    assert [occurrence.ordinal for occurrence in _BUILDS] == [3]
    assert len(operations.create_commands) == 1
    assert operations.create_commands[0].due_at == _ANCHOR + timedelta(hours=6)


def test_existing_exact_occurrence_is_authoritative_without_rebuilding_intent() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()
    original = _definition()
    occurrence = original.latest_occurrence(_NOW)
    assert occurrence is not None
    existing_intent = _target.encode_intent(
        _PlannerIntent(source="old-active-generation", ordinal=occurrence.ordinal)
    )
    operations.schedules[occurrence.schedule_id] = _schedule(
        schedule_id=occurrence.schedule_id,
        definition=_target,
        intent=existing_intent,
        due_at=occurrence.due_at,
        now=_NOW,
    )

    result = _planner(operations, original).cycle()

    assert result.existing_schedules == 1
    assert result.drifted_schedules == 0
    assert result.created_schedules == 0
    assert _BUILDS == []
    assert operations.create_commands == []


def test_rolling_target_change_reports_drift_but_keeps_existing_authority() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()
    original = _definition()
    changed = _definition(target=_changed_target)
    occurrence = original.latest_occurrence(_NOW)
    assert occurrence is not None
    operations.schedules[occurrence.schedule_id] = _schedule(
        schedule_id=occurrence.schedule_id,
        definition=_target,
        intent=_target.encode_intent(
            _PlannerIntent(source="old-deployment", ordinal=occurrence.ordinal)
        ),
        due_at=occurrence.due_at,
        now=_NOW,
    )

    result = _planner(operations, changed).cycle()

    assert result.existing_schedules == 1
    assert result.drifted_schedules == 1
    assert result.created_schedules == 0
    assert _BUILDS == []
    assert operations.create_commands == []


def test_reused_version_with_changed_trigger_reports_due_time_drift() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()
    original = _definition()
    occurrence = original.latest_occurrence(_NOW)
    assert occurrence is not None
    operations.schedules[occurrence.schedule_id] = _schedule(
        schedule_id=occurrence.schedule_id,
        definition=_target,
        intent=_target.encode_intent(
            _PlannerIntent(source="old-trigger", ordinal=occurrence.ordinal)
        ),
        due_at=occurrence.due_at,
        now=_NOW,
    )
    changed = ProcedureScheduleDefinition(
        id=original.id,
        version=original.version,
        procedure=_target,
        trigger=IntervalTrigger(
            anchor=_ANCHOR + timedelta(minutes=30),
            every=timedelta(hours=2),
        ),
        _build_intent=_build_intent,
    )

    result = _planner(operations, changed).cycle()

    assert result.existing_schedules == 1
    assert result.drifted_schedules == 1
    assert _BUILDS == []
    assert operations.create_commands == []


def test_invalid_durable_intent_reports_drift_without_calling_builder() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()
    definition = _definition()
    occurrence = definition.latest_occurrence(_NOW)
    assert occurrence is not None
    operations.schedules[occurrence.schedule_id] = _schedule(
        schedule_id=occurrence.schedule_id,
        definition=_target,
        intent={"source": "missing-ordinal"},
        due_at=occurrence.due_at,
        now=_NOW,
    )

    result = _planner(operations, definition).cycle()

    assert result.existing_schedules == 1
    assert result.drifted_schedules == 1
    assert _BUILDS == []
    assert operations.create_commands == []


def test_create_conflict_reopens_the_exact_winner_without_rebuilding() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()
    definition = _definition()
    occurrence = definition.latest_occurrence(_NOW)
    assert occurrence is not None
    operations.conflict_winner = _schedule(
        schedule_id=occurrence.schedule_id,
        definition=_target,
        intent=_target.encode_intent(
            _PlannerIntent(source="concurrent", ordinal=occurrence.ordinal)
        ),
        due_at=occurrence.due_at,
        now=_NOW,
    )
    operations.create_error = _daemon_error(
        DaemonConflictError,
        409,
        "winner already committed",
    )

    result = _planner(operations, definition).cycle()

    assert result.reconciled_schedules == 1
    assert result.drifted_schedules == 0
    assert len(_BUILDS) == 1
    assert len(operations.create_commands) == 1
    assert operations.get_calls == [occurrence.schedule_id, occurrence.schedule_id]


def test_create_conflict_without_winner_is_local_and_later_definition_runs() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations(
        create_error=_daemon_error(
            DaemonConflictError,
            409,
            "conflict without winner",
        )
    )

    result = _planner(
        operations,
        _definition(id="tests.a-conflict"),
        _definition(id="tests.z-success"),
    ).cycle()

    assert result.failures == 1
    assert result.created_schedules == 1
    assert result.reconciled_schedules == 0
    assert len(_BUILDS) == 2
    assert len(operations.create_commands) == 2


def test_lost_create_response_reopens_committed_occurrence() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()
    definition = _definition()
    occurrence = definition.latest_occurrence(_NOW)
    assert occurrence is not None
    operations.conflict_winner = _schedule(
        schedule_id=occurrence.schedule_id,
        definition=_target,
        intent=_target.encode_intent(
            _PlannerIntent(source="committed", ordinal=occurrence.ordinal)
        ),
        due_at=occurrence.due_at,
        now=_NOW,
    )
    operations.create_error = httpx2.ReadError("lost response")

    result = _planner(operations, definition).cycle()

    assert result.reconciled_schedules == 1
    assert len(_BUILDS) == 1
    assert len(operations.create_commands) == 1


def test_stop_after_absence_lookup_does_not_call_mutable_builder() -> None:
    _BUILDS.clear()
    stop = Event()
    operations = _FakePlanningOperations(stop_after_missing_get=stop)

    result = _planner(operations, _definition()).cycle(stop)

    assert result.eligible_occurrences == 1
    assert result.has_more is True
    assert _BUILDS == []
    assert operations.create_commands == []


def test_stop_set_by_builder_prevents_one_shot_creation() -> None:
    stop = Event()

    def stopping_builder(
        context: ProcedurePlanningContext,
        occurrence: IntervalOccurrence,
    ) -> _PlannerIntent:
        stop.set()
        return _PlannerIntent(
            source=cast("str", cast("object", context.config.entry("seed"))),
            ordinal=occurrence.ordinal,
        )

    operations = _FakePlanningOperations()
    result = _planner(
        operations,
        _definition(builder=stopping_builder),
    ).cycle(stop)

    assert result.has_more is True
    assert result.eligible_occurrences == 1
    assert operations.create_commands == []


def test_pre_stopped_cycle_does_not_evaluate_project_clock() -> None:
    stop = Event()
    stop.set()
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        raise AssertionError("stopped planner must not read its clock")

    result = _planner(
        _FakePlanningOperations(),
        _definition(),
        clock=clock,
    ).cycle(stop)

    assert result.has_more is True
    assert result.definitions == 0
    assert clock_calls == 0


def test_deterministic_builder_failure_does_not_block_later_definition() -> None:
    operations = _FakePlanningOperations()

    result = _planner(
        operations,
        _definition(id="tests.a-failure", builder=_fail_intent),
        _definition(id="tests.z-success"),
    ).cycle()

    assert result.failures == 1
    assert result.created_schedules == 1
    assert len(operations.create_commands) == 1


def test_transient_builder_read_propagates_to_worker_backoff() -> None:
    with pytest.raises(httpx2.ReadError, match="planning read lost"):
        _planner(
            _FakePlanningOperations(),
            _definition(builder=_transient_intent),
        ).cycle()


def test_before_anchor_has_no_occurrence_or_future_one_shot() -> None:
    _BUILDS.clear()
    operations = _FakePlanningOperations()

    result = _planner(
        operations,
        _definition(),
        clock=lambda: _ANCHOR - timedelta(microseconds=1),
    ).cycle()

    assert result.eligible_occurrences == 0
    assert operations.get_calls == []
    assert _BUILDS == []


def test_naive_evaluation_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        _planner(
            _FakePlanningOperations(),
            _definition(),
            clock=lambda: _NOW.replace(tzinfo=None),
        ).cycle()


def _schedule(
    *,
    schedule_id: str,
    definition: RegisteredProcedure,
    intent: Mapping[str, object],
    due_at: datetime,
    now: datetime,
) -> ProcedureSchedule:
    return ProcedureSchedule(
        schedule_id=schedule_id,
        definition=definition.ref,
        intent=intent,
        intent_hash=procedure_intent_hash(definition.ref, intent),
        due_at=due_at,
        revision=1,
        state="pending",
        created_at=now,
        updated_at=now,
    )


def _daemon_error(
    error_type: type[DaemonClientError],
    status: int,
    detail: str,
) -> DaemonClientError:
    response = httpx2.Response(
        status,
        request=httpx2.Request("GET", "http://daemon.test/resource"),
    )
    return error_type(detail, response=response)
