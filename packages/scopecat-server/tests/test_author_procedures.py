"""Ordinary author procedures cross a real daemon and immutable worker boundary."""

from __future__ import annotations

import importlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest
from filelock import FileLock
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.application.author_imports import release_notebook_imports
from scopecat.application.author_procedures import AuthorProcedure
from scopecat.automation.wire import (
    ProcedureRunListQuery,
    ProcedureStepInputSubmitCommand,
)
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


@pytest.mark.parametrize("request_key", [None, "ordinary-retry"])
def test_managed_source_retry_disconnect_and_continue(
    tmp_path: Path, request_key: str | None
) -> None:
    project = initialize_project(tmp_path / "ordinary")
    manifest = project.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[lab.capabilities]\nprocedures = ["scopecat_lab.workflow:ordinary"]\n'
    )
    source = project.root / "src/scopecat_lab/workflow.py"
    gate = tmp_path / "disconnect.lock"
    # The calibration-task test also consumes _SOURCE. Keep this handshake local
    # to the case that owns the gate instead of changing that shared fixture.
    procedure_source = _SOURCE.replace(
        "    time.sleep(2)\n",
        "    from pathlib import Path\n"
        "    from filelock import FileLock\n"
        f"    gate = Path({str(gate)!r})\n"
        '    gate.with_suffix(".entered").touch()\n'
        "    with FileLock(gate, timeout=60):\n"
        "        pass\n",
    )
    source.write_text(procedure_source)
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
                    old_definition, old_intent, request_key=request_key
                )
                another = author.procedures.prepare(old_definition, old_intent)
                assert another.command.request_key != prepared.command.request_key
                assert another.command.source == prepared.command.source
                if request_key is not None:
                    assert prepared.command.request_key == request_key
                else:
                    from uuid import UUID

                    assert UUID(prepared.command.request_key).version == 4
                # The command owns detached, recursively frozen JSON containers.
                old_intent.tags.append("caller mutation")
                assert prepared.command.intent["tags"] == ("source-bound",)
                # Reconnection identity fences are covered directly in
                # scopecat/tests/test_author_procedures.py.
                original_source = prepared.command.source
                helper.write_text(
                    helper.read_text().replace("original source", "changed source")
                )
                source.write_text(
                    procedure_source.replace(
                        "title=intent.label,", 'title=intent.label + " new",'
                    )
                )
                with pytest.raises(ValueError, match="imports do not match"):
                    author.procedures.prepare(
                        old_definition, old_intent, request_key="stale-source"
                    )
                # Hold the real worker until this client has disconnected. The
                # unlocked gate is immediate on resume and on the cancellation run.
                with FileLock(gate, timeout=60):
                    handle = prepared.submit()
                    assert handle.dispatch_error is None
                    deadline = time.monotonic() + 60
                    while not gate.with_suffix(".entered").exists():
                        assert time.monotonic() < deadline, handle.progress()
                        time.sleep(0.01)
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

                @dataclass(frozen=True)
                class Answer:
                    accepted: bool

                pending = observed.pending_input(
                    "confirm", schema=AnalysisFactSchema("test.answer.v1", Answer)
                )
                assert pending.request.instructions == "original source"
                answered = pending.respond(Answer(True), actor="test-author")
                assert answered.run.state == "ready"
                assert pending.respond(Answer(True), actor="test-author") == answered
                assert observed.progress().dispatch.management == "paused"
                assert observed.resume().dispatch_error is None
                _wait(observed, "closed")
                closure = observed.snapshot.closure
                assert closure is not None and closure.status == "succeeded"
                assert prepared.reconnect(reconnected).submit().id == handle.id
                assert (
                    len(
                        reconnected.procedures.list(
                            request_key=prepared.command.request_key, state="closed"
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
                first = reconnected.procedures.list(state="closed", limit=1)
                assert [item.procedure_run_id for item in first.items] == [cancelled.id]
                assert first.next_cursor is not None
                second = reconnected.procedures.list(
                    state="closed", limit=1, cursor=first.next_cursor
                )
                assert [item.procedure_run_id for item in second.items] == [handle.id]
                assert not reconnected.procedures.list(
                    request_key=prepared.command.request_key[:-1]
                ).items
                assert not reconnected.procedures.list(
                    request_key=prepared.command.request_key, state="waiting_for_input"
                ).items

    finally:
        release_notebook_imports(project.root)
        stop_project(project)


def test_wait_hands_off_input_only_after_worker_exit(tmp_path: Path) -> None:
    """Hold a real worker after its durable input wait, before process exit."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    project = initialize_project(tmp_path / "handoff")
    manifest = project.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[lab.capabilities]\nprocedures = ["scopecat_lab.workflow:ordinary"]\n'
    )
    gate = tmp_path / "exit.lock"
    source = project.root / "src/scopecat_lab/workflow.py"
    source.write_text(
        _SOURCE.replace(
            "    time.sleep(2)\n",
            "    import atexit\n"
            "    from filelock import FileLock\n"
            "    def exiting():\n"
            f"        with FileLock({str(gate)!r}, timeout=60):\n"
            "            pass\n"
            "    atexit.register(exiting)\n",
        )
    )
    source.with_name("helper.py").write_text('LABEL = "exit barrier"\n')
    start_project(project, timeout=60)
    try:
        with isolated_project_imports(), project.authoring() as author:
            author.refresh()
            module = importlib.import_module("scopecat_lab.workflow")
            with ThreadPoolExecutor(max_workers=1) as executor:
                with FileLock(gate, timeout=60):
                    handle = author.procedures.prepare(
                        module.ordinary,
                        module.Intent(label="Review"),
                        request_key="exit",
                    ).submit()
                    observed = Event()
                    read_progress = author.procedure_progress

                    def progress(
                        procedure_id: str,
                        *,
                        cursor: int | None = None,
                        timeout: float | None = None,
                    ):
                        result = read_progress(
                            procedure_id, cursor=cursor, timeout=timeout
                        )
                        if (
                            result.procedure.state == "waiting_for_input"
                            and result.dispatch.worker_running
                        ):
                            observed.set()
                        return result

                    with patch.object(
                        author, "procedure_progress", side_effect=progress
                    ):
                        waiting = executor.submit(handle.wait)
                        assert observed.wait(30), (
                            "wait did not observe the exit barrier"
                        )
                        assert not waiting.done()
                assert waiting.result(timeout=60).state == "waiting_for_input"
            view = handle.progress()
            assert not view.dispatch.worker_running
            step = view.steps.items[0]
            assert step.interpretation_request is not None
            author.submit_procedure_step_input(
                ProcedureStepInputSubmitCommand(
                    procedure_run_id=handle.id,
                    expected_run_revision=view.procedure.revision,
                    step_key=step.step_key,
                    attempt=step.attempt,
                    expected_step_revision=step.revision,
                    request_hash=step.interpretation_request.request_hash,
                    actor="test-author",
                    actor_kind="human",
                    value={"accepted": True},
                )
            )
            assert handle.resume().dispatch_error is None
            _wait(handle, "closed")
            closure = handle.snapshot.closure
            assert closure is not None and closure.status == "succeeded"
            steps = handle.steps().items
            assert len(steps) == 1
            assert steps[0].attempt == 1 and steps[0].state == "succeeded"
    finally:
        release_notebook_imports(project.root)
        stop_project(project)


def test_managed_checkpoint_can_exit_and_reopen_without_answering(
    tmp_path: Path,
) -> None:
    project = initialize_project(tmp_path / "exit-checkpoint")
    manifest = project.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[lab.capabilities]\nprocedures = ["scopecat_lab.workflow:ordinary"]\n'
    )
    source = project.root / "src/scopecat_lab/workflow.py"
    source.write_text(_SOURCE)
    source.with_name("helper.py").write_text('LABEL = "retained exit question"\n')
    record = start_project(project, timeout=60)
    try:
        with isolated_project_imports(), project.authoring() as author:
            author.refresh()
            module = importlib.import_module("scopecat_lab.workflow")
            handle = author.procedures.prepare(
                module.ordinary,
                module.Intent(label="Preserve this question"),
                request_key="exit-checkpoint",
            ).submit()
            with httpx2.Client(
                base_url=record.base_url, trust_env=False, timeout=10
            ) as client:
                assert (
                    client.post("/api/v1/application-exit?wait=true").status_code == 204
                )
                deadline = time.monotonic() + 45
                while True:
                    report = client.get("/api/v1/application-exit").json()
                    if not report["finishing"] and not report["blockers"]:
                        break
                    assert time.monotonic() < deadline, report
                    time.sleep(0.1)
                assert any(item["identity"] == handle.id for item in report["deferred"])
                before = client.get(f"/api/v1/procedures/{handle.id}").json()
                view = handle.progress()
                assert view.dispatch.log_path is not None
                directory = Path(view.dispatch.log_path).parent
                assert (directory / "settled.json").read_bytes() == (
                    directory / "process.json"
                ).read_bytes()
                assert before["state"] == "waiting_for_input"
                assert view.steps.items[0].interpretation_request is not None
                assert (
                    view.steps.items[0].interpretation_request.instructions
                    == "retained exit question"
                )
        stop_project(project, only_if_idle=True)
        reopened = start_project(project, timeout=60)
        with httpx2.Client(
            base_url=reopened.base_url, trust_env=False, timeout=10
        ) as client:
            assert client.get(f"/api/v1/procedures/{handle.id}").json() == before
            assert client.post("/api/v1/application-exit?wait=true").status_code == 204
            report = client.get("/api/v1/application-exit").json()
            assert report["finishing"] == report["blockers"] == []
            assert client.post("/api/v1/application-exit?wait=false").status_code == 204
    finally:
        stop_project(project)
        release_notebook_imports(project.root)
