from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import psutil
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from scopecat.analysis.facts import AnalysisFactSchema, analysis_fact_structure_hash
from scopecat.api.analysis import Analysis, AnalysisContext, analysis_step
from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import (
    InterpretationRequest,
    ProcedureRegistry,
    ProcedureSource,
    ProcedureStepAttemptListQuery,
    ProcedureStepBeginCommand,
    ProcedureStepInputWaitCommand,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    procedure,
)
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.setup import SetupRevisionRef
from scopecat_testkit.server.instruments import signal_endpoint
from scopecat_testkit.workflow_fixtures import load_config, load_invocation

from scopecat_server import BackendConflict, LocalDaemonRuntime
from scopecat_server.services.background_rounds import BackgroundRounds
from scopecat_server.services.exit_drain import ExitDrain
from scopecat_server.services.project_workers import (
    ProjectProcedureWorkers,
    WorkerProcess,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase

from .test_automation import _definition, _service
from .test_automation_runtime import _daemon_client
from .test_procedure_schedules import _command
from .test_project_analysis_runtime import _config


class _ExitAnswer(BaseModel, frozen=True):
    accepted: bool


class _ExitIntent(BaseModel, frozen=True):
    setup: SetupRevisionRef


def _workers(tmp_path: Path) -> ProjectProcedureWorkers:
    workers = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "waiting_for_input")
    workers._path = lambda: tmp_path / "console-procedures.json"
    return workers


def _checkpoint(tmp_path: Path):
    service, clock = _service(tmp_path)
    clock[0] = datetime.now(UTC)
    run = service.submit(
        ProcedureSubmitCommand(
            definition=_definition(),
            request_key="checkpoint",
            intent={"exact": [1, 2]},
            source=ProcedureSource(
                workspace_id="source",
                code_revision=AuthorRevisionRef(content_hash="sha256:" + "a" * 64),
            ),
        )
    ).run
    workers = _workers(tmp_path)
    workers.manage(run.procedure_run_id)
    lease = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=run.procedure_run_id,
            worker_id="fixture",
            expected_run_revision=run.revision,
        )
    )
    request = InterpretationRequest(
        title="Choose a result",
        instructions="Inspect retained evidence",
        schema_id="fixture.v1",
        schema_hash=analysis_fact_structure_hash({"type": "object"}),
        structure={"type": "object"},
    )
    step = service.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=run.procedure_run_id,
            lease_token=lease.lease.lease_token,
            expected_run_revision=lease.run.revision,
            step_key="decision",
            operation="interpretation",
            intent_hash=request.request_hash,
        )
    )
    wait = workers.wait_for_input(
        lambda: service.wait_step_input(
            ProcedureStepInputWaitCommand(
                procedure_run_id=run.procedure_run_id,
                lease_token=lease.lease.lease_token,
                expected_run_revision=step.run.revision,
                step_key=step.step.step_key,
                attempt=step.step.attempt,
                expected_step_revision=step.step.revision,
                request=request,
            )
        )
    )
    directory = workers._worker_dir(run.procedure_run_id)
    directory.mkdir(parents=True, exist_ok=True)
    # Known non-existent PID with exact success observation; no hardware process.
    receipt = WorkerProcess(pid=2_000_000_000, created=1.0).model_dump_json()
    (directory / "process.json").write_text(receipt)
    (directory / "settled.json").write_text(receipt)
    return service, workers, wait


def test_exact_checkpoint_survives_reopen_without_completion(tmp_path: Path) -> None:
    service, workers, wait = _checkpoint(tmp_path)
    rounds = BackgroundRounds()
    rounds.drain(True)
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    drain = ExitDrain(sqlite)
    expected = service.get(wait.run.procedure_run_id)
    assert drain.report(workers, rounds, 0).ready
    assert drain.report(_workers(tmp_path), rounds, 0).ready
    assert service.get(expected.procedure_run_id) == expected
    assert expected.state == "waiting_for_input"
    assert (
        service.step_attempts(expected.procedure_run_id, Mock(limit=50, cursor=None))
        .items[0]
        .interpretation_request
        == wait.step.interpretation_request
    )
    # A recovered dead PID alone is insufficient evidence of successful cleanup.
    (workers._worker_dir(expected.procedure_run_id) / "settled.json").unlink()
    assert not drain.report(_workers(tmp_path), rounds, 0).ready
    sqlite.close()


def test_checkpoint_blocks_observed_failed_exit_and_live_orphan(tmp_path: Path) -> None:
    _, workers, wait = _checkpoint(tmp_path)
    identity = wait.run.procedure_run_id
    child = Mock()
    child.poll.return_value = 1
    workers._children[identity] = child
    assert not workers.checkpoint_settled(wait.step)
    workers._children.clear()
    directory = workers._worker_dir(identity)
    receipt = WorkerProcess(
        pid=os.getpid(), created=psutil.Process().create_time()
    ).model_dump_json()
    (directory / "process.json").write_text(receipt)
    (directory / "settled.json").write_text(receipt)
    assert not workers.checkpoint_settled(wait.step)
    assert workers.unsettled_processes()


