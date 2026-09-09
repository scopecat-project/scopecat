from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureStepAttemptPage,
    ProcedureSubmitCommand,
    procedure_step_operation_id,
)
from scopecat.daemon.views import RunDetail
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.manual_preview import ManualPreviewBinding, ManualPreviewFence

from scopecat_server.http.procedure_operator import read_procedure_operator
from scopecat_server.http.transport import create_app
from scopecat_server.services.application import DaemonApplication
from scopecat_server.services.manual_previews import ManualPreviewService
from scopecat_server.services.project_workers import ProjectProcedureWorkers
from scopecat_server.storage.sqlite.connection import SQLiteDatabase

NOW = datetime(2026, 9, 1, tzinfo=UTC)
HASH = "sha256:" + "1" * 64


def _application(root: Path) -> tuple[DaemonApplication, Mock]:
    command = ProcedureSubmitCommand(
        request_key="retained",
        definition=ProcedureDefinitionRef(
            id="diagnostic",
            version="1",
            fingerprint=HASH,
        ),
        intent={},
    )
    procedure = ProcedureRun(
        procedure_run_id="procedure-1",
        request_key=command.request_key,
        definition=command.definition,
        intent=command.intent,
        intent_hash=command.intent_hash,
        revision=3,
        state="ready",
        created_at=NOW,
        updated_at=NOW,
    )
    step = ProcedureStepAttempt(
        procedure_run_id=procedure.procedure_run_id,
        step_key="acquire",
        attempt=1,
        operation="run",
        intent_hash=HASH,
        revision=1,
        state="running",
        started_at=NOW,
        updated_at=NOW,
    )
    child = RunDetail.model_validate(
        {
            "control": {
                "sequence": 1,
                "state": "attention_required",
                "updated_at": NOW,
                "attention_reason": "hardware response lost",
                "completed_point_count": 1,
                "admission": {
                    "run_id": "admitted-child",
                    "run_contract_fingerprint": "1" * 64,
                    "admitted_at": NOW,
                    "plan": {
                        "experiment_id": "diagnostic",
                        "experiment_kind": "diagnostic",
                        "point_plan_fingerprint": "1" * 64,
                        "measurement_contract_fingerprint": "1" * 64,
                        "initial_point_count": 1,
                        "point_limit": 1,
                    },
                },
                "point_plan": {
                    "run_id": "admitted-child",
                    "initial_point_count": 1,
                    "accepted_point_count": 1,
                    "point_limit": 1,
                    "decision_count": 0,
                    "optimizer_attempt_count": 0,
                    "operator_request_count": 0,
                    "plan_closed": True,
                    "stop_reason": "complete",
                },
            },
            "snapshot": {"run_id": "admitted-child", "config_content_hash": HASH},
        }
    )
    lookup = Mock(return_value=child)
    application = cast(
        "DaemonApplication",
        cast(
            "object",
            SimpleNamespace(
                project_root=root,
                manual_previews=ManualPreviewService(
                    SQLiteDatabase(root / "store.sqlite3"), Mock()
                ),
                automation=SimpleNamespace(
                    get=Mock(return_value=procedure),
                    running_step=Mock(return_value=step),
                    step_attempts=Mock(
                        return_value=ProcedureStepAttemptPage(
                            procedure_run_id=procedure.procedure_run_id,
                            items=(),
                        )
                    ),
                ),
                runs=SimpleNamespace(find_run_by_submission_id=lookup),
            ),
        ),
    )
    return application, lookup


def test_current_child_and_dispatch_gate_do_not_depend_on_history_page(
    tmp_path: Path,
) -> None:
    application, lookup = _application(tmp_path)
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    first = read_procedure_operator(application, manager, "procedure-1")
    older = read_procedure_operator(application, manager, "procedure-1", cursor=1)
    assert first.current_child == older.current_child
    assert older.current_child is not None
    assert older.current_child.run.run_id == "admitted-child"
    assert first.dispatch_blocked_reason == older.dispatch_blocked_reason
    assert "unknown outcome" in (older.dispatch_blocked_reason or "")
    assert older.steps.items == ()
    lookup.assert_called_with(procedure_step_operation_id("procedure-1", "acquire"))
    assert not (tmp_path / ".scopecat/console-procedures.json").exists()


@pytest.mark.parametrize("via_submit", [False, True])
def test_unknown_child_never_dispatches_including_exact_submission_retry(
    tmp_path: Path, via_submit: bool
) -> None:
    application, _ = _application(tmp_path)
    from scopecat.records.launch_request import LaunchRequest
    from scopecat.records.run import ConfigRegistryRunConfigSource

    request = LaunchRequest(action="preview", experiment="diagnostic", version="1")
    command = request.model_copy(
        update={
            "action": "submit",
            "request_key": "retained",
            "expected_request_hash": request.request_hash,
            "config_source": ConfigRegistryRunConfigSource(
                selector="active",
                entry_id="baseline",
                config_ref="baseline",
                content_hash=HASH,
                registry_generation=1,
            ),
        }
    )
    assert command.config_source is not None
    command = command.model_copy(
        update={
            "manual_state": ManualPreviewFence(
                event_id=1,
                binding=ManualPreviewBinding(
                    request_hash=command.request_hash,
                    config_source_hash=sha256_json_hash(
                        command.config_source.model_dump(mode="json")
                    ),
                ),
            )
        }
    )
    with (
        patch(
            "scopecat_server.http.transport.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout='{"procedure_id":"procedure-1"}',
                stderr="",
            ),
        ),
        patch.object(ProjectProcedureWorkers, "dispatch") as dispatch,
    ):
        client = TestClient(create_app(application))
        response = (
            client.post(
                "/api/v1/experiment-launcher/submit",
                json=command.model_dump(mode="json"),
            )
            if via_submit
            else client.post("/api/v1/procedures/procedure-1/dispatch")
        )
    if via_submit:
        assert response.status_code == 200
        assert response.json()["procedure_id"] == "procedure-1"
    else:
        assert response.status_code == 409
        assert "unknown outcome" in response.json()["detail"]
    dispatch.assert_not_called()


def test_dispatch_snapshot_survives_restart_and_read_never_spawns(
    tmp_path: Path,
) -> None:
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    with (
        patch.object(manager, "_spawn", side_effect=OSError("spawn failed")),
        pytest.raises(OSError, match="spawn failed"),
    ):
        manager.dispatch("procedure-1")
    restarted = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    with patch.object(restarted, "_spawn") as spawn:
        view = restarted.snapshot("procedure-1")
    assert view.management == "paused" and not view.worker_running
    spawn.assert_not_called()
