"""Shipped managed lessons in real kernels; task and internal recovery regressions."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lab_teaching.project import create_project


@pytest.mark.parametrize(
    ("topic", "expected_runs"),
    [("task-calibration", 12)],
)
def test_calibration_notebook_resumes_and_retains_rejection(
    tmp_path: Path, topic: str, expected_runs: int
) -> None:
    root = tmp_path / "calibration"
    create_project(root, topic=topic)
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
    create_project(root, topic="calibration")
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
        lab = project.connect()
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
        # Exact fit boundary belongs to the external recovery harness.
        handle = lab.procedures.get(first.procedure_run_id)
        lab.procedures.resume_snapshot(
            first, should_yield=lambda: len(handle.steps().items) >= 2,
        )
        assert handle.state == "ready"
        assert len(handle.steps().items) == 2
        baseline_id = handle.output("baseline").run_id
        assert lab.parameters.checkout(namespace["trial"]).head == intent.destination
        stop_project(project)
        start_project(project, timeout=120)
        lab.close()
        lab = project.connect()
        handle = lab.procedures.get(first.procedure_run_id)
        session.close()
        session = project.authoring()
        run_worker(first.procedure_run_id)
        assert handle.output("baseline").run_id == baseline_id
        assert len(lab.runs().items) == 2
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


_HISTORY_CHECKS = """
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
"""


@pytest.mark.parametrize("topic", ["calibration", "joint-calibration"])
def test_managed_calibration_shipped_kernel(tmp_path, monkeypatch, topic):
    import json

    from nbclient import NotebookClient
    from nbformat import read, v4, write

    from lab_tools.application_runtime import ApplicationRuntime
    from lab_tools.notebook import kernel_command
    from scopecat.project import open_project
    from scopecat_server.lifecycle import start_project, stop_project

    root = create_project(tmp_path / "作者代码", topic=topic).parent
    material = (
        Path(__file__).resolve().parents[2]
        / "lab-teaching/src/lab_teaching/course_material/lessons"
    )
    path = root / "notebooks" / f"{topic}.ipynb"
    assert path.read_bytes() == (material / path.name).read_bytes()
    for name in ("calibration", "joint_calibration"):
        generated = root / f"src/my_experiment/{name}.py"
        if generated.exists():
            assert generated.read_bytes() == (material / f"{name}.py.txt").read_bytes()
    runtime = ApplicationRuntime(tmp_path / "应用数据")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    runtime.configure(static_dir=gui)
    runtime.register_source(root)
    application = open_project(runtime.root)
    _, environment = kernel_command(root, source_path=False)
    monkeypatch.setenv("JUPYTER_PATH", environment["JUPYTER_PATH"])
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    notebook = read(path, as_version=4)
    # Observe the learner's actual close call, not a substituted binding/cell.
    # The independent observer waits while that session is closed, proving the
    # real worker keeps executing without a live Notebook client.
    notebook.cells.insert(5, v4.new_code_cell(_OBSERVE_RUNNING_DISCONNECT))
    notebook.cells.append(v4.new_code_cell(_MANAGED_CHECKS))
    notebook.cells.append(v4.new_code_cell(_SAVE_EVIDENCE))
    reopen = v4.new_notebook(cells=[v4.new_code_cell(_REOPEN_CHECKS)])
    try:
        start_project(application, timeout=120)
        for name, document in (("managed", notebook), ("reopened", reopen)):
            if name == "reopened":
                stop_project(application)
                start_project(application, timeout=120)
            try:
                NotebookClient(
                    document,
                    timeout=120,
                    kernel_name="scopecat-lab",
                    resources={"metadata": {"path": str(root / "notebooks")}},
                ).execute()
            finally:
                write(document, root / f"notebooks/verified-{name}.ipynb")
        evidence = json.loads((root / "notebooks/evidence.json").read_text())
        assert evidence["disconnect_state"] == "leased"
        assert evidence["runs"] == (6 if topic == "calibration" else 12)
        if topic == "joint-calibration":
            result = subprocess.run(  # noqa: S603 - fixed regression and test source
                [
                    sys.executable,
                    "-c",
                    _INCOMPLETE_COVERAGE,
                    str(root),
                    evidence["request"],
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr
    finally:
        stop_project(application)


_OBSERVE_RUNNING_DISCONNECT = """
import time
from scopecat.daemon.client import DaemonClient

