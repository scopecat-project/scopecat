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
    assert AuthorProcedure(client, "retained").wait() is snapshot
    assert len(client.method_calls) == 1


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
