"""Opening retains the object's connection and an uncertain request's identity."""

from types import SimpleNamespace
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
    ApplicationOpenReceipt,
    ApplicationOpenTarget,
)


def connected_client():
    client = Mock(spec=DaemonClient)
    client.health.return_value = SimpleNamespace(
        project_id="project", deployment_id="app"
    )
    client.application_open_host.return_value = SimpleNamespace(host_id=uuid4())
    client.open_application_object.side_effect = lambda command: ApplicationOpenReceipt(
        request_id=command.request_id, target=command.target, state="opened"
    )
    return client


@pytest.mark.parametrize("lost_transport", [False, True])
def test_timeout_retry_uses_same_command_and_host(lost_transport):
    client = connected_client()
    target = ApplicationOpenTarget(kind="run", object_id="measurement")
    if lost_transport:
        client.open_application_object.side_effect = httpx2.ConnectError(
            "lost response"
        )
    else:
        client.open_application_object.side_effect = lambda command: (
            ApplicationOpenReceipt(
                request_id=command.request_id, target=command.target, state="opening"
            )
        )
    with pytest.raises(ApplicationOpenTimeout) as caught:
        open_in_application(client, target, timeout=0)
    command = client.open_application_object.call_args.args[0]
    client.open_application_object.side_effect = lambda command: ApplicationOpenReceipt(
        request_id=command.request_id, target=command.target, state="opened"
    )
    assert caught.value.request.wait(timeout=0).state == "opened"
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
def test_publication_preserves_exact_owner_and_record(owner):
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
    publication.open(timeout=0)
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
