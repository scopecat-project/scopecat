"""Six targets retain partial progress, recover, and publish only complete evidence."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lab_teaching.project import create_project


@pytest.mark.parametrize(
    "case",
    [
        "healthy",
        "drift",
        "readout-failure",
        "branch-conflict",
        "repair",
        "repair-budget",
        "repair-failure",
        "repair-drift",
        "repair-conflict",
        "repair-verification",
        "repair-noop",
    ],
)
def test_array_maintenance(tmp_path: Path, case: str) -> None:
    run_array_maintenance(tmp_path, case=case, target_count=6)


def run_array_maintenance(tmp_path: Path, *, case: str, target_count: int) -> None:
    root = tmp_path / "array"
    create_project(root, topic="task-calibration")
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
        .replace(
            '"my_experiment.array_maintenance:check",',
            '"my_experiment.array_maintenance:check", '
            '"my_experiment.array_maintenance:probe",',
        )
    )
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    environment["SCOPECAT_TEST_ARRAY_SIZE"] = str(target_count)
    result = subprocess.run(  # noqa: S603 - Fixed scenario and disposable project.
        [sys.executable, "-c", _JOURNEY, str(root), case],
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_JOURNEY = """
import sys, time
from pathlib import Path
import scopecat as sc
from scopecat.records.run import ParameterRunConfigSource
from scopecat_server.lifecycle import start_project, stop_project

root, case = Path(sys.argv[1]), sys.argv[2]
repair_mode = case.startswith("repair")
sys.path.insert(0, str(root / "src"))
from my_experiment.array_maintenance import (
    Channel, TARGETS, GROUPS, DECISION, create_task,
)
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
        setup = lab.setup.get("initial")
        original = lab.parameters.save(
            name="array-initial", catalog=sc.parameter_catalog("array", Channel),
            parameters=sc.parameter_snapshot("array", tables={
                Channel: [Channel(id=t,
                    offset=(0.2 + 0.01 * int(t[1:])) if case == "repair-noop"
                    else 0.21 if repair_mode and t == "q1" else 0.1)
                    for t in TARGETS]}),
        )
        destination = lab.parameters.create_branch("daily", revision=original)
        initial = lab.parameters.resolve(original, setup=setup.ref)
        task = create_task(lab, initial, destination,
            failed_group="readout-b" if case == "readout-failure" else None,
            drift_target="q4" if case in {"drift", "repair-drift"} else None,
            repair_mode=repair_mode, max_repairs=0 if case == "repair-budget" else 6,
            reject_fit="q0" if case == "repair-failure" else None,
            bad_candidate="q0" if case == "repair-verification" else None)
        prefix = lab.calibration_tasks.dispatch("array-round", "readout-a")
        lab.procedures.get(prefix.task.executions["readout-a"]).resume()
        wait_stage(lab, "readout-a")
        prefix = lab.calibration_tasks.dispatch("array-round", "q0")
        lab.procedures.get(prefix.task.executions["q0"]).resume()
        if repair_mode:
            prefix = lab.calibration_tasks.get("array-round")
            completed = next(s for s in prefix.progress.stages if s.id == "q0")
            expected_state = "passed" if case == "repair-noop" else "repair_ready"
            assert completed.state == expected_state, completed
        else:
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
        if case in {"branch-conflict", "repair-conflict"}:
            editor = lab.parameters.workspace("daily")
            editor[Channel]["q5"].offset = 0.33
            expected = editor.save(note="concurrent operator edit")
        lab.calibration_tasks.start(
            recovered, actor="maintainer", reason="finish array")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = lab.calibration_tasks.get("array-round")
            if result.task.mode == "finished":
                break
            time.sleep(0.1)
        assert result.task.mode == "finished", result
        assert result.task.attempts["q0"][0] == prefix.task.attempts["q0"][0]
        states = {s.id: s.state for s in result.progress.stages}
        assert lab.get_run(retained.run_id).snapshot == retained
        if case in {"repair-budget", "repair-failure", "repair-verification"}:
            assert result.finalization is None
            assert lab.parameters.workspace("daily").version == original
            if case == "repair-budget":
                assert result.task.stop_reason == "repair_budget_exhausted"
                assert result.task.repairs_used == 0
            else:
                assert states["q0"] == "rejected", states
                count = 3 if case == "repair-verification" else 2
                assert len(result.task.attempts["q0"]) == count
                assert all(states[t] == "passed" for t in TARGETS if t != "q0"), states
        elif case == "readout-failure":
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
            assert len(result.task.executions) == len(GROUPS) + len(TARGETS)
            assert result.finalization is not None
            final = lab.procedures.get(result.finalization.procedure_run_id)
            assert final.step("verify").state == "succeeded", final.summary()
            verification = final.output("verify")
            published = lab.published_analysis(verification.analysis_record_id)
            decision = published.fact_as("decision", DECISION)
            assert decision.checked == TARGETS
            expected_rejected = ("q4",) if case in {"drift", "repair-drift"} else ()
            assert decision.rejected == expected_rejected
            if case == "repair-noop":
                assert final.snapshot.closure.status == "succeeded"
                assert lab.parameters.workspace("daily").version == original
                assert result.task.repairs_used == 0
            elif case in {"healthy", "repair"}:
                assert final.snapshot.closure.status == "succeeded"
                selected_run = final.output(f"verify-{TARGETS[0]}").run_id
                saved = lab.parameters.workspace("daily")
                assert saved.version.ref != original.ref
                for index, target in enumerate(TARGETS):
                    fitted = 0.2 + 0.01 * index
                    tolerance = 0.003 if repair_mode else 1e-12
                    assert abs(saved[Channel][target].offset - fitted) < tolerance
            else:
                assert final.snapshot.closure.status == "failed", final.snapshot.closure
                assert lab.parameters.workspace("daily").version == expected
                reason = ("array verification rejected"
                    if case in {"drift", "repair-drift"}
                    else "parameter branch changed")
                assert reason in final.snapshot.closure.reason
            # All fit inputs remain captured even if daily was concurrently changed.
            for stage in result.progress.stages:
                source = lab.get_run(
                    stage.evidence.measurement.run_id).snapshot.config_source
                if result.task.attempts[stage.id][-1].phase == "verify":
                    assert source.kind == "analysis_candidate", source
                    phases = [a.phase for a in result.task.attempts[stage.id]]
                    assert phases == ["check", "repair", "verify"]
                else:
                    assert isinstance(source, ParameterRunConfigSource)
                    assert source.parameters == original.ref
            # Coupling is actually present in the aggregate remeasurement.
            assert published.fact("residual-q0").value > 0
        assert lab.config.registry().entries == ()
        assert lab.setup.get("initial") == setup
        print(case, states)
