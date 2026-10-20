"""Real shipped task cells through one author session and application workers."""

import json
from pathlib import Path

import httpx2
import pytest
from nbclient import NotebookClient
from nbformat import read, v4, write
from testing.app_support.project import create_project
from testing.app_support.verify_editing import _cell_index

from scopecat.author_workspaces import author_workspace_id
from scopecat_app.application_runtime import ApplicationRuntime
from scopecat_app.notebook import kernel_command
from scopecat_server.lifecycle import start_project, stop_project


@pytest.mark.parametrize("enrolled", [False, True])
def test_managed_task_calibration_shipped_kernel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    author_kernel_python: Path,
    enrolled: bool,
):
    root = create_project(tmp_path / "作者代码", topic="task-calibration").parent
    material = (
        Path(__file__).resolve().parents[2]
        / "scopecat-tutorials/src/scopecat_tutorials/course_material/lessons"
    )
    path = root / "notebooks/task-calibration.ipynb"
    assert path.read_bytes() == (material / path.name).read_bytes()
    for name in ("calibration", "joint_calibration", "task_calibration"):
        assert (root / f"src/my_experiment/{name}.py").read_bytes() == (
            material / f"{name}.py.txt"
        ).read_bytes()
    runtime = ApplicationRuntime(tmp_path / "应用数据")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    runtime.configure(static_dir=gui)
    runtime.register_source(root, python=author_kernel_python)
    application = runtime.instance
    _, environment = kernel_command(
        root, python=str(author_kernel_python), source_path=False
    )
    monkeypatch.setenv("JUPYTER_PATH", environment["JUPYTER_PATH"])
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    raw = json.loads(path.read_text(encoding="utf-8"))["cells"]
    for anchor in ("task-3", "task-4", "task-9"):
        _cell_index(raw, anchor)
    notebook = read(path, as_version=4)
    notebook.cells.insert(
        _cell_index(notebook.cells, "task-3"), v4.new_code_cell(_EDIT_AFTER_PREPARE)
    )
    notebook.cells.insert(
        _cell_index(notebook.cells, "task-4"), v4.new_code_cell(_OBSERVE_DISCONNECT)
    )
    notebook.cells.insert(
        _cell_index(notebook.cells, "task-9"), v4.new_code_cell(_CHECKS_AND_SAVE)
    )
    reopen = v4.new_notebook(
        cells=[
            v4.new_code_cell("import scopecat as sc\nsession = sc.notebook()"),
            notebook.cells[_cell_index(notebook.cells, "task-history")],
            v4.new_code_cell(_REOPEN),
        ]
    )
    try:
        endpoint = start_project(application, timeout=120)
        scope_id = None
        if enrolled:
            response = httpx2.post(
                endpoint.base_url + "/api/v1/notebook-practice",
                json={
                    "workspace_id": author_workspace_id(root),
                    "lesson": "task-calibration",
                },
                timeout=30,
                trust_env=False,
            )
            assert response.status_code == 200, response.text
            scope_id = response.json()["id"]
        for name, document in (("managed-task", notebook), ("reopened-task", reopen)):
            if name == "reopened-task":
                stop_project(application)
                endpoint = start_project(application, timeout=120)
            try:
                NotebookClient(
                    document,
                    timeout=180,
                    kernel_name="scopecat-lab",
                    resources={"metadata": {"path": str(root / "notebooks")}},
                ).execute()
            finally:
                write(document, root / f"notebooks/verified-{name}.ipynb")
        evidence = json.loads(
            (root / "notebooks/task-evidence.json").read_text(encoding="utf-8")
        )
        assert evidence["disconnect_state"] == "leased"
        assert evidence["reconnect_mode"] == "running"
        assert len(evidence["run_ids"]) == 12
        if scope_id is not None:
            response = httpx2.get(
                endpoint.base_url + f"/api/v1/notebook-practice/{scope_id}/preview",
                timeout=30,
                trust_env=False,
            )
            assert response.status_code == 200, response.text
            preview = response.json()
            assert set(preview["selection"]["runs"]) == set(evidence["run_ids"])
            assert preview["selection"]["branches"]
            assert preview["selection"]["calibration_tasks"]
            assert not preview["blockers"], preview
            response = httpx2.post(
                endpoint.base_url + f"/api/v1/notebook-practice/{scope_id}/clear",
                json={"preview": preview, "files": "preserve"},
                timeout=30,
                trust_env=False,
            )
            assert response.status_code == 200, response.text
            assert response.json()["state"] == "cleared"
    finally:
        stop_project(application)


_EDIT_AFTER_PREPARE = """
from pathlib import Path

source = session.project_root / "src/my_experiment/task_calibration.py"
original = source.read_text(encoding="utf-8")
assert "estimate = value - error" in original
source.write_text(
    original.replace("estimate = value - error", "estimate = 1.5"),
    encoding="utf-8",
)
frozen_commands = {case: item.command for case, item in prepared_tasks.items()}
for prepared in prepared_tasks.values():
    detached = prepared.command
    detached.calls.clear()
    assert prepared.command.calls
"""

