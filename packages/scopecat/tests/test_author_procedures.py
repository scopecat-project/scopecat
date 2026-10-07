"""Prepared procedure identity fences reject before any submission can escape."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scopecat.application.author_procedures import PreparedAuthorProcedure
from scopecat.automation import ProcedureDefinitionRef
from scopecat.automation.models import ProcedureSource
from scopecat.automation.wire import ProcedureSubmitCommand
from scopecat.daemon.client import DaemonClient
from scopecat.records.author_revision import AuthorRevisionRef


def test_prepared_procedure_fences_reconnection_and_rechecks_submission() -> None:
    client = Mock(spec=DaemonClient)
    client.workspace_id = "source-workspace"
    client.health.return_value = SimpleNamespace(
        project_id="store", deployment_id="app"
    )
    command = ProcedureSubmitCommand(
        request_key="ordinary-retry",
        definition=ProcedureDefinitionRef(
            id="ordinary", version="1", fingerprint="sha256:" + "1" * 64
        ),
        intent={"label": "Review"},
        source=ProcedureSource(
            workspace_id=client.workspace_id,
            code_revision=AuthorRevisionRef(content_hash="sha256:" + "2" * 64),
        ),
    )
    prepared = PreparedAuthorProcedure(client, command, "store", "app")
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
    other.submit_author_procedure.assert_not_called()
    client.submit_author_procedure.assert_not_called()

    other.health.return_value = client.health.return_value
    rebound = prepared.reconnect(other)
    assert rebound.command is command
    rebound.submit()
    other.submit_author_procedure.assert_called_once_with(command)
    # A formerly valid connection must still fail before admission if its identity
    # changes. No worker or second daemon is needed to exercise this client fence.
    other.health.return_value = SimpleNamespace(
        project_id="changed", deployment_id="app"
    )
    with pytest.raises(ValueError, match="another data store"):
        rebound.submit()
    other.submit_author_procedure.assert_called_once_with(command)
