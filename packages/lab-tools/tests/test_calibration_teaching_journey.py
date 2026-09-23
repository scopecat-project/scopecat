"""Execute the shipped calibration cells, including a daemon restart at the pause."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lab_teaching.lessons import install_lesson
from lab_teaching.project import create_project


@pytest.mark.parametrize(
    ("topic", "expected_runs"), [("calibration", 7), ("joint-calibration", 12)]
)
def test_calibration_notebook_resumes_and_retains_rejection(
    tmp_path: Path, topic: str, expected_runs: int
) -> None:
    root = tmp_path / "calibration"
    create_project(root)
    install_lesson(root, topic)
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    result = subprocess.run(  # noqa: S603 - Fixed script and generated test directories.
        [sys.executable, "-c", _JOURNEY, str(root), topic, str(expected_runs)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# A separate process models a fresh kernel and respects one code root per process.
_JOURNEY = """
import json
import sys
from pathlib import Path
import scopecat as sc
from scopecat_server.lifecycle import start_project, stop_project

root = Path(sys.argv[1])
notebook = root / "notebooks" / (sys.argv[2] + ".ipynb")
sys.path.insert(0, str(root / "src"))
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
            exec(compile(source, str(notebook), "exec"), namespace)
            if "request_id = request.id" in source:
                stop_project(project)
                start_project(project, timeout=120)
        with project.connect() as lab:
            expected_runs = int(sys.argv[3])
            if sys.argv[2] == "calibration":
                from functools import partial
                from my_experiment.calibration import (
                    CheckIntent, check_zero, read_check,
                )

                concurrent = lab.procedures.submit(
                    check_zero,
                    CheckIntent(initial=lab.parameters.resolve(namespace["accepted"].revision)),
                    request_key="history-concurrent-progress",
                )

                def advancing_reader(handle, snapshot):
                    if handle.id == concurrent.id:
                        handle.resume()
                    return read_check(lab, handle, snapshot)

                changed = lab.procedures.check_history(
                    procedure_id=check_zero.ref.id, read=advancing_reader, page_size=1,
                )
                assert not changed.complete
                assert "journal_changed" in changed.incomplete_reasons
                assert concurrent.id in changed.unresolved_procedures
                stable = lab.procedures.check_history(
                    procedure_id=check_zero.ref.id, read=partial(read_check, lab),
                )
                assert stable.complete
                expected_runs += 1
            assert len(lab.runs().items) == expected_runs
            assert lab.config.registry().entries == ()
            outcomes = {r.summary().outcome for r in lab.procedures.list().items}
            assert outcomes == {"succeeded", "failed"}
finally:
    stop_project(project)
"""
