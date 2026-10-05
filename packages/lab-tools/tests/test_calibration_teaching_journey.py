"""Execute the shipped calibration cells, including a daemon restart at the pause."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lab_teaching.lessons import install_lesson
from lab_teaching.project import create_project


@pytest.mark.parametrize(
    ("topic", "expected_runs"),
    [("calibration", 7), ("joint-calibration", 12), ("task-calibration", 12)],
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
import os
import sys
from pathlib import Path
from IPython.core.interactiveshell import InteractiveShell
import scopecat as sc
from scopecat_server.lifecycle import start_project, stop_project

root = Path(sys.argv[1])
notebook = root / "notebooks" / (sys.argv[2] + ".ipynb")
sys.path.insert(0, str(root / "src"))
project = sc.open_project(root)
start_project(project, timeout=120)
shell = InteractiveShell.instance()
os.chdir(root)
try:
    with shell.builtin_trap:
        namespace = shell.user_ns
        cells = json.loads(notebook.read_text(encoding="utf-8"))["cells"]
        for cell in cells:
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            result = shell.run_cell(source)
            result.raise_error()
            if "request_id = request.id" in source or "task_ids =" in source:
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
                    check_request(initial=lab.parameters.resolve(
                        namespace["accepted"].revision,
                        setup=lab.setup.get("teaching-bench"),
                    )),
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
                from scopecat.automation.calibration_tasks import (
                    CalibrationTaskPlan, CalibrationTaskStage,
                )
                declared = next(item.request for item in stable.requests
                                if item.execution.procedure_run_id == concurrent.id)
                staged = CalibrationTaskPlan(stages=(
                    CalibrationTaskStage(id="baseline", check=declared),
                    CalibrationTaskStage(id="followup", check=declared,
                                         depends_on=("baseline",)),
                    CalibrationTaskStage(id="independent", check=declared),
                ))
                progress = checks.preview_task(
                    staged, executions={"baseline": concurrent.id},
                )
                assert progress.stages[0].state == "passed"
                assert progress.ready == ("followup", "independent")
                assert not progress.complete
                # A queued check in another exact parameter context is visible,
                # but does not block this context's domain query.
                other_intent = check_request(
                    initial=lab.parameters.resolve(
                        namespace["initial"], setup=lab.setup.get("teaching-bench"),
                    ),
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
                from scopecat.api.calibration_tasks import task_call
                from scopecat.api.calibration_checks import CalibrationRequirement
                from datetime import timedelta
                from scopecat.daemon.client import DaemonConflictError
                import pytest

                resolved = lab.parameters.resolve(
                    namespace["accepted"].revision,
                    setup=lab.setup.get("teaching-bench"),
                )
                intents = {
                    "good": check_request(initial=resolved),
                    "bad": check_request(initial=resolved, disturbance=0.25),
                    "after-good": check_request(initial=resolved),
                    "after-bad": check_request(initial=resolved),
                }
                plan = CalibrationTaskPlan(stages=tuple(
                    CalibrationTaskStage(
                        id=key, check=intent.calibration_check,
                        depends_on=(key.removeprefix("after-"),)
                                   if key.startswith("after-") else (),
                    ) for key, intent in intents.items()
                ))
                tasks = lab.calibration_tasks
                created = tasks.create("teaching-round", plan, calls={
                    key: task_call(check_zero, intent)
                    for key, intent in intents.items()
                })
                assert created.progress.ready == ("good", "bad")
                with pytest.raises(DaemonConflictError, match="prerequisites"):
                    tasks.dispatch("teaching-round", "after-good")
                for key in ("good", "bad"):
                    task = tasks.dispatch("teaching-round", key)
                    lab.procedures.get(task.task.executions[key]).resume()
                progressed = tasks.get("teaching-round")
                assert progressed.progress.ready == ("after-good",)
                assert tuple(stage.state for stage in progressed.progress.stages) == (
                    "passed", "rejected", "ready", "blocked",
                )
                negative = checks.report(
                    context=intents["bad"].calibration_check.context,
                    requirements=(CalibrationRequirement(
                        id="readout", scope=intents["bad"].calibration_check.scope,
                        max_age=timedelta(hours=1),
                    ),),
                )
                assert negative.items[0].selection.status == "out_of_spec", negative
                with pytest.raises(DaemonConflictError, match="prerequisites"):
                    tasks.dispatch("teaching-round", "after-bad")
                followup = tasks.dispatch("teaching-round", "after-good")
                lab.procedures.get(followup.task.executions["after-good"]).resume()
                finished = tasks.get("teaching-round")
                assert finished.progress.complete and not finished.progress.successful
                assert len(finished.task.executions) == 3
                assert tasks.dispatch("teaching-round", "good").task == finished.task
                assert tasks.list().items == (finished.task,)
                expected_runs += 3
                import time

                automatic = tasks.create("automatic-round", plan, calls={
                    key: task_call(check_zero, intent)
                    for key, intent in intents.items()
                })
                paused = tasks.pause(
                    automatic, actor="test", reason="review before start",
                )
                assert paused.task.mode == "paused"
                tasks.start(paused, actor="test", reason="run in background")
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    automatic = tasks.get("automatic-round")
                    if automatic.task.mode == "finished":
                        break
                    time.sleep(0.1)
                assert automatic.task.mode == "finished", automatic
                assert automatic.progress.complete and not automatic.progress.successful
                assert tuple(stage.state for stage in automatic.progress.stages) == (
                    "passed", "rejected", "passed", "blocked",
                )
                assert len(automatic.task.executions) == 3
                expected_runs += 3
                requirement = CalibrationRequirement(
                    id="readout", scope=intents["good"].calibration_check.scope,
                    max_age=timedelta(hours=1),
                )
                report = checks.report(
                    context=intents["good"].calibration_check.context,
                    requirements=(requirement,
                        requirement.model_copy(update={
                            "id": "expired", "max_age": timedelta(microseconds=1),
                        }),
                        requirement.model_copy(update={
                            "id": "missing", "scope": replace(requirement.scope,
                                capability="not-measured"),
                        }),
                        requirement.model_copy(update={
                            "id": "dependent", "depends_on": ("missing",),
                        }),
                        requirement.model_copy(update={
                            "id": "downstream", "depends_on": ("dependent",),
                        }),
                    ),
                )
                assert tuple(item.selection.status for item in report.items) == (
                    "usable", "recheck", "unknown", "usable", "usable",
                ), report
                assert tuple(item.availability.status for item in report.items) == (
                    "usable", "recheck", "unknown", "blocked", "blocked",
                )
                assert report.items[3].availability.blocked_by == ("missing",)
                assert report.items[4].availability.blocked_by == ("dependent",)
                rendered = report._repr_html_()
                assert "Own check" in rendered and "Availability" in rendered
                assert "dependent evidence" in rendered
                assert "Exact measurement context" in rendered
                assert "Snapshot of declared requirements only" in repr(report)
                saved_profile = checks.save_profile(
                    "teaching-v1", requirements=(requirement,),
                )
                assert checks.profile("teaching-v1") == saved_profile
                assert saved_profile in checks.profiles().items
                saved_report = checks.report(
                    context=report.context, profile="teaching-v1",
                    requirement_ids=(requirement.id,),
                )
                assert saved_report.profile_id == "teaching-v1"
                assert saved_report.items[0].selection.status == "usable"
                assert "teaching-v1" in saved_report._repr_html_()
                subject = report.context.subject
                assert subject.kind == "unbound"
                captured = lab.resolve_context(
                    branch=namespace["trial"],
                    setup=lab.setup.get("teaching-bench"),
                )
                assert captured.branch is not None
                assert captured.branch.name == namespace["trial"]
                exact = lab.resolve_context(
                    parameters=lab.parameters.get(captured.context.parameters.revision_id),
                    setup=lab.setup.revision(captured.setup.revision_id),
                )
                assert exact.context == captured.context
                assert exact.branch is None
                assert captured.context.subject == subject
                assert report.items[1].selection.assessment.reasons == (
                    "check_expired",
                )
                assert report.items[2].selection.reason == "no_matching_evidence"
            assert len(lab.runs().items) == expected_runs
            assert lab.config.registry().entries == ()
            outcomes = {r.summary().outcome for r in lab.procedures.list().items}
            assert outcomes == {"succeeded", "failed"}
finally:
    if "session" in shell.user_ns:
        shell.user_ns["session"].close()
    stop_project(project)
"""


