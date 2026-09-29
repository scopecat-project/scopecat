import time
from pathlib import Path

import httpx2
from scopecat.automation import (
    ProcedureStepAttemptListQuery,
    ProcedureStepInputSubmitCommand,
)
from scopecat.daemon.client import DaemonClient
from scopecat.project import open_project
from scopecat.records.practice import PracticeClearCommand, PracticeCreateCommand

from scopecat_server.lifecycle import start_project, stop_project
from scopecat_server.snapshots import create_snapshot, restore_snapshot


def _wait(client: DaemonClient, identity: str, state: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = client.get_procedure(identity)
        if run.state == state:
            return
        if run.state in {"closed", "attention_required"}:
            raise AssertionError(run.model_dump_json())
        time.sleep(0.1)
    raise AssertionError(client.get_procedure(identity).model_dump_json())


def test_practice_restores_decision_without_reacquisition_then_clears_owned_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "application"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("practice")
    project = open_project(root)
    try:
        endpoint = start_project(project, static_dir=gui)
        with DaemonClient(endpoint.base_url) as client:
            scope = client.create_practice(PracticeCreateCommand(request_key="lesson"))
            assert scope.procedure_id is not None
            _wait(client, scope.procedure_id, "waiting_for_input")
            steps = client.list_procedure_step_attempts(
                scope.procedure_id, ProcedureStepAttemptListQuery()
            ).items
            scan = next(item for item in steps if item.step_key == "scan").output
            note = Path(scope.directory) / "notes.txt"
            note.write_text("my observation")
        stop_project(project)
        snapshot = tmp_path / "backup"
        create_snapshot(project, snapshot)
        restored = tmp_path / "restored"
        restore_snapshot(snapshot, restored)
        project = open_project(restored)
        endpoint = start_project(project, static_dir=gui)
        with (
            DaemonClient(endpoint.base_url) as client,
            httpx2.Client(trust_env=False) as web,
        ):
            scope = client.practice(scope.id)
            assert scope.procedure_id is not None
            assert Path(scope.directory).is_relative_to(restored)
            assert (Path(scope.directory) / "notes.txt").read_text() == "my observation"
            task = client.get_procedure(scope.procedure_id)
            assert task.state == "waiting_for_input"
            steps = client.list_procedure_step_attempts(
                task.procedure_run_id, ProcedureStepAttemptListQuery()
            ).items
            assert (
                next(item for item in steps if item.step_key == "scan").output == scan
            )
            waiting = next(item for item in steps if item.state == "waiting_for_input")
            assert waiting.interpretation_request is not None
            client.submit_procedure_step_input(
                ProcedureStepInputSubmitCommand(
                    procedure_run_id=task.procedure_run_id,
                    expected_run_revision=task.revision,
                    step_key=waiting.step_key,
                    attempt=waiting.attempt,
                    expected_step_revision=waiting.revision,
                    request_hash=waiting.interpretation_request.request_hash,
                    actor="practice verification",
                    actor_kind="service",
                    value={"outcome": "Peak selected", "frequency_mhz": 6500},
                )
            )
            response = web.post(
                endpoint.base_url
                + f"/api/v1/procedures/{task.procedure_run_id}/dispatch"
            )
            assert response.status_code == 200, response.text
            assert response.json()["dispatch_error"] is None
            _wait(client, task.procedure_run_id, "closed")
            completed = client.get_procedure(task.procedure_run_id)
            assert completed.closure is not None
            assert completed.closure.status == "succeeded"
            cleared = client.clear_practice(
                scope.id, PracticeClearCommand(files="preserve")
            )
            assert cleared.state == "cleared", cleared.cleanup_error
            assert (
                Path(cleared.directory) / "notes.txt"
            ).read_text() == "my observation"
            assert (
                web.get(
                    endpoint.base_url + f"/api/v1/procedures/{task.procedure_run_id}"
                ).status_code
                == 404
            )
            assert not list((restored / ".scopecat/objects/resources").rglob("*.tmp"))
    finally:
        stop_project(project)
