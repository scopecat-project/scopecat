"""Author task preparation owns retries; observations never advance execution."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import pytest

from scopecat.application.author_calibration_tasks import AuthorCalibrationTasks
from scopecat.automation import ProcedureDefinitionRef
from scopecat.automation.calibration_tasks import CalibrationTaskPlan
from scopecat.daemon.calibration_tasks import CalibrationTaskCall
from scopecat.daemon.client import DaemonClient
from scopecat.kernel.errors import SessionClosedError
from scopecat.records.author_revision import AuthorRevisionRef

from .test_calibration_tasks import HASH, _stage


def test_preparation_detaches_inputs_and_fences_reconnection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock(spec=DaemonClient)
    client.is_closed = False
    client.workspace_id = "source-workspace"
    client.current_author_source.return_value = AuthorRevisionRef(content_hash=HASH)
    client.health.return_value = SimpleNamespace(
        project_id="store", deployment_id="app"
    )
    monkeypatch.setattr(
        "scopecat.application.author_imports.notebook_imports_selected",
        Mock(return_value=True),
    )
    stage = _stage("fit")
    calls = {
        "fit": CalibrationTaskCall(
            definition=ProcedureDefinitionRef(
                id="check", version="1", fingerprint=HASH
            ),
            intent={"calibration_check": stage.check.model_dump(mode="json")},
        )
    }
    operations = AuthorCalibrationTasks(client, Path("/source"))
    prepared = operations.prepare(
        "round", CalibrationTaskPlan(stages=(stage,)), calls=calls
    )
    original = prepared.command
    calls.clear()
    prepared.command.calls.clear()
    assert prepared.command == original
    client.create_calibration_task.assert_not_called()
    prepared.submit()
    prepared.submit()
    assert [c.args[0] for c in client.create_calibration_task.call_args_list] == [
        original,
        original,
    ]
    client.control_calibration_task.assert_not_called()
    other = Mock(spec=DaemonClient)
    other.workspace_id = "wrong-workspace"
    with pytest.raises(ValueError, match="another workspace"):
        prepared.reconnect(other)
    other.health.assert_not_called()
    other.workspace_id = client.workspace_id
    for project, deployment in (("other-store", "app"), ("store", "other-app")):
        other.health.return_value = SimpleNamespace(
            project_id=project, deployment_id=deployment
        )
        with pytest.raises(ValueError, match="another data store or deployment"):
            prepared.reconnect(other)
    other.create_calibration_task.assert_not_called()
    other.health.return_value = client.health.return_value
    rebound = prepared.reconnect(other)
    rebound.submit()
    assert other.create_calibration_task.call_args.args == (original,)
    # Recheck at submission, even after a previously valid reconnect.
    other.health.return_value = SimpleNamespace(
        project_id="changed", deployment_id="app"
    )
    with pytest.raises(ValueError, match="another data store"):
        rebound.submit()
    assert other.create_calibration_task.call_count == 1
    client.is_closed = True
    with pytest.raises(SessionClosedError):
        operations.prepare("closed", original.plan, calls=original.calls)


def _view():
    return SimpleNamespace(
        task=SimpleNamespace(
            mode="running", dispatch_errors={}, finalization_error=None
        ),
        progress=SimpleNamespace(stages=()),
        finalization=None,
    )


def test_wait_timeout_never_controls_or_dispatches() -> None:
    client = Mock(spec=DaemonClient)
    client.get_calibration_task.return_value = _view()
    operations = AuthorCalibrationTasks(client, None)
    with pytest.raises(TimeoutError, match="not cancelled"):
        operations.wait("active", timeout=0)
    assert client.method_calls == [
        ("get_calibration_task", ("active",), {"timeout": 0.001})
    ]
    client.get_calibration_task.side_effect = httpx2.ReadTimeout("slow")
    with pytest.raises(TimeoutError, match="not cancelled"):
        operations.wait("active", timeout=0.1)
    assert client.get_calibration_task.call_args.kwargs["timeout"] <= 0.1
    client.control_calibration_task.assert_not_called()
    client.dispatch_calibration_task.assert_not_called()


@pytest.mark.parametrize(
    "stop",
    [
        "manual",
        "paused",
        "cancelled",
        "finished",
        "admission",
        "finalization",
        "stage_attention",
        "stage_input",
        "final_attention",
        "final_input",
    ],
)
def test_wait_preserves_explicit_continue_and_scientific_failure(stop: str) -> None:
    client = Mock(spec=DaemonClient)
    view = _view()
    if stop in {"manual", "paused", "cancelled", "finished"}:
        view.task.mode = stop
    elif stop == "admission":
        view.task.dispatch_errors = {"fit": "rejected admission"}
    elif stop == "finalization":
        view.task.finalization_error = "rejected finalization admission"
    else:
        state = (
            "attention_required" if stop.endswith("attention") else "waiting_for_input"
        )
        if stop.startswith("stage"):
            view.progress.stages = (SimpleNamespace(state=state),)
        else:
            view.finalization = SimpleNamespace(state=state)
    client.get_calibration_task.return_value = view
    assert AuthorCalibrationTasks(client, None).wait("task") is view
    assert len(client.method_calls) == 1


@pytest.mark.parametrize(
    ("timeout", "interval"),
    [
        (float("nan"), 1),
        (float("inf"), 1),
        (-1, 1),
        (1, 0),
        (1, -1),
        (1, float("nan")),
        (1, float("inf")),
    ],
)
def test_wait_rejects_invalid_budget(timeout: float, interval: float) -> None:
    client = Mock(spec=DaemonClient)
    with pytest.raises(ValueError, match="finite"):
        AuthorCalibrationTasks(client, None).wait(
            "task", timeout=timeout, interval=interval
        )
    assert not client.method_calls


@pytest.mark.parametrize("action", ["start", "pause", "cancel"])
def test_controls_keep_existing_revision_fence(action: str) -> None:
    client = Mock(spec=DaemonClient)
    view = _view()
    view.task.specification = SimpleNamespace(task_id="retained")
    view.task.control_revision = 7
    operations = AuthorCalibrationTasks(client, None)
    result = getattr(operations, action)(
        view, actor="learner", reason="explicit choice"
    )
    assert result is client.control_calibration_task.return_value
    command = client.control_calibration_task.call_args.args[0]
    assert command.task_id == "retained"
    assert command.expected_revision == 7
    assert command.action == action
    assert command.actor == "learner"
    assert command.reason == "explicit choice"
    assert len(client.method_calls) == 1