def test_procedure_capture_preserves_imports_and_checks_changed_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "calibration"
    create_project(root)
    install_lesson(root, "calibration")
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    result = subprocess.run(  # noqa: S603 - Fixed script and generated test directory.
        [sys.executable, "-c", _SOURCE_LIFECYCLE, str(root)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_SOURCE_LIFECYCLE = """
import importlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
import scopecat as sc
from scopecat_server.lifecycle import start_project, stop_project

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
project = sc.open_project(root)
start_project(project, timeout=120)

def run_worker(procedure_id):
    worker = subprocess.run([
        sys.executable, "-c",
        "from pathlib import Path; import sys; "
        "from scopecat_server.launch_worker import run_project_procedure; "
        "run_project_procedure(Path(sys.argv[1]), sys.argv[2])",
        str(root), procedure_id,
    ], capture_output=True, text=True, timeout=60)
    assert worker.returncode == 0, worker.stdout + worker.stderr

try:
    with project.authoring() as session:
        session.refresh()
        namespace = {"sc": sc, "session": session}
        cells = json.loads((root / "notebooks/calibration.ipynb").read_text())["cells"]
        exec("".join(cells[2]["source"]), namespace)
        lab = namespace["lab"]
        module = importlib.import_module("my_experiment.calibration")
        intent = module.CalibrationIntent(
            initial=lab.parameters.resolve(
                namespace["initial"], setup=namespace["setup"],
            ),
            destination=namespace["destination"],
            revision_name="capture-accepted",
        )
        first = lab.procedures.submit(
            module.calibrate, intent, request_key="first",
        ).snapshot
        assert importlib.import_module(module.__name__) is module
        run_worker(first.procedure_run_id)
        completed = lab.procedures.get(first.procedure_run_id)
        assert completed.summary().outcome == "succeeded"
        lab.close()
        with project.connect() as lab:
            second = lab.procedures.submit(
                module.calibrate, intent, request_key="second",
            )
            assert second.snapshot.source == first.source
            source = root / "src/my_experiment/calibration.py"
            original = source.read_text()
            source.write_text(original.replace(
                '    baseline = ctx.run(',
                '    raise ValueError("changed implementation")\\n'
                '    baseline = ctx.run(',
            ))
            changed = lab.procedures.submit(
                module.calibrate, intent, request_key="changed",
            )
            assert changed.snapshot.source is not None
            assert first.source is not None
            assert (changed.snapshot.source.code_revision
                    != first.source.code_revision)
            assert importlib.import_module(module.__name__) is module
            run_worker(changed.id)
            rejected = changed.snapshot
            assert rejected.state == "attention_required", rejected
            assert "fingerprint" in rejected.attention_reason, rejected
            assert not changed.steps().items
        # Explicit author refresh still selects new imports and rejects old models.
        session.refresh()
        refreshed = importlib.import_module(module.__name__)
        assert refreshed is not module
        with pytest.raises(ValueError, match="CalibrationIntent"):
            refreshed.calibrate.validate_intent(intent)
finally:
    stop_project(project)
"""
