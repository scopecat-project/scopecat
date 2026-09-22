"""Installed teaching contract: local refresh, retained analysis and restart."""

import os
from pathlib import Path

import httpx2
import numpy as np

import scopecat as sc
from lab_teaching.parameters import Drive
from lab_teaching.project import create_project
from lab_teaching.session import analyze_rabi, open_parameters
from scopecat.daemon.wire import SampleCreateCommand
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.sample import SampleRevisionDraft
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
            assert session.params is params
            assert session.selection.parameter_branch == "learner-choice"
            assert session.parameter_branch.head.revision == version.ref
            params[Drive]["q0"].frequency = 5.147
            session.create_sample(
                SampleCreateCommand(
                    operation_id="synthetic-subject",
                    sample_id="chip",
                    kind="synthetic",
                    actor="author",
                    content=SampleRevisionDraft(display_name="Chip"),
                )
            )
            batch = session.create_experimental_batch("Cooldown")
            prepared = session.prepare(
                "teaching.rabi",
                parameters=params,
                sample="chip",
                batch=batch.id,
                scans={"amplitude": np.linspace(0, 0.8, 21)},
            )
            frozen = prepared.preview.reviewed.config_source
            assert isinstance(frozen, ParameterRunConfigSource)
            assert frozen.parameters == version.ref
            assert frozen.overrides
            params[Drive]["q0"].frequency = 5.149
            job = prepared.run()
            run = job.wait(timeout=120).result()
            assert run.snapshot.config_source == frozen
            subject = run.snapshot.scientific_binding.samples[0]
            assert (subject.sample_id, subject.batch_id) == ("chip", batch.id)
            assert session.selection.science.subject.kind == "unbound"
            assert session.selection.parameter_branch == "learner-choice"
            assert params.version == version
            params.discard()
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
            session.use(parameter_branch="learner-choice")
            assert session.params.version == version
            assert session.params[Drive]["q0"].frequency == 5.148
            restored = session.reopen(receipt).wait(timeout=120).result()
            assert restored.id == run_id
            assert (
                analyze_rabi(session, restored).pi_amplitude
                == first_report.pi_amplitude
            )
    finally:
        stop_project(project)


def test_live_requests_history_and_failed_refresh(
    tmp_path: Path, notebook_imports
) -> None:
    import importlib

    import pytest

    from scopecat.daemon.preparation import AuthorPreparationFailed

    root = tmp_path / "live"
    create_project(root)
    project = sc.open_project(root)
    start_project(project, timeout=120)
    try:
        with project.authoring() as session:
            session.refresh()
            declaration = importlib.import_module(
                "my_experiment.teaching"
            ).teaching_rabi
            live = session.live(declaration)
            first = live()
            generation = session.state().generation
            unchanged = live()
            assert session.state().generation == generation
            assert (
                unchanged.declaration.code_revision == first.declaration.code_revision
            )
            params = open_parameters(session)
            prepared = session.prepare(first, parameters=params)
            source = root / "src/my_experiment/teaching.py"
            original = source.read_text(encoding="utf-8")
            updated = original.replace("seed: int = 200", "seed: int = 401")
            source.write_text(updated, encoding="utf-8")
            second = live()
            assert second.snapshot()["seed"] == 401
            assert first.snapshot()["seed"] == 200
            assert second.declaration.code_revision != first.declaration.code_revision
            # A failed automatic refresh must never silently execute the last good code.
            source.write_text(
                updated + "\nthis is invalid Python !\n", encoding="utf-8"
            )
            with pytest.raises(AuthorPreparationFailed):
                live()
            source.write_text(updated, encoding="utf-8")
            assert live().declaration.code_revision == second.declaration.code_revision
            # The old preview still executes its admitted revision after the edit.
            run = prepared.run().wait(timeout=120).result()
            number = session.run_number(run)
            assert session.run(number).id == run.id
            assert str(number) in repr(session.history())
            assert "<table>" in session.history()._repr_html_()
            later = (
                session.prepare(second, parameters=params)
                .run()
                .wait(timeout=120)
                .result()
            )
            assert session.run_number(later) != number
            assert session.run(number).id == run.id
            with pytest.raises(KeyError):
                session.run(1000000)
        with project.authoring() as reader:
            assert reader.run(number).id == run.id
    finally:
        stop_project(project)
