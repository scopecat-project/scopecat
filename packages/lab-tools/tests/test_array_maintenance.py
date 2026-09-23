"""Six targets retain partial progress, recover, and publish only complete evidence."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lab_teaching.lessons import install_lesson
from lab_teaching.project import create_project


@pytest.mark.parametrize(
    "case", ["healthy", "drift", "readout-failure", "branch-conflict"]
)
def test_array_maintenance(tmp_path: Path, case: str) -> None:
    root = tmp_path / "array"
    create_project(root)
    install_lesson(root, "task-calibration")
    shutil.copy2(
        Path(__file__).parent / "fixtures/array_maintenance.py",
        root / "src/my_experiment/array_maintenance.py",
    )
    manifest = root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        .replace(
            "my_experiment.task_calibration:fit_stage",
            "my_experiment.array_maintenance:check",
        )
        .replace(
            "my_experiment.task_calibration:finalize",
            "my_experiment.array_maintenance:finish",
        )
    )
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    result = subprocess.run(  # noqa: S603 - Fixed scenario and disposable project.
        [sys.executable, "-c", _JOURNEY, str(root), case],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_JOURNEY = """
import sys, time
from pathlib import Path
import scopecat as sc
from scopecat.records.run import ParameterRunConfigSource
from scopecat_server.lifecycle import start_project, stop_project

root, case = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / "src"))
from my_experiment.array_maintenance import Channel, TARGETS, DECISION, create_task
project = sc.open_project(root)

def wait_stage(lab, name):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        view = lab.calibration_tasks.get("array-round")
        stage = next(s for s in view.progress.stages if s.id == name)
        if stage.state == "passed":
            return view, stage
        assert stage.state not in {"failed", "rejected", "attention_required"}, stage
        time.sleep(0.05)
    raise AssertionError(view)

start_project(project)
try:
    with project.connect() as lab:
        setup = lab.setup.active()
        original = lab.parameters.save(
            name="array-initial", catalog=sc.parameter_catalog("array", Channel),
            parameters=sc.parameter_snapshot("array", tables={
                Channel: [Channel(id=t, offset=0.1) for t in TARGETS]}),
        )
        destination = lab.parameters.create_branch("daily", revision=original)
        initial = lab.parameters.resolve(original, setup=setup.revision.ref)
        task = create_task(lab, initial, destination,
            failed_group="readout-b" if case == "readout-failure" else None,
            drift_target="q4" if case == "drift" else None)
        prefix = lab.calibration_tasks.dispatch("array-round", "readout-a")
        lab.procedures.get(prefix.task.executions["readout-a"]).resume()
        wait_stage(lab, "readout-a")
        prefix = lab.calibration_tasks.dispatch("array-round", "q0")
        lab.procedures.get(prefix.task.executions["q0"]).resume()
        prefix, completed = wait_stage(lab, "q0")
        retained = lab.get_run(completed.evidence.measurement.run_id).snapshot
        assert lab.parameters.workspace("daily").version == original
    # Restart after retaining a fitted candidate, before admitting the other groups.
    stop_project(project)
    start_project(project)
    with project.connect() as lab:
        recovered = lab.calibration_tasks.get("array-round")
        assert recovered.task.executions == prefix.task.executions
        assert lab.get_run(retained.run_id).snapshot == retained
        expected = original
        if case == "branch-conflict":
            editor = lab.parameters.workspace("daily")
            editor[Channel]["q5"].offset = 0.33
            expected = editor.save(note="concurrent operator edit")
        lab.calibration_tasks.start(
            recovered, actor="maintainer", reason="finish array")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = lab.calibration_tasks.get("array-round")
            if result.task.mode == "finished":
                break
            time.sleep(0.1)
        assert result.task.mode == "finished", result
        assert result.task.executions["q0"] == prefix.task.executions["q0"]
        states = {s.id: s.state for s in result.progress.stages}
        assert lab.get_run(retained.run_id).snapshot == retained
        if case == "readout-failure":
            assert states["readout-b"] == "failed", states
            assert states["q2"] == states["q3"] == "blocked", states
            assert all(states[t] == "passed" for t in ("q0", "q1", "q4", "q5")), states
            assert result.finalization is None
            assert len(result.task.executions) == 7
            failed = lab.procedures.get(result.task.executions["readout-b"])
            assert failed.snapshot.closure.status == "failed"
            assert "synthetic readout outage" in failed.snapshot.closure.reason
            assert lab.parameters.workspace("daily").version == original
        else:
            assert result.progress.successful
            assert len(result.task.executions) == 9
            assert result.finalization is not None
            final = lab.procedures.get(result.finalization.procedure_run_id)
            assert final.step("verify").state == "succeeded", final.summary()
            verification = final.output("verify")
            published = lab.published_analysis(verification.analysis_record_id)
            decision = published.fact_as("decision", DECISION)
            assert decision.checked == TARGETS
            assert decision.rejected == (("q4",) if case == "drift" else ())
            if case == "healthy":
                assert final.snapshot.closure.status == "succeeded"
                saved = lab.parameters.workspace("daily")
                assert saved.version.ref != original.ref
                for index, target in enumerate(TARGETS):
                    fitted = 0.2 + 0.01 * index
                    assert abs(saved[Channel][target].offset - fitted) < 1e-12
            else:
                assert final.snapshot.closure.status == "failed", final.snapshot.closure
                assert lab.parameters.workspace("daily").version == expected
                reason = ("array verification rejected" if case == "drift"
                    else "parameter branch changed")
                assert reason in final.snapshot.closure.reason
            # All fit inputs remain captured even if daily was concurrently changed.
            for stage in result.progress.stages:
                source = lab.get_run(
                    stage.evidence.measurement.run_id).snapshot.config_source
                assert isinstance(source, ParameterRunConfigSource)
                assert source.parameters == original.ref
            # Coupling is actually present in the aggregate remeasurement.
            assert published.fact("residual-q0").value > 0
        assert lab.config.registry().entries == ()
        assert lab.setup.active() == setup
        print(case, states)
finally:
    stop_project(project)
"""
