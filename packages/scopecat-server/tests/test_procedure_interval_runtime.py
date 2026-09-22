from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from scopecat.api.lab import LabClient
from scopecat.api.procedure_planner import ProcedurePlanningContext
from scopecat.api.procedures import LabProcedureContext
from scopecat.api.project_worker import ProjectAutomationWorker
from scopecat.application import LabApplication
from scopecat.automation import (
    IntervalOccurrence,
    IntervalTrigger,
    ProcedureRunListQuery,
    ProcedureScheduleDueQuery,
    ProcedureScheduleListQuery,
    ProcedureStepAttemptListQuery,
    RunOutputRef,
    interval_schedule,
    procedure,
)
from scopecat.daemon.client import DaemonClient

from scopecat_server import LocalDaemonRuntime

_STEP_INTENT_HASH = "sha256:" + "7" * 64
_DUE_ANCHOR = datetime(2020, 1, 1, tzinfo=UTC)
_DUE_PLANNER_NOW = _DUE_ANCHOR + timedelta(days=2, hours=3)
_FUTURE_ANCHOR = datetime(2100, 1, 1, tzinfo=UTC)
_INTENT_BUILDS: list[tuple[str, int]] = []
_EFFECT_CALLS: list[str] = []


class _IntervalIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_definition_id: str
    ordinal: int


@procedure(
    id="tests.runtime-interval-target",
    version="1",
    intent=_IntervalIntent,
)
def _interval_target(
    context: LabProcedureContext,
    intent: _IntervalIntent,
) -> None:
    def effect(operation_id: str) -> RunOutputRef:
        _EFFECT_CALLS.append(operation_id)
        return RunOutputRef(run_id=f"interval-child-{intent.ordinal}")

    context.step(
        "interval-effect",
        operation="run",
        intent_hash=_STEP_INTENT_HASH,
        effect=effect,
    )


@interval_schedule(
    id="tests.runtime-due-interval",
    version="1",
    procedure=_interval_target,
    trigger=IntervalTrigger(anchor=_DUE_ANCHOR, every=timedelta(days=1)),
)
def _due_interval_intent(
    context: ProcedurePlanningContext,
    occurrence: IntervalOccurrence,
) -> _IntervalIntent:
    del context
    _INTENT_BUILDS.append((occurrence.schedule_definition_id, occurrence.ordinal))
    return _IntervalIntent(
        schedule_definition_id=occurrence.schedule_definition_id,
        ordinal=occurrence.ordinal,
    )


@interval_schedule(
    id="tests.runtime-future-interval",
    version="1",
    procedure=_interval_target,
    trigger=IntervalTrigger(anchor=_FUTURE_ANCHOR, every=timedelta(days=1)),
)
def _future_interval_intent(
    context: ProcedurePlanningContext,
    occurrence: IntervalOccurrence,
) -> _IntervalIntent:
    del context
    _INTENT_BUILDS.append((occurrence.schedule_definition_id, occurrence.ordinal))
    return _IntervalIntent(
        schedule_definition_id=occurrence.schedule_definition_id,
        ordinal=occurrence.ordinal,
    )


_DUE_APPLICATION = LabApplication(
    procedures=(_interval_target,),
    procedure_schedules=(_due_interval_intent,),
)
_FUTURE_APPLICATION = LabApplication(
    procedures=(_interval_target,),
    procedure_schedules=(_future_interval_intent,),
)


