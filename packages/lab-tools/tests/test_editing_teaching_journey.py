"""Shipped editing lessons retain their results in one ordinary application."""

import os
from pathlib import Path

from nbclient import NotebookClient
from nbformat import v4, write

from lab_teaching.project import create_project
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.notebook import kernel_command
from lab_tools.verify_editing import (
    REFRESH_REOPEN_CELLS,
    _cell_index,
    editing_notebook,
    reopen_cells,
)
from scopecat.project import open_project
from scopecat_server.lifecycle import start_project, stop_project


def test_editing_lessons_share_application_and_reopen_after_restart(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = ApplicationRuntime(tmp_path / "application")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    runtime.configure(static_dir=gui)
    roots = {
        topic: create_project(tmp_path / topic, topic=topic).parent
        for topic in ("refresh", "compute")
    }
    for root in roots.values():
        runtime.register_source(root)
    application = open_project(runtime.root)
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path / "ipython"))

    def execute(root, name, document):
        _, environment = kernel_command(root, source_path=False)
        monkeypatch.setenv("JUPYTER_PATH", environment["JUPYTER_PATH"])
        try:
            NotebookClient(
                document,
                timeout=120,
                kernel_name="scopecat-lab",
                resources={"metadata": {"path": str(root / "notebooks")}},
            ).execute(env=dict(os.environ))
        finally:
            write(document, root / f"notebooks/verified-{name}.ipynb")

    try:
        record = start_project(application, timeout=120)
        for topic, root in roots.items():
            document = editing_notebook(root, topic)
            evidence = f"isolation_path = {str(tmp_path / 'isolation.json')!r}\n"
            if topic == "refresh":
                document.cells.insert(
                    _cell_index(document.cells, "refresh-2"),
                    v4.new_code_cell(evidence + _ORDINARY_INPUT),
                )
            document.cells.insert(
                _cell_index(document.cells, f"{topic}-2") + 1,
                v4.new_code_cell(evidence + _LESSON_IDENTITY),
            )
            document.cells.append(v4.new_code_cell(evidence + _SAVE_ISOLATION))
            document.cells.insert(
                2,
                v4.new_code_cell(f"assert session.base_url == {record.base_url!r}"),
            )
            execute(root, topic, document)
        stop_project(application)
        start_project(application, timeout=120)
        for topic, root in roots.items():
            cells = REFRESH_REOPEN_CELLS if topic == "refresh" else reopen_cells(root)
            document = v4.new_notebook(
                cells=[v4.new_code_cell(source) for source in cells]
                + [v4.new_code_cell(evidence + _CHECK_ISOLATION)]
            )
            execute(root, f"{topic}-reopened", document)
    finally:
        stop_project(application)


_ORDINARY_INPUT = """
import json
from pathlib import Path
from my_experiment.parameters import Drive
from workspace_app import initial_setup

ordinary_setup = session.setup.import_recipe(initial_setup(), name="ordinary-control")
ordinary_revision = session.parameters.save(
    name="ordinary-control-input",
    catalog=sc.parameter_catalog("ordinary-control", Drive),
    parameters=sc.parameter_snapshot(
        "ordinary-control", tables={Drive: [Drive(id="q0", frequency=4.7)]},
    ),
)
ordinary_branch = session.parameters.create_branch(
    "ordinary-control", revision=ordinary_revision,
)
Path(isolation_path).write_text(json.dumps({
    "ordinary_setup": ordinary_setup.model_dump(mode="json"),
    "ordinary_branch": ordinary_branch.model_dump(mode="json"),
}))
"""

_LESSON_IDENTITY = """
from my_experiment.lesson_identity import IDENTITY
from my_experiment.parameters import Drive

assert params.branch == f"{IDENTITY}-parameters"
assert params[Drive]["q0"].frequency == 5.15
lesson_setup = session.setup.get(f"{IDENTITY}-setup")
"""

_SAVE_ISOLATION = """
import json
from pathlib import Path

session = sc.notebook()
from my_experiment.parameters import Drive
from my_experiment.setup import open_parameters
params = open_parameters(session)
isolation = json.loads(Path(isolation_path).read_text())
if IDENTITY.startswith("refresh-"):
    params[Drive]["q0"].frequency = 5.17
    params.save(note="retained learner edit")
else:
    refresh_branch = isolation["refresh"]["branch"]
    assert session.parameters.workspace(refresh_branch)[Drive]["q0"].frequency == 5.17
    assert params[Drive]["q0"].frequency == 5.15
isolation[IDENTITY.split("-")[0]] = {
    "branch": params.branch,
    "setup": lesson_setup.model_dump(mode="json"),
}
ordinary = session.parameters.checkout("ordinary-control").head
assert ordinary.model_dump(mode="json") == isolation["ordinary_branch"]
ordinary_setup = session.setup.get("ordinary-control")
assert ordinary_setup.model_dump(mode="json") == isolation["ordinary_setup"]
assert session.parameters.workspace("ordinary-control")[Drive]["q0"].frequency == 4.7
Path(isolation_path).write_text(json.dumps(isolation))
session.close()
"""

_CHECK_ISOLATION = """
import json
from pathlib import Path

session = sc.notebook()
from my_experiment.parameters import Drive
from my_experiment.setup import open_parameters
isolation = json.loads(Path(isolation_path).read_text())
assert isolation["refresh"]["branch"] != isolation["compute"]["branch"]
assert isolation["refresh"]["setup"] != isolation["compute"]["setup"]
for topic, frequency in (("refresh", 5.17), ("compute", 5.15)):
    branch = isolation[topic]["branch"]
    assert session.parameters.workspace(branch)[Drive]["q0"].frequency == frequency
ordinary = session.parameters.checkout("ordinary-control").head
assert ordinary.model_dump(mode="json") == isolation["ordinary_branch"]
ordinary_setup = session.setup.get("ordinary-control")
assert ordinary_setup.model_dump(mode="json") == isolation["ordinary_setup"]
assert session.parameters.workspace("ordinary-control")[Drive]["q0"].frequency == 4.7
# Continuing the lesson must preserve the learner's saved parameter edit.
params = open_parameters(session)
expected = 5.17 if params.branch.startswith("refresh-") else 5.15
assert params[Drive]["q0"].frequency == expected
session.close()
"""
