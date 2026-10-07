"""Run the generated, shipped grouping lesson with real kernels and restart."""

from pathlib import Path

import pytest
from nbclient import NotebookClient
from nbformat import read, v4, write

import scopecat as sc
from lab_teaching.project import create_project
from lab_tools.notebook import kernel_command
from lab_tools.verify_groups import GROUP_CHECKS, GROUP_REOPEN_CELLS, lesson_path
from lab_tools.verify_maintenance import ADD_ANALYSIS
from scopecat_server.lifecycle import start_project, stop_project


@pytest.mark.parametrize("topic", ["groups", None])
def test_grouped_teaching_restart(tmp_path: Path, monkeypatch, notebook_imports, topic):
    root = create_project(tmp_path / "分组 教材", topic=topic).parent
    path = lesson_path(root)
    material = (
        Path(__file__).resolve().parents[2]
        / "lab-teaching/src/lab_teaching/course_material"
    )
    # lab-teaching is non-editable: stale installed resources must fail explicitly.
    expected = {
        "notebooks/groups.ipynb": "lessons/groups.ipynb",
        "src/workspace_app.py": "lessons/workspace_app.py.txt",
        **{
            f"src/my_experiment/{name}.py": f"lessons/{template}.py.txt"
            for name, template in (
                ("parameters", "parameters"),
                ("setup", "parameters_setup" if topic == "groups" else "setup"),
                ("response", "response"),
                ("teaching", "experiment"),
            )
        },
        "src/my_experiment/group_analysis.py": "group_analysis.py",
        "src/my_experiment/result_types.py": "result_types.py",
    }
    for generated, source in expected.items():
        assert (root / generated).read_bytes() == (material / source).read_bytes(), (
            f"Reinstall scopecat-lab-teaching: stale {generated}"
        )
    _, environment = kernel_command(root, source_path=False)
    monkeypatch.setenv("JUPYTER_PATH", environment["JUPYTER_PATH"])
    notebook = read(path, as_version=4)
    notebook.cells.append(
        v4.new_code_cell(GROUP_CHECKS + "\nassert before_runs == {run.id}\n")
    )
    reopen = v4.new_notebook(
        cells=[
            *[v4.new_code_cell(c) for c in GROUP_REOPEN_CELLS],
            v4.new_code_cell(
                "assert before_runs == {bookmark['run_id'], bookmark['changed_run_id']}"
            ),
            v4.new_code_cell(ADD_ANALYSIS),
        ]
    )
    project = sc.open_project(root)
    for name, document in (("groups", notebook), ("groups-reopen", reopen)):
        start_project(project, timeout=120)
        try:
            NotebookClient(
                document,
                timeout=120,
                kernel_name="scopecat-lab",
                resources={"metadata": {"path": str(root / "notebooks")}},
            ).execute()
        finally:
            write(document, root / f"notebooks/verified-{name}.ipynb")
            stop_project(project)
