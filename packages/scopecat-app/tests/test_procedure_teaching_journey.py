"""Execute the generated author lesson, including the real worker/input handoff."""

import os
import subprocess
import sys
from pathlib import Path

from scopecat_app.notebook_journey import create_lesson_source


def test_generated_procedure_lesson_answers_and_recovers(tmp_path: Path) -> None:
    root = tmp_path / "procedure-author"
    create_lesson_source(root, "procedure")
    env = dict(os.environ)
    env.pop("SCOPECAT_DAEMON_URL", None)
    result = subprocess.run(  # noqa: S603 - fixed script and isolated generated source
        [sys.executable, "-c", _JOURNEY, str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_JOURNEY = r"""
import json
import sys
from contextlib import ExitStack
from pathlib import Path
import scopecat as sc
from scopecat.daemon.client import DaemonConflictError
from scopecat_server.lifecycle import start_project, stop_project

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
project = sc.open_project(root)
cells = {
    cell["id"]: "".join(cell["source"])
    for cell in json.loads((root / "notebooks/procedure.ipynb").read_text())["cells"]
    if cell["cell_type"] == "code"
}
start_project(project, timeout=60)
try:
    with ExitStack() as sessions:
        session = sessions.enter_context(project.authoring())
        session.refresh()
        from my_experiment.procedure import PeakChoice, peak_choice_problem

        # Editable input rules accept both endpoints, reject missing/out-of-range
        # selections, and require empty frequencies for either non-selection.
        for outcome, frequency, valid in [
            ("Peak selected", 6490.0, True),
            ("Peak selected", 6510.0, True),
            ("Peak selected", 6489.0, False),
            ("Peak selected", 6511.0, False),
            ("Peak selected", None, False),
            ("Unsure", None, True),
            ("Unsure", 6500.0, False),
            ("No clear peak", None, True),
            ("No clear peak", 6500.0, False),
        ]:
            assert (not peak_choice_problem(PeakChoice(outcome, frequency))) == valid
        ns = {"sc": sc, "session": session}

        def run(name):
            exec(cells[name], ns)

        run("procedure-history")
        for outcome in ["Peak selected", "Unsure", "No clear peak"]:
            run("procedure-prepare")
            run("procedure-submit")
            task = ns["task"]
            assert task.snapshot.state == "waiting_for_input"
            run("procedure-inspect")
            pending = ns["pending"]
            scan = next(item.run_id for item in pending.inputs if item.kind == "run")
            assert (
                pending.request.structure["fields"]["frequency_mhz"]["type"] == "union"
            )
            limits = pending.request.metadata["field_constraints"]["frequency_mhz"]
            assert limits["minimum"] == 6490 and limits["maximum"] == 6510
            if outcome == "Peak selected":
                # Python bypasses UI hints; the procedure still checks meaning.
                answer = ns["PeakChoice"](outcome, 6600)
                receipt = pending.respond_and_continue(answer, actor="learner")
                repeated = pending.respond_and_continue(answer, actor="learner")
                assert repeated.answer.output == receipt.answer.output
                assert task.wait(timeout=60).state == "waiting_for_input"
                try:
                    pending.respond_and_continue(
                        ns["PeakChoice"](outcome, 6500), actor="learner"
                    )
                except DaemonConflictError:
                    pass
                else:
                    raise AssertionError("old pending must not answer the new question")
                run("procedure-inspect")
                assert "between 6490 and 6510" in ns["pending"].request.instructions
                assert (
                    next(
                        item.run_id
                        for item in ns["pending"].inputs
                        if item.kind == "run"
                    )
                    == scan
                )
                # Restart while the correction question is pending. Reconnect
                # by the retained ID, without preparing/submitting another scan.
                identity = task.id
                question = ns["pending"].request
                source = task.snapshot.source
                runs = session.list_runs()
                assert runs.next_cursor is None and len(runs.items) == 1
                session.close()
                stop_project(project)
                start_project(project, timeout=60)
                session = sessions.enter_context(project.authoring())
                ns = {"sc": sc, "session": session}
                run("procedure-history")
                task = session.procedures.get(identity)
                ns["task"] = task
                run("procedure-inspect")
                assert ns["pending"].request == question
                assert task.snapshot.source == source
                assert task.output("scan").run_id == scan
                assert session.list_runs() == runs
                assert [
                    step.step_key for step in task.steps().items
                    if step.state == "waiting_for_input"
                ] == ["choose-2"]
                run("procedure-answer")
            else:
                ns["receipt"] = pending.respond_and_continue(
                    ns["PeakChoice"](outcome, None), actor="learner"
                )
            run("procedure-continue")
            assert ns["result"].closure.status == "succeeded"
            assert task.output("scan").run_id == scan
            scan_steps = [
                step for step in task.steps().items if step.step_key == "scan"
            ]
            assert len(scan_steps) == 1 and scan_steps[0].attempt == 1
            if outcome == "Peak selected":
                assert session.list_runs() == runs
        # History works after closing the author session, without new acquisitions.
        identity = task.id
    with project.authoring() as reopened:
        ns["session"] = reopened
        run("procedure-history")
        assert reopened.procedures.get(identity).snapshot.closure.status == "succeeded"
finally:
    stop_project(project)
"""


def test_shipped_procedure_notebook_in_independent_kernel(tmp_path: Path) -> None:
    root = tmp_path / "procedure-kernel"
    create_lesson_source(root, "procedure")
    env = dict(os.environ)
    env.pop("SCOPECAT_DAEMON_URL", None)
    result = subprocess.run(  # noqa: S603 - fixed script and isolated generated source
        [sys.executable, "-c", _KERNEL_JOURNEY, str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_KERNEL_JOURNEY = r"""
import os
import sys
from pathlib import Path
import nbformat
from nbclient import NotebookClient
import scopecat as sc
from scopecat_app.notebook import kernel_command
from scopecat_server.lifecycle import start_project, stop_project

root = Path(sys.argv[1])
# No source PYTHONPATH or injected author session: sc.notebook() owns imports.
_, environment = kernel_command(root, source_path=False)
os.environ["JUPYTER_PATH"] = environment["JUPYTER_PATH"]
project = sc.open_project(root)
document = nbformat.read(root / "notebooks/procedure.ipynb", as_version=4)
document.cells.append(nbformat.v4.new_code_cell('''
assert result.closure.status == "succeeded"
assert task.output("scan").run_id == next(
    item.run_id for item in pending.inputs if item.kind == "run"
)
assert task.snapshot.closure.status == "succeeded"
session.close()
'''))
from threading import Event, Thread
from unittest.mock import Mock
from scopecat_app.desktop_objects import DesktopObjectOpener
from scopecat_app.desktop_session import DesktopSession
endpoint = start_project(project, timeout=60)
closing = Event()
desktop = DesktopSession(Mock(), closing)
desktop.connected(endpoint.base_url)
opened = []
opener = DesktopObjectOpener(desktop, opened.append)
opener.pump()
def pump():
    while not closing.wait(0.05):
        opener.pump()
thread = Thread(target=pump)
thread.start()
try:
    NotebookClient(
        document,
        timeout=120,
        kernel_name="scopecat-lab",
        resources={"metadata": {"path": str(root / "notebooks")}},
    ).execute()
    assert len(opened) == 1
    assert opened[0].kind == "procedure" and opened[0].view == "decision"
finally:
    closing.set()
    thread.join(timeout=5)
    opener.close()
    nbformat.write(document, root / "notebooks/verified-procedure.ipynb")
    stop_project(project)
"""


def test_registered_course_procedure_owns_children_and_cleans_exact_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned-procedure"
    create_lesson_source(root, "procedure")
    env = dict(os.environ)
    env.pop("SCOPECAT_DAEMON_URL", None)
    script = (
        _JOURNEY.replace(
            "start_project(project, timeout=60)\ntry:",
            """endpoint = start_project(project, timeout=60)
import httpx2
from scopecat.author_workspaces import author_workspace_id
with httpx2.Client(base_url=endpoint.base_url, trust_env=False, timeout=30) as web:
    response = web.post("/api/v1/notebook-practice", json={
        "workspace_id": author_workspace_id(project.root), "lesson": "procedure"
    })
    assert response.status_code == 200, response.text
    scope_id = response.json()["id"]
try:""",
            1,
        )
        .replace(
            "finally:\n    stop_project(project)",
            """    with httpx2.Client(
        base_url=endpoint.base_url, trust_env=False, timeout=30
    ) as web:
        preview = web.get(f"/api/v1/notebook-practice/{scope_id}/preview")
        assert preview.status_code == 200, preview.text
        content = preview.json()
        assert len(content["selection"]["runs"]) == 3, content
        assert len(content["selection"]["procedures"]) == 3, content
        assert content["selection"]["parameters"], content
        assert not content["blockers"], content
        cleared = web.post(f"/api/v1/notebook-practice/{scope_id}/clear", json={
            "preview": content, "files": "preserve"
        })
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["state"] == "cleared"
        assert (root / "src/my_experiment/procedure.py").is_file()
finally:
    stop_project(project)""",
            1,
        )
        .replace(
            "start_project(project, timeout=60)\n                session =",
            "endpoint = start_project(project, timeout=60)\n                session =",
        )
    )
    result = subprocess.run(  # noqa: S603 - generated lesson in an isolated test source
        [sys.executable, "-c", script, str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
