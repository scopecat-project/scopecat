from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    ProcedureSubmitCommand,
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


def test_registered_project_worker_materializes_its_course_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat.application.lab import LabApplication
    from scopecat.author_workspaces import (
        LocalAuthorWorkspace,
        LocalAuthorWorkspaces,
        author_bindings_path,
    )
    from scopecat.project import Project, open_project
    from scopecat.records.practice import PracticeScope

    from scopecat_server.storage.sqlite.practice import PracticeOwnership

    root = tmp_path / "course-project"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    bindings = author_bindings_path(root)
    bindings.parent.mkdir(parents=True, exist_ok=True)
    bindings.write_text(
        LocalAuthorWorkspaces(
            service_root=tmp_path / "service",
            items=(
                LocalAuthorWorkspace(
                    id="course-source", name="Course", root=root, python=root / "python"
                ),
            ),
        ).model_dump_json()
    )
    registry = ProcedureRegistry((_scheduled_worker_procedure,))

    def application(_project: Project) -> LabApplication:
        return LabApplication(procedures=registry)

    monkeypatch.setattr(Project, "load_application", application)

    def endpoint(_root: Path, *, explicit: str | None = None) -> str:
        return explicit or "http://testserver"

    monkeypatch.setattr("scopecat.daemon.endpoint.resolve_daemon_endpoint", endpoint)
    with (
        LocalDaemonRuntime(tmp_path / "service") as runtime,
        TestClient(runtime.app()) as transport,
    ):
        sqlite = runtime.application.calibration_tasks._sqlite
        with sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            owners.save(
                PracticeScope(
                    id="course",
                    title="Course",
                    lesson="parameters",
                    directory=str(root),
                    kind="notebook",
                    workspace_id="course-source",
                )
            )
            owners.claim("course", "source", "course-source")
            owners.save(
                PracticeScope(
                    id="other-course",
                    title="Other",
                    lesson="parameters",
                    directory="other",
                    kind="notebook",
                    workspace_id="other-source",
                )
            )
            owners.claim("other-course", "source", "other-source")
        other = transport.post(
            "/api/v1/procedures",
            json=ProcedureSubmitCommand(
                definition=_scheduled_worker_procedure.ref,
                request_key="other-course",
                intent={"output_run_id": "other-output"},
            ).model_dump(mode="json"),
            headers={"X-Scopecat-Workspace": "other-source"},
        )
        assert other.status_code == 201, other.text
        other_id = other.json()["run"]["procedure_run_id"]
        command = ProcedureScheduleCreateCommand(
            schedule_id="course-schedule",
            definition=_scheduled_worker_procedure.ref,
            intent={"output_run_id": "course-output"},
            due_at=_PAST,
        )
        response = transport.post(
            "/api/v1/procedure-schedules",
            json=command.model_dump(mode="json"),
            headers={"X-Scopecat-Workspace": "course-source"},
        )
        assert response.status_code == 201, response.text
        assert transport.get("/api/v1/procedure-schedules/due").json()["items"] == []
        original_init = DaemonClient.__init__

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

        def connected(
            self: DaemonClient, base_url: str, *, workspace_id: str | None = None
        ) -> None:
            original_init(
                self,
                base_url,
                workspace_id=workspace_id,
                transport=httpx2.MockTransport(send),
            )

        monkeypatch.setattr(DaemonClient, "__init__", connected)
        with open_project(root).connect("http://testserver") as lab:
            cycle = ProjectAutomationWorker(lab.procedures).cycle()
            assert cycle.schedules.materialized == 1
            assert cycle.schedules.failures == 0 and cycle.schedules.conflicts == 0
            assert cycle.procedures.dispatched == 1
            schedule = lab.procedures.get_schedule("course-schedule")
            assert schedule.state == "materialized"
            assert (
                transport.get(f"/api/v1/procedures/{other_id}").json()["state"]
                == "ready"
            )