def test_unknown_scope_pause_resource_and_quarantine_stay_blocking(
    tmp_path: Path,
) -> None:
    service, workers, wait = _checkpoint(tmp_path)
    rounds = BackgroundRounds()
    rounds.drain(True)
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    drain = ExitDrain(sqlite)
    with sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO resource_claims VALUES (?, ?, ?, ?, ?, ?)",
            (
                "instrument",
                "fake-output",
                "run",
                "unknown-owner",
                "quarantined",
                datetime.now(UTC).isoformat(),
            ),
        )
    report = drain.report(workers, rounds, 0)
    assert not report.ready
    assert report.blockers[0].identity == "instrument:fake-output"
    with sqlite.write_transaction() as connection:
        connection.execute("DELETE FROM resource_claims")
        document = wait.run.model_dump(mode="json")
        document.pop("exit_scope")
        connection.execute(
            "UPDATE procedure_runs SET run_json = ?", (json.dumps(document),)
        )
    assert "Legacy" in drain.report(workers, rounds, 0).blockers[0].reason
    replayed = service.submit(
        ProcedureSubmitCommand(
            definition=wait.run.definition,
            intent=wait.run.intent,
            source=wait.run.source,
            request_key=wait.run.request_key,
        )
    ).run
    assert replayed.exit_scope is None
    assert replayed.state == "waiting_for_input"
    with sqlite.write_transaction() as connection:
        document["exit_scope"] = "current"
        document["state"] = "ready"
        document["resource_wait"] = {"step_key": "child", "run_id": "queued-child"}
        connection.execute(
            "UPDATE procedure_runs SET state = 'ready', run_json = ?",
            (json.dumps(document),),
        )
    assert "resources" in drain.report(workers, rounds, 0).blockers[0].reason
    command = ProcedureSubmitCommand(
        definition=_definition(),
        request_key="deferred",
        intent={},
        exit_scope="subsequent",
    )
    run = service.submit(command).run
    with pytest.raises(BackendConflict, match="exit scope"):
        service.submit(command.model_copy(update={"exit_scope": "current"}))
    assert workers._managed is not None
    workers._managed[run.procedure_run_id] = "paused"
    assert any(
        item.identity == run.procedure_run_id and "paused" in item.reason
        for item in drain.report(workers, rounds, 0).blockers
    )
    sqlite.close()


def test_http_drain_retains_schedules_and_reopens_admission_after_failed_shutdown(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=_config(), instrument_endpoint=signal_endpoint()
    ) as runtime:
        schedule = runtime.application.procedure_schedules.create(
            _command(due_at=datetime.now(UTC) + timedelta(days=20))
        )
        reject = Mock(return_value=False)
        client = TestClient(runtime.app(request_shutdown=reject))
        assert client.post("/api/v1/application-exit?wait=true").status_code == 204
        report = client.get("/api/v1/application-exit")
        assert report.status_code == 200, report.text
        assert report.json()["deferred"][0]["kind"] == "schedule"
        for path in (
            "procedures",
            "runs",
            "author-procedures/submit",
            "procedures/id/steps/question/attempts/1/input",
            "procedures/id/steps/question/attempts/1/input/continue",
            "procedure-schedule-materializations/schedule-1",
        ):
            assert client.post("/api/v1/" + path, json={}).status_code == 409
        assert (
            client.post(
                "/api/v1/shutdown?only_if_idle=true",
                headers={"X-Scopecat-Shutdown-Token": "fixture"},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/application-exit").json()["draining"]
        assert client.post("/api/v1/application-exit?wait=false").status_code == 204
        # Malformed ordinary request now reaches validation rather than drain.
        assert client.post("/api/v1/procedures", json={}).status_code == 422
        assert (
            runtime.application.procedure_schedules.get(schedule.schedule.schedule_id)
            == schedule.schedule
        )
    with LocalDaemonRuntime(tmp_path) as reopened:
        assert (
            reopened.application.procedure_schedules.get(schedule.schedule.schedule_id)
            == schedule.schedule
        )


def test_drain_counts_inflight_mutation_and_fences_new_request(tmp_path: Path) -> None:
    entered, finish = Event(), Event()
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=_config(), instrument_endpoint=signal_endpoint()
    ) as runtime:
        app = runtime.app(request_shutdown=lambda _: True)

        @app.post("/api/v1/fixture-write")
        def write() -> dict[str, bool]:
            entered.set()
            assert finish.wait(5)
            return {"saved": True}

        with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, "/api/v1/fixture-write")
            assert entered.wait(5)
            try:
                assert (
                    client.post("/api/v1/application-exit?wait=true").status_code == 204
                )
                report = client.get("/api/v1/application-exit")
                assert report.status_code == 200, report.text
                assert any(
                    item["kind"] == "requests" for item in report.json()["finishing"]
                )
                assert client.post("/api/v1/fixture-write").status_code == 409
                assert (
                    client.post(
                        "/api/v1/shutdown?only_if_idle=true",
                        headers={"X-Scopecat-Shutdown-Token": "fixture"},
                    ).status_code
                    == 409
                )
            finally:
                finish.set()
            assert pending.result().status_code == 200
            assert client.get("/api/v1/application-exit").json()["finishing"] == []