original_close = session.close

def observe_close():
    deadline = time.monotonic() + 30
    while True:
        view = request.progress()
        assert view.procedure.closure is None, "Need an in-flight close witness"
        if view.procedure.state == "leased" and view.dispatch.worker_running:
            break
        assert time.monotonic() < deadline, view
        time.sleep(0.02)
    global disconnect_state
    disconnect_state = view.procedure.state
    original_close()
    assert session.is_closed
    with DaemonClient(session.base_url, workspace_id=session.workspace_id) as observer:
        deadline = time.monotonic() + 90
        while True:
            retained = observer.get_procedure(request_id)
            if retained.closure is not None:
                assert retained.closure.status == "succeeded", retained
                break
            assert retained.state != "attention_required", retained
            assert time.monotonic() < deadline, retained
            time.sleep(0.1)
    assert session.is_closed

session.close = observe_close
"""

_MANAGED_CHECKS = """
import scopecat as sc
session = sc.notebook()
request = session.procedures.get(request_id)
rejected = session.procedures.get(rejected.id)
assert request.snapshot.closure.status == "succeeded"
assert rejected.snapshot.closure.status == "failed"
assert request.snapshot.source == prepared.command.source
assert dict(request.snapshot.intent) == dict(prepared.command.intent)
assert prepared.reconnect(session).submit().id == request_id
assert session.parameters.checkout(trial).head == accepted
assert accepted.generation == destination.generation + 1
assert session.parameters.checkout(rejected_branch.name).head == rejected_branch
assert session.config.registry().entries == ()
assert len(request.steps().items) == (5 if trial.startswith("zero-") else 13)
assert {p.procedure_run_id for p in session.procedures.list().items} >= {
    request_id, rejected.id,
}
assert decision["accepted"] is False
if trial.startswith("zero-"):
    assert not report.parameter_proposals
    assert not result.passed
    assert selected.status == "out_of_spec"
    assert selected.evidence.analysis_record_id == report.id
    assert history.complete
    assert session.calibration_checks.history().complete
    from datetime import timedelta
    from dataclasses import replace
    from my_experiment.calibration import CHECK_RESULT, check_scope
    from scopecat.automation.calibration import assess_calibration_check
    for label, expected in (("healthy", True), ("drifted", False)):
        saved = next(p for p in session.procedures.list().items
                     if p.request_key == trial + "-" + label)
        health = session.procedures.get(saved.procedure_run_id)
        assert health.snapshot.closure.status == "succeeded"
        assert len(health.steps().items) == 2
        measured = session.run(health.output("check").run_id)
        report = measured.published_analysis(health.output("assess").analysis_record_id)
        result = report.fact_as("check", CHECK_RESULT)
        assert result.passed is expected
        assert not report.parameter_proposals
    observed = measured.snapshot
    def assess(scope=None, context=current, at=now):
        return assess_calibration_check(
            observed, checked_scope=result.scope,
            requested_scope=check_scope(0.01) if scope is None else scope,
            passed=result.passed, current=context, now=at,
            max_age=timedelta(hours=1),
        )
    assert assess().status == "out_of_spec"
    assert "policy_changed" in assess(check_scope(0.02)).reasons
    assert "parameters_changed" in assess(
        context=replace(current, parameters=destination.revision)
    ).reasons
    expired = assess(at=observed.created_at + timedelta(hours=1))
    assert expired.status == "recheck" and "check_expired" in expired.reasons
    limited = session.calibration_checks.history(max_requests=1)
    assert not limited.complete
    assert limited.select(
        requested_scope=check_scope(0.01), current=current, now=now,
        max_age=timedelta(hours=1),
    ).reason == "incomplete_history"
else:
    assert set(decision["rejected"]) == {"q0", "q1"}
    assert not decision["missing"]
    for target in ("q0", "q1"):
        assert session.published_analysis(
            rejected.output(f"individual-decision-{target}").analysis_record_id
        ).fact("decision").value["accepted"]
