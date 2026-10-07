"""Ordinary author procedures cross a real daemon and immutable worker boundary."""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest
from scopecat.application.author_imports import release_notebook_imports
from scopecat.application.author_procedures import AuthorProcedure
from scopecat.automation.wire import (
    ProcedureRunListQuery,
    ProcedureStepInputSubmitCommand,
)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from scopecat.project import Project
from scopecat_testkit.project_loading import isolated_project_imports

from scopecat_server.lifecycle import initialize_project, start_project, stop_project

_SOURCE = """\
import time
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import procedure
from scopecat_lab.helper import LABEL

class Intent(BaseModel):
    model_config = ConfigDict(frozen=True)
    label: str
    tags: list[str] = ["source-bound"]
    repetitions: int = 1

@dataclass
class Answer:
    accepted: bool

@procedure(id="ordinary", version="1", intent=Intent)
def ordinary(context: LabProcedureContext, intent: Intent) -> None:
    time.sleep(2)
    context.interpret(
        "confirm",
        title=intent.label,
        instructions=LABEL,
        schema=AnalysisFactSchema("test.answer.v1", Answer),
    )
"""


def _wait(handle: AuthorProcedure, state: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        view = handle.progress()
        if view.procedure.state == state and not view.dispatch.worker_running:
            return
        if view.dispatch.failure:
            pytest.fail(str(view.dispatch.failure))
        time.sleep(0.1)
    pytest.fail(str(handle.progress()))


def test_managed_source_retry_disconnect_and_continue(tmp_path: Path) -> None:
    project = initialize_project(tmp_path / "ordinary")
    manifest = project.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[lab.capabilities]\nprocedures = ["scopecat_lab.workflow:ordinary"]\n'
    )
    source = project.root / "src/scopecat_lab/workflow.py"
    source.write_text(_SOURCE)
    helper = source.with_name("helper.py")
    helper.write_text('LABEL = "original source"\n')
    start_project(project, timeout=60)
    try:
        with isolated_project_imports(), project.authoring() as author:
            author.refresh()
            module = importlib.import_module("scopecat_lab.workflow")
            old_definition = module.ordinary
            old_intent = module.Intent(label="Review ordinary procedure")
            # Any client-side application loading or worker construction is a bug.
            with (
                patch.object(Project, "load_application", side_effect=AssertionError),
                patch(
                    "scopecat.api.procedures.ProcedureWorker",
                    side_effect=AssertionError,
                ),
            ):
                prepared = author.procedures.prepare(
                    old_definition, old_intent, request_key="ordinary-retry"
                )
                # The command owns detached, recursively frozen JSON containers.
                old_intent.tags.append("caller mutation")
                assert prepared.command.intent["tags"] == ("source-bound",)
                # Even a low-level client with the same workspace ID must not
                # reconnect this command to another application's scientific store.
                other = initialize_project(tmp_path / "other-store")
                start_project(other, timeout=60)
                try:
                    with DaemonClient(
                        resolve_daemon_endpoint(other.root),
                        workspace_id=author.workspace_id,
                    ) as wrong_store:
                        with pytest.raises(ValueError, match="another data store"):
                            prepared.reconnect(wrong_store)
                        assert not wrong_store.list_procedures(
                            ProcedureRunListQuery(request_key="ordinary-retry")
                        ).items
                finally:
                    stop_project(other)
                original_source = prepared.command.source
                helper.write_text('LABEL = "changed source"\n')
                source.write_text(
                    _SOURCE.replace(
                        "title=intent.label,", 'title=intent.label + " new",'
                    )
                )
                with pytest.raises(ValueError, match="imports do not match"):
                    author.procedures.prepare(
                        old_definition, old_intent, request_key="stale-source"
                    )
                handle = prepared.submit()
                assert handle.dispatch_error is None
                assert handle.progress().dispatch.worker_running
                author.close()
            # The submitting client is gone before waiting for the worker.
            with project.authoring() as reconnected:
                observed = reconnected.procedures.get(handle.id)
                _wait(observed, "waiting_for_input")
                assert observed.snapshot.source == original_source
                view = observed.progress()
                step = view.steps.items[0]
                assert step.interpretation_request is not None
                assert step.interpretation_request.instructions == "original source"
                assert view.dispatch.log_path is not None
                receipt = Path(view.dispatch.log_path).with_name("process.json")
                assert json.loads(receipt.read_text())["pid"] != os.getpid()
                assert prepared.reconnect(reconnected).submit().id == handle.id
                reconnected.submit_procedure_step_input(
                    ProcedureStepInputSubmitCommand(
                        procedure_run_id=handle.id,
                        expected_run_revision=observed.snapshot.revision,
                        step_key=step.step_key,
                        attempt=step.attempt,
                        expected_step_revision=step.revision,
                        request_hash=step.interpretation_request.request_hash,
                        actor="test-author",
                        actor_kind="human",
                        value={"accepted": True},
                    )
                )
                assert observed.resume().dispatch_error is None
                _wait(observed, "closed")
                closure = observed.snapshot.closure
                assert closure is not None and closure.status == "succeeded"
                assert prepared.reconnect(reconnected).submit().id == handle.id
                assert (
                    len(
                        reconnected.list_procedures(
                            ProcedureRunListQuery(request_key="ordinary-retry")
                        ).items
                    )
                    == 1
                )
                # Refresh is explicit. Old model/definition aliases are not coerced.
                reconnected.refresh()
                with pytest.raises(ValueError, match="Stale procedure"):
                    reconnected.procedures.prepare(
                        old_definition, old_intent, request_key="stale-alias"
                    )
                module = importlib.import_module("scopecat_lab.workflow")
                with pytest.raises(ValueError, match="Intent"):
                    reconnected.procedures.prepare(
                        module.ordinary, old_intent, request_key="stale-intent"
                    )
                current = reconnected.procedures.prepare(
                    module.ordinary, module.Intent(label="Cancel"), request_key="cancel"
                )
                # Old definition + new source is rejected before admission too.
                stale_source = prepared.command.model_copy(
                    update={
                        "request_key": "changed-source",
                        "source": current.command.source,
                    }
                )
                with pytest.raises(httpx2.HTTPStatusError):
                    reconnected.submit_author_procedure(stale_source)
                assert not reconnected.list_procedures(
                    ProcedureRunListQuery(request_key="changed-source")
                ).items
                # JSON true must not pass canonical validation as integer 1.
                noncanonical = current.command.model_copy(
                    update={
                        "request_key": "noncanonical",
                        "intent": {**current.command.intent, "repetitions": True},
                    }
                )
                with pytest.raises(httpx2.HTTPStatusError) as rejected:
                    reconnected.submit_author_procedure(noncanonical)
                assert "not canonical" in rejected.value.response.text
                assert not reconnected.list_procedures(
                    ProcedureRunListQuery(request_key="noncanonical")
                ).items
                # The daemon checks registration/fingerprint before admitting.
                forged = current.command.model_copy(
                    update={
                        "request_key": "forged",
                        "definition": current.command.definition.model_copy(
                            update={
                                "fingerprint": "sha256:" + "0" * 64,
                            }
                        ),
                    }
                )
                with pytest.raises(httpx2.HTTPStatusError):
                    reconnected.submit_author_procedure(forged)
                assert not reconnected.list_procedures(
                    ProcedureRunListQuery(request_key="forged")
                ).items
                cancelled = current.submit()
                _wait(cancelled, "waiting_for_input")
                request = cancelled.progress().steps.items[0].interpretation_request
                assert request is not None and request.instructions == "changed source"
                cancelled.cancel(actor="test-author", reason="Done testing")
                closure = cancelled.snapshot.closure
                assert closure is not None and closure.status == "cancelled"
    finally:
        release_notebook_imports(project.root)
        stop_project(project)