_OBSERVE_DISCONNECT = """
import time
from scopecat.daemon.client import DaemonClient

original_close = session.close
original_notebook = sc.notebook


def observe_close():
    deadline = time.monotonic() + 60
    while True:
        view = session.calibration_tasks.get(task_ids[0])
        assert view.task.mode == "running"
        ids = list(view.task.executions.values())
        if ids:
            progress = session.procedures.get(ids[0]).progress()
            if (
                progress.procedure.state == "leased"
                and progress.dispatch.worker_running
            ):
                break
        assert time.monotonic() < deadline, view
        time.sleep(0.02)
    global disconnect_state, disconnected_procedure
    disconnect_state = progress.procedure.state
    disconnected_procedure = progress.procedure.procedure_run_id
    original_close()
    assert session.is_closed
    with DaemonClient(session.base_url, workspace_id=session.workspace_id) as observer:
        assert observer.get_procedure(disconnected_procedure).closure is None
    sc.notebook = observe_reconnect


def observe_reconnect():
    assert session.is_closed
    reopened = original_notebook()
    sc.notebook = original_notebook
    global reconnect_mode
    view = reopened.calibration_tasks.get(task_ids[0])
    reconnect_mode = view.task.mode
    assert reconnect_mode == "running", "Need an in-flight reconnect witness"
    assert view.finalization is None or view.finalization.closure is None
    return reopened


session.close = observe_close
"""

_CHECKS_AND_SAVE = """
import json
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ParameterBranchPublishOutputRef,
)

try:
    assert all(
        v.task.mode == "finished" and v.progress.successful for v in views.values()
    )
except AssertionError:
    try:
        states = {case: view.model_dump(mode="json") for case, view in views.items()}
        print("Task completion diagnostics: " + json.dumps(states)[:16000], flush=True)
    except Exception:
        pass  # Diagnostics must preserve the original assertion failure.
    raise
assert session.config.registry().entries == ()
assert (
    session.current_author_source() != frozen_commands["accepted"].source.code_revision
)
assert {t.specification.task_id for t in session.calibration_tasks.list().items} == set(
    task_ids
)
accepted = session.parameters.checkout(trial + "-accepted").head
assert accepted.generation == destinations["accepted"].generation + 1
assert session.parameters.checkout(trial + "-rejected").head == destinations["rejected"]
assert session.parameters.checkout(trial + "-conflict").head == concurrent
saved = {}
for case, view in views.items():
    command = frozen_commands[case]
    assert view.task.specification == command
    replay = prepared_tasks[case].reconnect(session).submit()
    assert replay.task == view.task
    assert len(view.task.attempts) == 2
    for stage, attempts in view.task.attempts.items():
        assert len(attempts) == 1
        stage_run = session.procedures.get(attempts[0].procedure_run_id)
        assert stage_run.snapshot.source == command.source
        assert stage_run.snapshot.intent == command.calls[stage].intent
        assert stage_run.snapshot.closure.status == "succeeded"
    final = session.procedures.get(view.finalization.procedure_run_id)
    assert final.snapshot.source == command.source
    assert final.snapshot.intent["calibration_task"]["task_id"] == command.task_id
    for key, value in command.finalization.intent.items():
        if key != "calibration_task":
            assert final.snapshot.intent[key] == value
    verification = final.output("verify-joint")
    assert isinstance(verification, AnalysisPublicationOutputRef)
    decision = (
        session.published_analysis(verification.analysis_record_id)
        .fact("decision")
        .value
    )
    assert tuple(decision["checked"]) == ("q0", "q1") and not decision["missing"]
    assert decision["accepted"] == (case != "rejected")
    if case == "accepted":
        assert final.snapshot.closure.status == "succeeded"
        receipt = final.output("publish")
        assert isinstance(receipt, ParameterBranchPublishOutputRef)
        assert receipt.branch == accepted
    else:
        assert final.snapshot.closure.status == "failed"
        if case == "conflict":
            attempt = next(s for s in final.steps().items if s.step_key == "publish")
            assert attempt.state == "failed"
            assert "branch changed" in final.snapshot.closure.reason
        else:
            assert not any(
                s.operation == "parameter_publish" for s in final.steps().items
            )
    adopted = view.progress.stages[0].evidence
    run = session.run(adopted.measurement.run_id)
    saved[case] = {
        "task": command.model_dump(mode="json"),
        "final": final.id,
        "head": session.parameters.checkout(command.task_id).head.model_dump(
            mode="json"
        ),
        "measurement": adopted.measurement.run_id,
        "raw": {
            k: list(run.measurements()[k].require_values())
            for k in ("setting", "residual")
        },
    }
runs = session.list_runs(limit=100).items
assert len(runs) == 12
Path("task-evidence.json").write_text(
    json.dumps(
        {
            "tasks": saved,
            "run_ids": [r.run_id for r in runs],
            "disconnect_state": disconnect_state,
            "reconnect_mode": reconnect_mode,
        }
    )
)
"""

_REOPEN = """
import json
from pathlib import Path
import scopecat as sc

evidence = json.loads(Path("task-evidence.json").read_text(encoding="utf-8"))
assert {item.specification.task_id for item in lesson_history} == {
    saved["task"]["task_id"] for saved in evidence["tasks"].values()
}
assert all(IDENTITY in item.specification.task_id for item in lesson_history)
assert session.setup.get(f"{IDENTITY}-setup")
for case, saved in evidence["tasks"].items():
    task_id = saved["task"]["task_id"]
    view = session.calibration_tasks.get(task_id)
    assert view.task.mode == "finished"
    assert view.task.specification.model_dump(mode="json") == saved["task"]
    assert session.calibration_tasks.wait(task_id).task == view.task
    assert (
        session.parameters.checkout(task_id).head.model_dump(mode="json")
        == saved["head"]
    )
    final = session.procedures.get(saved["final"])
    assert final.snapshot.source.model_dump(mode="json") == saved["task"]["source"]
    decision = (
        session.published_analysis(final.output("verify-joint").analysis_record_id)
        .fact("decision")
        .value
    )
    assert decision["accepted"] == (case != "rejected")
    run = session.run(saved["measurement"])
    for key, values in saved["raw"].items():
        assert list(run.measurements()[key].require_values()) == values
assert {r.run_id for r in session.list_runs(limit=100).items} == set(
    evidence["run_ids"]
)
session.close()
"""