finally:
    stop_project(project)

if case == "healthy":
    from scopecat.data_exchange import ScientificExchange
    from scopecat_server.storage.sqlite.connection import SQLiteDatabase
    from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
    from scopecat_server.storage.sqlite.evidence_graph import export_scientific_capture
    data_root = project.runtime_binding.data_root
    store = SQLiteProjectStore(
        SQLiteDatabase(data_root / "control.sqlite3"), data_root / "objects")
    try:
        destination = root / "composed-calibration.scopecat"
        export_scientific_capture(store, (selected_run,), destination)
        with ScientificExchange(destination) as capture:
            capture.verify()
            assert selected_run in capture.evidence.roots
            assert len(capture.evidence.runs) >= len(TARGETS) + 1
            assert any(item.record.title == "array-offsets"
                for item in capture.evidence.analyses)
            from scopecat.records.parameter_change import ParameterChangeProposal
            from scopecat.runs.refs import content_entry_ref
            from scopecat.data_exchange.proposals import validate_proposal_references
            proposals = []
            for run in capture.evidence.runs:
                for entry in run.contents:
                    if entry.kind != "parameter_change_proposal":
                        continue
                    reference = next(ref for ref in capture.payloads
                        if ref.owner_kind == "run"
                        and ref.owner_id == run.snapshot.run_id
                        and ref.ref == content_entry_ref(entry))
                    target = root / f"proposal-{len(proposals)}.json"
                    capture.copy_payload(reference, target)
                    proposals.append(ParameterChangeProposal.model_validate_json(
                        target.read_bytes()))
            composed = next(item for item in proposals if item.composition is not None)
            original = composed.composition.sources
            corrupted = original[0].model_copy(update={
                "content_hash": "sha256:" + "f" * 64})
            changed = composed.model_copy(update={
                "composition": composed.composition.model_copy(update={
                    "sources": (corrupted, *original[1:])})})
            try:
                validate_proposal_references(capture.evidence, tuple(
                    changed if item is composed else item for item in proposals))
            except ValueError as error:
                assert "composed proposal content identity differs" in str(error), error
            else:
                raise AssertionError("changed composition source was accepted")
    finally:
        store.close()
"""