"""

_INCOMPLETE_COVERAGE = """
# Internal analysis regression runs in its own source-importing process.
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as internal:
    from my_experiment.joint_calibration import verify_joint
    request = internal.procedures.get(sys.argv[2])
    incomplete = internal.analyze(verify_joint(
        targets=("q0", "q1"),
        baselines={t: internal.get_run(request.output(f"baseline-{t}").run_id)
                   for t in ("q0", "q1")},
        checks={"q0": internal.get_run(request.output("joint-q0").run_id)},
        tolerance=0.01,
    )).fact("decision").value
    assert not incomplete["accepted"]
    assert not incomplete["rejected"]
    assert tuple(incomplete["missing"]) == ("q1",)
"""


_SAVE_EVIDENCE = """
import json
from pathlib import Path

runs = session.list_runs(limit=100).items
baseline_key = "baseline" if trial.startswith("zero-") else "baseline-q0"
run = session.run(request.output(baseline_key).run_id)
raw = {key: list(run.measurements()[key].require_values())
       for key in ("setting", "residual")}
evidence = {
    "request": request_id, "rejected": rejected.id, "trial": trial,
    "runs": len(runs), "run_ids": [r.run_id for r in runs],
    "source": request.snapshot.source.model_dump(mode="json"),
    "intent": request.snapshot.model_dump(mode="json")["intent"],
    "accepted": accepted.model_dump(mode="json"),
    "destination": destination.model_dump(mode="json"),
    "initial": initial.model_dump(mode="json"),
    "baseline_key": baseline_key, "raw": raw,
    "disconnect_state": disconnect_state,
}
Path("evidence.json").write_text(json.dumps(evidence))
session.close()
"""

_REOPEN_CHECKS = """
import json
from pathlib import Path
import scopecat as sc

session = sc.notebook()
evidence = json.loads(Path("evidence.json").read_text())
request = session.procedures.get(evidence["request"])
assert request.snapshot.closure.status == "succeeded"
assert request.snapshot.source.model_dump(mode="json") == evidence["source"]
assert request.snapshot.model_dump(mode="json")["intent"] == evidence["intent"]
head = session.parameters.checkout(evidence["trial"]).head
assert head.model_dump(mode="json") == evidence["accepted"]
run = session.run(request.output(evidence["baseline_key"]).run_id)
for key, values in evidence["raw"].items():
    assert list(run.measurements()[key].require_values()) == values
runs = session.list_runs(limit=100).items
assert {r.run_id for r in runs} == set(evidence["run_ids"])
rejected = session.procedures.get(evidence["rejected"])
key = "verify" if evidence["trial"].startswith("zero-") else "verify-joint"
verification = session.published_analysis(rejected.output(key).analysis_record_id)
assert not verification.fact("decision").value["accepted"]
session.close()
"""


def test_calibration_history_policy_regressions(tmp_path: Path) -> None:
    import textwrap

    root = create_project(tmp_path / "history", topic="calibration").parent
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    script = (
        _HISTORY_SETUP
        + textwrap.indent(_HISTORY_CHECKS, "    ")
        + "\n    assert len(lab.runs().items) == expected_runs\n"
        + "finally:\n    lab.close()\n    stop_project(project)\n"
    )
    result = subprocess.run(  # noqa: S603 - isolated internal policy regression
        [sys.executable, "-c", script, str(root)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_HISTORY_SETUP = """
import sys
from pathlib import Path
import scopecat as sc
from scopecat_server.lifecycle import start_project, stop_project

project = sc.open_project(Path(sys.argv[1]))
start_project(project, timeout=120)
lab = project.connect()
try:
    from my_experiment.calibration import Sensor
    from workspace_app import initial_setup
    setup = lab.setup.import_recipe(initial_setup(), name="teaching-bench")
    def revision(name, offset):
        return lab.parameters.save(
            name=name, catalog=sc.parameter_catalog("zero", Sensor),
            parameters=sc.parameter_snapshot(name, tables={
                Sensor: [Sensor(id="q0", offset=offset)],
            }),
        )
    initial = revision("initial", 0.1)
    accepted = lab.parameters.create_branch(
        "history", revision=revision("accepted", 0.24),
    )
    namespace = {
        "accepted": accepted, "initial": initial, "trial": "history",
        "current": lab.resolve_context(
            parameters=accepted.revision, setup=setup,
        ).context,
    }
    expected_runs = 0
"""