@pytest.mark.parametrize("first_workspace", [None, "one"])
@pytest.mark.parametrize("resource", ["procedure", "schedule"])
def test_http_procedure_replay_never_transfers_course_ownership(
    tmp_path: Path,
    first_workspace: str | None,
    resource: Literal["procedure", "schedule"],
) -> None:
    from scopecat.records.practice import PracticeScope

    from scopecat_server.storage.sqlite.practice import PracticeOwnership

    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        sqlite = runtime.application.calibration_tasks._sqlite
        with sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            for identity in ("one", "two"):
                owners.save(
                    PracticeScope(
                        id=identity,
                        title=identity,
                        lesson="parameters",
                        directory=identity,
                        kind="notebook",
                        workspace_id=identity,
                    )
                )
                owners.claim(identity, "source", identity)
        if resource == "procedure":
            command = ProcedureSubmitCommand(
                definition=_scheduled_worker_procedure.ref,
                request_key="same-key",
                intent={"output_run_id": "same-intent"},
            ).model_dump(mode="json")
            path, receipt, identity_key = (
                "/api/v1/procedures",
                "run",
                "procedure_run_id",
            )
        else:
            command = ProcedureScheduleCreateCommand(
                schedule_id="same-key",
                definition=_scheduled_worker_procedure.ref,
                intent={"output_run_id": "same-intent"},
                due_at=_PAST,
            ).model_dump(mode="json")
            path, receipt, identity_key = (
                "/api/v1/procedure-schedules",
                "schedule",
                "schedule_id",
            )
        headers = {"X-Scopecat-Workspace": first_workspace} if first_workspace else {}
        first = client.post(path, json=command, headers=headers)
        assert first.status_code == 201, first.text
        repeated = client.post(path, json=command, headers=headers)
        assert repeated.status_code == 201, repeated.text
        identity = first.json()[receipt][identity_key]
        assert repeated.json()[receipt][identity_key] == identity
        for workspace in (None, "one", "two"):
            if workspace == first_workspace:
                continue
            other = client.post(
                path,
                json=command,
                headers={"X-Scopecat-Workspace": workspace} if workspace else {},
            )
            assert other.status_code == 409, other.text
            assert "belongs to another" in other.text
        with sqlite.read_transaction() as connection:
            assert (
                PracticeOwnership(connection).owner(resource, identity)
                == first_workspace
            )


@pytest.mark.parametrize("action", ["cancel", "materialize"])
def test_schedule_operator_replay_requires_its_workspace(
    tmp_path: Path, action: str
) -> None:
    from scopecat.records.practice import PracticeScope

    from scopecat_server.storage.sqlite.practice import PracticeOwnership

    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        schedule = runtime.application.procedure_schedules.create(
            ProcedureScheduleCreateCommand(
                schedule_id="operator-replay",
                definition=_definition(),
                intent={},
                due_at=_PAST,
            )
        ).schedule
        sqlite = runtime.application.calibration_tasks._sqlite
        with sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            for identity in ("one", "two"):
                owners.save(
                    PracticeScope(
                        id=identity,
                        title=identity,
                        lesson="parameters",
                        directory=identity,
                        kind="notebook",
                        workspace_id=identity,
                    )
                )
                owners.claim(identity, "source", identity)
            owners.claim("one", "schedule", schedule.schedule_id)
        if action == "cancel":
            path = f"/api/v1/procedure-schedule-cancellations/{schedule.schedule_id}"
            command = ProcedureScheduleCancelCommand(
                schedule_id=schedule.schedule_id,
                expected_schedule_revision=schedule.revision,
                actor="operator",
                reason="stop",
            ).model_dump(mode="json")
        else:
            path = f"/api/v1/procedure-schedule-materializations/{schedule.schedule_id}"
            command = ProcedureScheduleMaterializeCommand(
                schedule_id=schedule.schedule_id,
                expected_schedule_revision=schedule.revision,
            ).model_dump(mode="json")
        for workspace in (None, "two"):
            response = client.post(
                path,
                json=command,
                headers={"X-Scopecat-Workspace": workspace} if workspace else {},
            )
            assert response.status_code == 409, response.text
        response = client.post(
            path, json=command, headers={"X-Scopecat-Workspace": "one"}
        )
        assert response.status_code == 200, response.text
        assert (
            client.post(
                path, json=command, headers={"X-Scopecat-Workspace": "one"}
            ).json()
            == response.json()
        )
        for workspace in (None, "two"):
            replay = client.post(
                path,
                json=command,
                headers={"X-Scopecat-Workspace": workspace} if workspace else {},
            )
            assert replay.status_code == 409, replay.text