@analysis_step(id="exit-evidence")
def _exit_evidence(context: AnalysisContext) -> Analysis:
    context.measurements()
    return context.result("Retained exit evidence").fact("checked", True)


@procedure(id="exit-sdk", version="1", intent=_ExitIntent)
def _exit_workflow(context: LabProcedureContext, intent: _ExitIntent) -> None:
    child = context.run(
        "child", load_invocation(), config=load_config(), setup=intent.setup
    )
    result = context.analyze_run("evidence", child, _exit_evidence())
    context.interpret(
        "confirm",
        title="Confirm evidence",
        instructions="Review without remeasurement",
        schema=AnalysisFactSchema("exit-confirm.v1", _ExitAnswer),
        inputs=(child, result),
    )


def test_admitted_sdk_child_analysis_and_human_wait_finish_during_drain(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(
            tmp_path,
            bootstrap_config=load_config(),
            instrument_endpoint=signal_endpoint(),
        ) as runtime,
        TestClient(runtime.app(request_shutdown=lambda _: True)) as transport,
        LabClient(
            _daemon_client(transport), procedures=ProcedureRegistry((_exit_workflow,))
        ) as lab,
    ):
        selected_setup = runtime.application.setup.resolve("initial").ref
        submitted = lab.procedures.submit(
            _exit_workflow,
            {"setup": selected_setup.model_dump(mode="json")},
            request_key="sdk",
        )
        assert transport.post("/api/v1/application-exit?wait=true").status_code == 204
        waiting = lab.procedures.resume(submitted)
        stored = runtime.application.automation.get(waiting.id)
        assert stored.state == "waiting_for_input", stored
        steps = runtime.application.automation.step_attempts(
            waiting.id, ProcedureStepAttemptListQuery()
        ).items
        assert {step.state for step in steps} == {"succeeded", "waiting_for_input"}
        question = next(step for step in steps if step.state == "waiting_for_input")
        assert len(question.inputs) == 2
        # This external notebook worker lacks a managed success receipt and
        # retained author source: the safe conservative boundary stays visible.
        report = transport.get("/api/v1/application-exit").json()
        assert report["blockers"]
        with pytest.raises(Exception, match="Exit wait"):
            lab.procedures.respond(
                waiting,
                "confirm",
                _ExitAnswer(accepted=True),
                schema=AnalysisFactSchema("exit-confirm.v1", _ExitAnswer),
                actor="fixture",
            )
    with LocalDaemonRuntime(tmp_path) as reopened:
        assert reopened.application.automation.get(stored.procedure_run_id) == stored
        assert (
            reopened.application.automation.step_attempts(
                stored.procedure_run_id, ProcedureStepAttemptListQuery()
            ).items
            == steps
        )


@pytest.mark.parametrize("scope", ["current", "subsequent", "maintenance"])
def test_task_scope_controls_new_rounds_without_cancelling_plan(
    tmp_path: Path, scope: str
) -> None:
    from scopecat.daemon.calibration_tasks import CalibrationTaskControl

    from scopecat_server.services.calibration_task_runner import CalibrationTaskRunner

    from .test_calibration_check_admission import _check_case, _task

    with _check_case(tmp_path) as (runtime, check, _):
        tasks = runtime.application.calibration_tasks
        spec = _task(check).model_copy(update={"exit_scope": scope})
        created = tasks.create(spec)
        started = tasks.control(
            CalibrationTaskControl(
                task_id=spec.task_id,
                expected_revision=created.task.control_revision,
                action="start",
                actor="fixture",
                reason="bounded exit test",
            )
        )
        rounds = BackgroundRounds()
        rounds.drain(True)
        runner = CalibrationTaskRunner(tasks, Mock(), rounds=rounds)
        runner.tick()
        after = tasks.get(spec.task_id)
        assert after.task.mode == "running"
        if scope == "current":
            assert after.task.executions
        else:
            assert after.task == started.task
            report = runtime.application.exit_drain.report(
                _workers(tmp_path), rounds, 0
            )
            assert report.ready
            assert any(item.identity == spec.task_id for item in report.deferred)
        rounds.drain(False)
        runner.tick()
        assert tasks.get(spec.task_id).task.executions
