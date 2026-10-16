"""Managed procedure observations never become execution or retry authority."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import pytest

from scopecat.application.author_procedures import AuthorProcedure
from scopecat.daemon.client import DaemonClient


def test_wait_timeout_leaves_work_running() -> None:
    client = Mock(spec=DaemonClient)
    client.get_procedure.return_value = SimpleNamespace(closure=None, state="leased")
    procedure = AuthorProcedure(client, "running")
    with pytest.raises(TimeoutError, match="not cancelled"):
        procedure.wait(timeout=0)
    assert client.method_calls == [("get_procedure", ("running",), {"timeout": 0.001})]
    client.get_procedure.side_effect = httpx2.ReadTimeout("slow read")
    with pytest.raises(TimeoutError, match="not cancelled"):
        procedure.wait(timeout=0.1)
    assert client.get_procedure.call_args.kwargs["timeout"] <= 0.1
    client.cancel_procedure.assert_not_called()
    client.dispatch_project_procedure.assert_not_called()


@pytest.mark.parametrize("state", ["closed", "waiting_for_input", "attention_required"])
def test_wait_returns_retained_stop_without_retry(state: str) -> None:
    client = Mock(spec=DaemonClient)
    snapshot = SimpleNamespace(
        closure=SimpleNamespace(status="failed") if state == "closed" else None,
        state=state,
    )
    client.get_procedure.return_value = snapshot
    client.procedure_progress.return_value = SimpleNamespace(
        procedure=snapshot, dispatch=SimpleNamespace(worker_running=False)
    )
    assert AuthorProcedure(client, "retained").wait() is snapshot
    assert len(client.method_calls) == (2 if state == "waiting_for_input" else 1)


def test_output_pages_and_does_not_fall_back_from_newest_incomplete_attempt() -> None:
    client = Mock(spec=DaemonClient)
    output = object()
    client.list_procedure_step_attempts.side_effect = [
        SimpleNamespace(items=(), next_cursor=5),
        SimpleNamespace(
            items=(SimpleNamespace(step_key="fit", state="succeeded", output=output),),
            next_cursor=None,
        ),
    ]
    procedure = AuthorProcedure(client, "retained")
    assert procedure.output("fit") is output
    assert client.list_procedure_step_attempts.call_args.args[1].cursor == 5
    client.list_procedure_step_attempts.side_effect = None
    client.list_procedure_step_attempts.return_value = SimpleNamespace(
        items=(SimpleNamespace(step_key="fit", state="running", output=None),),
        next_cursor=4,
    )
    with pytest.raises(RuntimeError, match="no successful output"):
        procedure.output("fit")
    client.cancel_procedure.assert_not_called()
    client.dispatch_project_procedure.assert_not_called()


@pytest.mark.parametrize(
    ("timeout", "interval"),
    [
        (float("nan"), 0.2),
        (float("inf"), 0.2),
        (float("-inf"), 0.2),
        (-1, 0.2),
        (60, float("nan")),
        (60, float("inf")),
        (60, float("-inf")),
        (60, 0),
        (60, -1),
    ],
)
def test_wait_rejects_invalid_budgets_before_reading(
    timeout: float, interval: float
) -> None:
    client = Mock(spec=DaemonClient)
    with pytest.raises(ValueError, match="finite"):
        AuthorProcedure(client, "retained").wait(timeout=timeout, interval=interval)
    assert not client.method_calls


def test_wait_for_exiting_worker_is_bounded_and_interruptible() -> None:
    client = Mock(spec=DaemonClient)
    snapshot = SimpleNamespace(closure=None, state="waiting_for_input")
    client.get_procedure.return_value = snapshot
    client.procedure_progress.return_value = SimpleNamespace(
        procedure=snapshot, dispatch=SimpleNamespace(worker_running=True)
    )
    procedure = AuthorProcedure(client, "retained")
    with pytest.raises(TimeoutError, match="not cancelled"):
        procedure.wait(timeout=0)
    assert client.procedure_progress.call_args.kwargs["timeout"] == 0.001
    client.procedure_progress.side_effect = httpx2.ReadTimeout("slow operator read")
    with pytest.raises(TimeoutError, match="not cancelled"):
        procedure.wait(timeout=0.1)
    assert client.procedure_progress.call_args.kwargs["timeout"] <= 0.1
    client.procedure_progress.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        procedure.wait()
    client.cancel_procedure.assert_not_called()
    client.submit_procedure_step_input.assert_not_called()
    client.dispatch_project_procedure.assert_not_called()


@pytest.mark.parametrize("state", ["ready", "leased", "waiting_for_input"])
def test_wait_observes_concurrent_continuation_without_dispatch(state: str) -> None:
    client = Mock(spec=DaemonClient)
    waiting = SimpleNamespace(closure=None, state="waiting_for_input")
    continued = SimpleNamespace(closure=None, state=state)
    closed = SimpleNamespace(closure=object(), state="closed")
    client.get_procedure.side_effect = [waiting, closed]
    # Another caller answers/continues between the durable read and worker read.
    client.procedure_progress.return_value = SimpleNamespace(
        procedure=continued, dispatch=SimpleNamespace(worker_running=True)
    )
    assert AuthorProcedure(client, "retained").wait(interval=0.001) is closed
    client.submit_procedure_step_input.assert_not_called()
    client.dispatch_project_procedure.assert_not_called()


def test_resume_propagates_concurrent_dispatch_conflict_without_retry() -> None:
    from scopecat.daemon.client import DaemonConflictError

    client = Mock(spec=DaemonClient)
    conflict = DaemonConflictError(
        "A worker is already handling this procedure.",
        response=httpx2.Response(409),
    )
    client.dispatch_project_procedure.side_effect = conflict
    with pytest.raises(DaemonConflictError) as raised:
        AuthorProcedure(client, "retained").resume()
    assert raised.value is conflict
    client.dispatch_project_procedure.assert_called_once_with("retained")
    client.submit_procedure_step_input.assert_not_called()
