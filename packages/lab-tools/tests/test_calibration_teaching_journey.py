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
                from unittest.mock import patch
                from my_experiment.calibration import (
                    check_request, check_zero,
                )

                concurrent = lab.procedures.submit(
                    check_zero,
                    check_request(initial=lab.parameters.resolve(namespace["accepted"].revision)),
                    request_key="history-concurrent-progress",
                )

                checks = lab.calibration_checks
                original_query = checks._client.query_calibration_checks

                def advancing_query(query):
                    page = original_query(query)
                    if any(item.execution.procedure_run_id == concurrent.id
                           and item.evidence is None for item in page.items):
                        concurrent.resume()
                    return page

                with patch.object(
                    checks._client, "query_calibration_checks", advancing_query,
                ):
                    changed = checks.history(page_size=1)
                assert not changed.complete
                assert "journal_changed" in changed.incomplete_reasons
                assert concurrent.id in changed.unresolved_procedures
                with patch.object(checks._client, "get_procedure",
                                  side_effect=AssertionError("per-check HTTP read")):
                    stable = checks.history()
                assert stable.complete
                from dataclasses import replace
                # A queued check in another exact parameter context is visible,
                # but does not block this context's domain query.
                other_intent = check_request(
                    initial=lab.parameters.resolve(namespace["initial"]),
                )
                other = lab.procedures.submit(
                    check_zero, other_intent, request_key="other-context-check",
                )
                all_checks = checks.history()
                assert other.id in all_checks.unresolved_procedures
                declared = next(
                    item for item in all_checks.requests
                    if item.execution.procedure_run_id == other.id
                )
                assert declared.request == other_intent.calibration_check
                assert checks.history(context=namespace["current"]).complete
                other.resume()
                absent = checks.history(
                    scope=replace(
                        other_intent.calibration_check.scope,
                        conditions="never-requested",
                    ),
                    max_requests=1,
                )
                assert absent.complete and absent.scanned == 0
                exact = checks.history(context=other_intent.calibration_check.context)
                assert exact.complete
                budgeted = checks.history(
                    context=other_intent.calibration_check.context,
                    max_requests=len(exact.requests), page_size=1,
                )
                assert budgeted.complete
                assert budgeted.scanned == len(exact.requests)
                expected_runs += 2
            assert len(lab.runs().items) == expected_runs
            assert lab.config.registry().entries == ()
            outcomes = {r.summary().outcome for r in lab.procedures.list().items}
            assert outcomes == {"succeeded", "failed"}
finally:
    stop_project(project)
"""
