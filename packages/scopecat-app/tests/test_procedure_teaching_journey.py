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
from pathlib import Path
import scopecat as sc
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
    with project.authoring() as session:
        session.refresh()
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
            if outcome == "Peak selected":
                # Python bypasses UI hints; the procedure still checks meaning.
                pending.respond(ns["PeakChoice"](outcome, 6600), actor="learner")
                assert task.snapshot.state == "ready"
                task.resume()
                assert task.wait(timeout=60).state == "waiting_for_input"
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
                run("procedure-answer")
            else:
                ns["receipt"] = pending.respond(
                    ns["PeakChoice"](outcome, None), actor="learner"
                )
            assert task.snapshot.state == "ready"
            run("procedure-continue")
            assert ns["result"].closure.status == "succeeded"
            assert task.output("scan").run_id == scan
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
start_project(project, timeout=60)
try:
    NotebookClient(
        document,
        timeout=120,
        kernel_name="scopecat-lab",
        resources={"metadata": {"path": str(root / "notebooks")}},
    ).execute()
finally:
    nbformat.write(document, root / "notebooks/verified-procedure.ipynb")
    stop_project(project)
"""