def test_interval_worker_plans_executes_and_reopens_one_exact_slot(
    tmp_path: Path,
) -> None:
    _INTENT_BUILDS.clear()
    _EFFECT_CALLS.clear()
    occurrence = _due_interval_intent.latest_occurrence(_DUE_PLANNER_NOW)
    assert occurrence is not None
    assert occurrence.ordinal == 2

    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
        _lab(client, _DUE_APPLICATION) as lab,
    ):
        worker = ProjectAutomationWorker(
            lab.procedures,
            planner=lab.procedures.interval_planner(clock=lambda: _DUE_PLANNER_NOW),
            worker_id="interval-worker-first",
        )

        first = worker.cycle()

        assert first.intervals.eligible_occurrences == 1
        assert first.intervals.created_schedules == 1
        assert first.intervals.existing_schedules == 0
        assert first.schedules.materialized == 1
        assert first.procedures.dispatched == 1
        terminal = lab.procedures.get_schedule(occurrence.schedule_id)
        assert terminal.state == "materialized"
        assert terminal.materialization is not None
        run = client.get_procedure(terminal.materialization.procedure_run_id)
        assert run.state == "closed"
        assert run.closure is not None and run.closure.status == "succeeded"
        [step] = client.list_procedure_step_attempts(
            run.procedure_run_id,
            ProcedureStepAttemptListQuery(),
        ).items
        assert step.state == "succeeded"
        assert step.output == RunOutputRef(run_id="interval-child-2")

        repeated = worker.cycle()

        assert repeated.intervals.existing_schedules == 1
        assert repeated.intervals.created_schedules == 0
        assert repeated.schedules.materialized == 0
        assert repeated.procedures.dispatched == 0
        assert _INTENT_BUILDS == [("tests.runtime-due-interval", 2)]
        assert len(_EFFECT_CALLS) == 1

    with (
        LocalDaemonRuntime(tmp_path) as reopened,
        TestClient(reopened.app()) as transport,
        _daemon_client(transport) as client,
        _lab(client, _DUE_APPLICATION) as lab,
    ):
        worker = ProjectAutomationWorker(
            lab.procedures,
            planner=lab.procedures.interval_planner(clock=lambda: _DUE_PLANNER_NOW),
            worker_id="interval-worker-restarted",
        )

        restarted = worker.cycle()

        assert restarted.intervals.existing_schedules == 1
        assert restarted.intervals.created_schedules == 0
        assert restarted.schedules.materialized == 0
        assert restarted.procedures.dispatched == 0
        assert _INTENT_BUILDS == [("tests.runtime-due-interval", 2)]
        assert len(_EFFECT_CALLS) == 1
        assert client.list_procedure_schedules(ProcedureScheduleListQuery()).items == (
            terminal,
        )
        assert client.list_procedures(ProcedureRunListQuery()).items == (run,)


def test_planner_clock_cannot_make_a_future_one_shot_due_on_the_server(
    tmp_path: Path,
) -> None:
    assert datetime.now(UTC) < _FUTURE_ANCHOR
    _INTENT_BUILDS.clear()
    _EFFECT_CALLS.clear()
    occurrence = _future_interval_intent.latest_occurrence(_FUTURE_ANCHOR)
    assert occurrence is not None

    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
        _lab(client, _FUTURE_APPLICATION) as lab,
    ):
        worker = ProjectAutomationWorker(
            lab.procedures,
            planner=lab.procedures.interval_planner(clock=lambda: _FUTURE_ANCHOR),
            worker_id="future-interval-worker",
        )

        planned = worker.cycle()

        assert planned.intervals.eligible_occurrences == 1
        assert planned.intervals.created_schedules == 1
        assert planned.schedules.discovered == 0
        assert planned.schedules.materialized == 0
        assert planned.procedures.discovered == 0
        assert planned.procedures.dispatched == 0
        pending = lab.procedures.get_schedule(occurrence.schedule_id)
        assert pending.state == "pending"
        assert pending.due_at == _FUTURE_ANCHOR
        assert (
            client.list_due_procedure_schedules(ProcedureScheduleDueQuery()).items == ()
        )
        assert client.list_procedures(ProcedureRunListQuery()).items == ()
        assert _INTENT_BUILDS == [("tests.runtime-future-interval", 0)]
        assert _EFFECT_CALLS == []


def _lab(client: DaemonClient, application: LabApplication) -> LabClient:
    return LabClient(
        client,
        procedures=application.procedures,
        procedure_schedules=application.procedure_schedules,
    )


def _daemon_client(transport: TestClient) -> DaemonClient:
    def send(request: httpx2.Request) -> httpx2.Response:
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )
