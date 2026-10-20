"""Opening retains the object's connection and an uncertain request's identity."""

from pathlib import Path
from types import SimpleNamespace
from typing import Literal, override
from unittest.mock import Mock
from uuid import uuid4

import httpx2
import pytest

from scopecat.api._remote import RemoteRunOperations
from scopecat.api.application_open import ApplicationOpenTimeout, open_in_application
from scopecat.api.project_analysis import RemoteProjectAnalysisOperations
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.application.author_procedures import AuthorProcedure
from scopecat.daemon.client import DaemonClient
from scopecat.records.analysis import SampleAnalysisSubject
from scopecat.records.application_open import (
    ApplicationOpenCommand,
    ApplicationOpenReceipt,
    ApplicationOpenTarget,
)


def opened_receipt(
    command: ApplicationOpenCommand, *, timeout: float
) -> ApplicationOpenReceipt:
    assert timeout > 0
    return ApplicationOpenReceipt(
        request_id=command.request_id, target=command.target, state="opened"
    )


def opening_receipt(
    command: ApplicationOpenCommand, *, timeout: float
) -> ApplicationOpenReceipt:
    assert timeout > 0
    return ApplicationOpenReceipt(
        request_id=command.request_id, target=command.target, state="opening"
    )


def connected_client() -> Mock:
    client = Mock(spec=DaemonClient)
    client.health.return_value = SimpleNamespace(
        project_id="project", deployment_id="app"
    )
    client.application_open_host.return_value = SimpleNamespace(host_id=uuid4())
    client.open_application_object.side_effect = opened_receipt
    return client


@pytest.mark.parametrize("lost_transport", [False, True])
def test_timeout_retry_uses_same_command_and_host(lost_transport: bool) -> None:
    client = connected_client()
    target = ApplicationOpenTarget(kind="run", object_id="measurement")
    if lost_transport:
        client.open_application_object.side_effect = httpx2.ConnectError(
            "lost response"
        )
    else:
        client.open_application_object.side_effect = opening_receipt
    with pytest.raises(ApplicationOpenTimeout) as caught:
        open_in_application(client, target, timeout=0.001)
    command = client.open_application_object.call_args.args[0]
    client.open_application_object.side_effect = opened_receipt
    assert caught.value.request.wait(timeout=1).state == "opened"
    assert client.open_application_object.call_args.args[0] is command
    client.application_open_host.assert_called_once()


def test_missing_desktop_does_not_submit_open_or_scientific_work():
    client = connected_client()
    client.application_open_host.side_effect = ValueError(
        "Open this application's desktop"
    )
    with pytest.raises(ValueError, match="desktop"):
        AuthorProcedure(client, "task").open()
    client.open_application_object.assert_not_called()
    client.submit_author_procedure.assert_not_called()


@pytest.mark.parametrize("owner", ["project", "run", "sample"])
def test_publication_preserves_exact_owner_and_record(
    owner: Literal["project", "run", "sample"],
) -> None:
    client = connected_client()
    if owner == "run":
        source = RunHandle(Mock(run_operations=RemoteRunOperations(client)), "r")
    elif owner == "sample":
        source = RemoteProjectAnalysisOperations(
            client, SampleAnalysisSubject(sample_id="s")
        )
    else:
        source = RemoteProjectAnalysisOperations(client)
    publication = PublishedAnalysis(source, Mock(entry=SimpleNamespace(id="record")))
    publication.open(timeout=1)
    command = client.open_application_object.call_args.args[0]
    assert command.target.object_id == "record"
    assert (
        command.target.kind
        == {"project": "analysis", "run": "run_analysis", "sample": "sample_analysis"}[
            owner
        ]
    )
    assert (
        command.target.owner_id == {"project": None, "run": "r", "sample": "s"}[owner]
    )
    assert command.project_id == "project"
    assert command.deployment_id == "app"


def test_delayed_http_response_obeys_wait_budget_and_retries_same_request():
    import threading
    import time
    from contextlib import suppress
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from scopecat.api.application_open import ApplicationOpenRequest

    received: list[ApplicationOpenCommand] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            command = ApplicationOpenCommand.model_validate_json(
                self.rfile.read(int(self.headers["Content-Length"]))
            )
            received.append(command)
            if len(received) == 1:
                time.sleep(0.3)
            payload = (
                ApplicationOpenReceipt(
                    request_id=command.request_id, target=command.target, state="opened"
                )
                .model_dump_json()
                .encode()
            )
            with suppress(BrokenPipeError, ConnectionResetError):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                _ = self.wfile.write(payload)

        @override
        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        with DaemonClient(f"http://127.0.0.1:{server.server_port}") as client:
            command = ApplicationOpenCommand(
                project_id="project",
                deployment_id="app",
                host_id=uuid4(),
                request_id=uuid4(),
                target=ApplicationOpenTarget(kind="run", object_id="r"),
            )
            request = ApplicationOpenRequest(client, command)
            started = time.monotonic()
            with pytest.raises(ApplicationOpenTimeout) as caught:
                request.wait(timeout=0.02)
            assert time.monotonic() - started < 0.2
            assert caught.value.request is request
            assert request.wait(timeout=1).state == "opened"
            assert received == [command, command]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_task_lookup_uses_open_budget_without_submitting(tmp_path: Path) -> None:
    import time

    from scopecat.application.author_project import AuthorJob

    client = connected_client()

    def lookup(*args: object, timeout: float) -> SimpleNamespace:
        assert args and 0 < timeout <= 0.01
        time.sleep(0.02)
        return SimpleNamespace(items=(SimpleNamespace(procedure_run_id="p"),))

    client.list_procedures.side_effect = lookup
    job = AuthorJob(client, tmp_path / "receipt", Mock(request_key="retained"))
    with pytest.raises(TimeoutError, match="no open request sent"):
        job.open(timeout=0.01)
    client.open_application_object.assert_not_called()
    client.submit_author_procedure.assert_not_called()
