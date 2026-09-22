from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureContext
from scopecat.api.project_worker import ProjectAutomationWorker
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureRegistry,
    ProcedureRunListQuery,
    ProcedureRunnableQuery,
    ProcedureScheduleCancelCommand,
    ProcedureScheduleCreateCommand,
    ProcedureScheduleDueQuery,
    ProcedureScheduleListQuery,
    ProcedureScheduleMaterializeCommand,
    ProcedureStepAttemptListQuery,
    RunOutputRef,
    procedure,
)
from scopecat.daemon.client import DaemonClient

from scopecat_server import LocalDaemonRuntime

_HASH = "sha256:" + "1" * 64
_STEP_HASH = "sha256:" + "2" * 64
_PAST = datetime(2020, 1, 1, tzinfo=UTC)
_SCHEDULED_EFFECT_CALLS: list[str] = []


class _ScheduledWorkerIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_run_id: str


@procedure(
    id="tests.runtime-scheduled-worker",
    version="1",
    intent=_ScheduledWorkerIntent,
)
def _scheduled_worker_procedure(
    context: LabProcedureContext,
    intent: _ScheduledWorkerIntent,
) -> None:
    def effect(operation_id: str) -> RunOutputRef:
        _SCHEDULED_EFFECT_CALLS.append(operation_id)
        return RunOutputRef(run_id=intent.output_run_id)

    context.step(
        "scheduled-effect",
        operation="run",
        intent_hash=_STEP_HASH,
        effect=effect,
    )


def _definition() -> ProcedureDefinitionRef:
    return ProcedureDefinitionRef(
        id="runtime-scheduled-procedure",
        version="1",
        fingerprint=_HASH,
    )


def test_runtime_exposes_persistent_quoted_schedules_and_runnable_runs(
    tmp_path: Path,
) -> None:
    schedule_id = "nightly/q0"
    command = ProcedureScheduleCreateCommand(
        schedule_id=schedule_id,
        definition=_definition(),
        intent={"qubits": ["q0"]},
        due_at=_PAST,
    )
    materialize = ProcedureScheduleMaterializeCommand(
        schedule_id=schedule_id,
        expected_schedule_revision=1,
    )
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        pending = client.create_procedure_schedule(command).schedule
        assert client.get_procedure_schedule(schedule_id) == pending
        assert client.list_procedure_schedules(ProcedureScheduleListQuery()).items == (
            pending,
        )
        assert client.list_due_procedure_schedules(
            ProcedureScheduleDueQuery()
        ).items == (pending,)

        terminal = client.materialize_procedure_schedule(materialize).schedule
        assert terminal.materialization is not None
        run = client.get_procedure(terminal.materialization.procedure_run_id)
        assert client.list_runnable_procedures(
            ProcedureRunnableQuery(definitions=(_definition(),))
        ).items == (run,)

    with (
        LocalDaemonRuntime(tmp_path) as reopened,
        TestClient(reopened.app()) as transport,
        _daemon_client(transport) as client,
    ):
        assert client.get_procedure_schedule(schedule_id) == terminal
        assert client.materialize_procedure_schedule(materialize).schedule == terminal
        assert client.get_procedure(run.procedure_run_id) == run


def test_runtime_cancel_route_persists_terminal_schedule(tmp_path: Path) -> None:
    command = ProcedureScheduleCreateCommand(
        schedule_id="maintenance/q1",
        definition=_definition(),
        intent={"qubits": ["q1"]},
        due_at=datetime(2100, 1, 1, tzinfo=UTC),
    )
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        pending = client.create_procedure_schedule(command).schedule
        cancelled = client.cancel_procedure_schedule(
            ProcedureScheduleCancelCommand(
                schedule_id=pending.schedule_id,
                expected_schedule_revision=pending.revision,
                actor="operator",
                reason="maintenance window",
            )
        ).schedule
        assert cancelled.state == "cancelled"
        assert client.create_procedure_schedule(command).schedule == cancelled


def test_project_worker_materializes_executes_and_reopens_one_shot_schedule(
    tmp_path: Path,
) -> None:
    schedule_id = "worker/nightly?q0"
    registry = ProcedureRegistry((_scheduled_worker_procedure,))
    _SCHEDULED_EFFECT_CALLS.clear()
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(
            transport,
            lose_schedule_materialization_responses=2,
        ) as client,
        LabClient(client, procedures=registry) as lab,
    ):
        pending = lab.procedures.create_schedule(
            _scheduled_worker_procedure,
            _ScheduledWorkerIntent(output_run_id="scheduled-child-run"),
            schedule_id=schedule_id,
            due_at=_PAST,
        )
        assert pending.state == "pending"
        worker = ProjectAutomationWorker(
            lab.procedures,
            planner=lab.procedures.interval_planner(),
            worker_id="project-worker-before-crash",
        )
        with pytest.raises(httpx2.ReadError, match="response was lost"):
            worker.cycle()
        assert _SCHEDULED_EFFECT_CALLS == []

    with (
        LocalDaemonRuntime(tmp_path) as reopened,
        TestClient(reopened.app()) as transport,
        _daemon_client(transport) as client,
        LabClient(client, procedures=registry) as lab,
    ):
        worker = ProjectAutomationWorker(
            lab.procedures,
            planner=lab.procedures.interval_planner(),
            worker_id="project-worker-restarted",
        )

        completed_cycle = worker.cycle()

        assert completed_cycle.schedules.materialized == 0
        assert completed_cycle.procedures.dispatched == 1
        terminal = lab.procedures.get_schedule(schedule_id)
        assert terminal.state == "materialized"
        assert terminal.materialization is not None
        run = client.get_procedure(terminal.materialization.procedure_run_id)
        assert run.state == "closed"
        assert run.closure is not None
        assert run.closure.status == "succeeded"
        [step] = client.list_procedure_step_attempts(
            run.procedure_run_id,
            ProcedureStepAttemptListQuery(),
        ).items
        assert step.state == "succeeded"
        assert step.output == RunOutputRef(run_id="scheduled-child-run")
        assert len(_SCHEDULED_EFFECT_CALLS) == 1
        assert worker.cycle().procedures.dispatched == 0
        runs = client.list_procedures(ProcedureRunListQuery())
        assert runs.items == (run,)


def _daemon_client(
    transport: TestClient,
    *,
    lose_schedule_materialization_responses: int = 0,
) -> DaemonClient:
    remaining_lost_responses = lose_schedule_materialization_responses

    def send(request: httpx2.Request) -> httpx2.Response:
        nonlocal remaining_lost_responses
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        if (
            request.method == "POST"
            and request.url.path.startswith(
                "/api/v1/procedure-schedule-materializations/"
            )
            and remaining_lost_responses > 0
        ):
            remaining_lost_responses -= 1
            raise httpx2.ReadError(
                "schedule materialization response was lost",
                request=request,
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
