"""Execute the shipped calibration cells, including a daemon restart at the pause."""

import json
from pathlib import Path

import scopecat as sc
from lab_teaching.lessons import install_lesson
from lab_teaching.project import create_project
from scopecat_server.lifecycle import start_project, stop_project


def test_calibration_notebook_resumes_and_retains_rejection(
    tmp_path: Path, notebook_imports
) -> None:
    root = tmp_path / "calibration"
    create_project(root)
    notebook = install_lesson(root, "calibration")
    notebook_imports.syspath_prepend(str(root / "src"))
    project = sc.open_project(root)
    start_project(project, timeout=120)
    try:
        with project.authoring() as session:
            namespace = {"sc": sc, "session": session}
            cells = json.loads(notebook.read_text(encoding="utf-8"))["cells"]
            for cell in cells:
                if cell["cell_type"] != "code":
                    continue
                source = "".join(cell["source"])
                if "sc.notebook()" in source:
                    # Wrong-interpreter protection is exercised by test_sandboxes.
                    continue
                exec(compile(source, str(notebook), "exec"), namespace)  # noqa: S102 - Execute shipped course cells.
                if "request_id = request.id" in source:
                    stop_project(project)
                    start_project(project, timeout=120)
            with project.connect() as lab:
                assert len(lab.runs().items) == 4  # Baseline + check for each request.
                assert lab.config.registry().entries == ()
                outcomes = {r.summary().outcome for r in lab.procedures.list().items}
                assert outcomes == {"succeeded", "failed"}
    finally:
        stop_project(project)
