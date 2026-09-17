"""Installed teaching contract: local refresh, retained analysis and restart."""

import os
from pathlib import Path

import httpx2
import numpy as np

import scopecat as sc
from lab_teaching.parameters import Drive
from lab_teaching.project import create_project
from lab_teaching.session import analyze_rabi, open_parameters
from scopecat_server.lifecycle import start_project, stop_project


def test_minimal_teaching_refresh_and_restart(tmp_path: Path, notebook_imports) -> None:
    root = tmp_path / "minimal"
    create_project(root)
    project = sc.open_project(root)
    static_dir = os.environ.get("SCOPECAT_TEST_GUI_DIR")
    record = start_project(project, timeout=120, static_dir=static_dir)
    try:
        if static_dir:
            response = httpx2.get(record.base_url)
            response.raise_for_status()
            assert "<html" in response.text
        with project.authoring() as session:
            assert [entry.id for entry in session.catalog().entries] == [
                "teaching.rabi"
            ]
            params = open_parameters(session)
            assert list(params) == ["teaching_drive"]
            params[Drive]["q0"].frequency = 5.148
            version = params.save("learner-choice")
            prepared = session.prepare(
                "teaching.rabi",
                parameters=params,
                scans={"amplitude": np.linspace(0, 0.8, 21)},
            )
            job = prepared.run()
            run = job.wait(timeout=120).result()
            first_report = analyze_rabi(session, run)
            assert first_report.status == "passed"
            state = session.state()
            source = root / "src/my_experiment/teaching.py"
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "seed: int = 200", "seed: int = 400"
                ),
                encoding="utf-8",
            )
            refreshed = session.refresh()
            assert refreshed.active != state.active
            second = (
                session.prepare(
                    "teaching.rabi",
                    parameters=params,
                    scans={"amplitude": np.linspace(0, 0.8, 21)},
                )
                .run()
                .wait(timeout=120)
                .result()
            )
            assert not np.array_equal(
                np.asarray(run.measurements()["iq"].require_values()),
                np.asarray(second.measurements()["iq"].require_values()),
            )
            assert analyze_rabi(session, run).pi_amplitude == first_report.pi_amplitude
            current = session.analyze(
                run.id,
                "lab_teaching.analysis:rabi_diagnostic",
                code_revision=refreshed.active,
            )
            assert (
                run.published_analysis(current.analysis_id)
                .fact("diagnostic")
                .value["status"]
                == "passed"
            )
            receipt = job.receipt
            run_id = run.id
    finally:
        stop_project(project)
    start_project(project, timeout=120, static_dir=static_dir)
    try:
        with project.authoring() as session:
            assert (
                session.config.workspace(context=version.name)[Drive]["q0"].frequency
                == 5.148
            )
            restored = session.reopen(receipt).wait(timeout=120).result()
            assert restored.id == run_id
            assert (
                analyze_rabi(session, restored).pi_amplitude
                == first_report.pi_amplitude
            )
    finally:
        stop_project(project)
