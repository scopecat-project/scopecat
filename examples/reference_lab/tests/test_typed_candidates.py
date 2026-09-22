"""Receipt cells and independent verification without publishing shared defaults."""

import shutil
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import pytest
import scopecat as sc
from scopecat.analysis.facts import ordinary_result_schema
from scopecat.application import LabApplication
from scopecat.project import load_project
from scopecat.records.parameter import TableParameterValue
from scopecat.records.run import AnalysisCandidateRunConfigSource
from scopecat.records.sample import SampleRevisionDraft
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT, bootstrap_config
from reference_lab.exploration import exploration_config
from reference_lab.parameters import QubitParameters
from reference_lab.workflows.authored.ordinary_analysis import (
    PeakResult,
    PeakVerification,
)
from reference_lab.workflows.drag_beta_analysis import drag_beta_analysis
from reference_lab.workflows.drag_beta_experiment import drag_beta_experiment
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_VERIFICATION_SCHEMA,
    drag_beta_candidate_verification,
)

from .conftest import ReferenceLabDaemon

pytestmark = pytest.mark.usefixtures("reference_lab_author_imports")


@pytest.fixture
def candidate_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[ReferenceLabDaemon]:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "candidate-project"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(
        EXAMPLE_ROOT / "fixtures/equipment_bootstrap.py",
        root / "src/equipment_bootstrap.py",
    )
    (root / "scopecat.toml").write_text(
        (EXAMPLE_ROOT / "scopecat.toml")
        .read_text()
        .replace(
            "reference_lab.application:create_bootstrap",
            "equipment_bootstrap:create_bootstrap",
        )
    )
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    try:
        yield ReferenceLabDaemon(url=endpoint.base_url, root=root)
    finally:
        stop_project(project)


def test_typed_candidates_retain_cells_and_independent_policy(
    candidate_daemon: ReferenceLabDaemon,
) -> None:
    project = load_project(candidate_daemon.root / "scopecat.toml")
    analysis_module = "reference_lab.workflows.authored.ordinary_analysis"
    with (
        LabApplication().connect(candidate_daemon.url) as lab,
        project.authoring() as author,
    ):
        config = exploration_config(sc.Quantity(4.83, "GHz"))
        baseline = lab.parameters.save(
            name="baseline",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
        branch = lab.parameters.create_branch("daily", revision=baseline)
        registry = lab.config.registry()
        assert registry.entries == () and registry.activation is None
        setup = lab.setup.active()
        sample = lab.samples.create(
            "candidate-sample",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Candidate sample"),
        )
        author.use(sample=sample.id, parameter_branch="daily")
        author.refresh()
        run = (
            author.prepare(
                "signal",
                scans={"frequency": [4.7, 4.8, 4.9, 5.0]},
            )
            .run()
            .wait(timeout=60)
            .result()
        )
        fit = author.analyze_as(run.id, f"{analysis_module}:estimate_peak", PeakResult)
        assert fit.value.frequency is not None
        # The materialized dataclass is never authority, even if mutated locally.
        edited = replace(
            fit, value=replace(fit.value, frequency=sc.Quantity(5.9, "GHz"))
        )
        candidate = author.config.stage(
            edited,
            name="carrier",
            table=QubitParameters,
            key="q0",
            fields={QubitParameters.drive_carrier_frequency: "frequency"},
        )
        manual = (
            run.analysis("manual estimate")
            .result()
            .fact("result", fit.value, schema=ordinary_result_schema(PeakResult))
            .save()
            .result_as(PeakResult)
        )
        with pytest.raises(ValueError, match="no managed author source receipt"):
            author.config.stage(
                manual,
                name="manual",
                table="qubits",
                key="q0",
                fields={"drive_carrier_frequency": "frequency"},
            )
        original = run.config.parameter_snapshot.get("qubits")
        resolved = author.config.resolve(candidate.config).parameter_snapshot.get(
            "qubits"
        )
        assert isinstance(original, TableParameterValue) and isinstance(
            resolved, TableParameterValue
        )
        for before, after in zip(original.rows, resolved.rows, strict=True):
            for field, value in before.items():
                if field != "drive_carrier_frequency":
                    assert after[field] == value
        cells = candidate.config.parameter_proposal.deltas[0].cells
        assert cells is not None and len(cells) == 1
        assert isinstance(cells[0].after, sc.Quantity)
        assert cells[0].after.to("GHz").value == pytest.approx(
            fit.value.frequency.to("GHz").value
        )
        reopened = author.config.candidate(run.id, candidate.name)
        assert reopened.config == candidate.config
        assert lab.config.registry() == registry
        with pytest.raises(ValueError, match="independent retained"):
            candidate.verify(fit)
        unknown = author.analyze_as(
            run.id,
            f"{analysis_module}:estimate_peak",
            PeakResult,
            arguments={"minimum_contrast": 2.0},
        )
        with pytest.raises(ValueError, match="unknown"):
            author.config.stage(
                unknown,
                name="unknown",
                table="qubits",
                key="q0",
                fields={"drive_carrier_frequency": "frequency"},
            )
        prepared = author.prepare(
            "signal", candidate=reopened, scans={"frequency": [4.7, 4.8, 4.9, 5.0]}
        )
        check_run = prepared.run().wait(timeout=60).result()
        assert isinstance(
            check_run.snapshot.config_source, AnalysisCandidateRunConfigSource
        )
        assert check_run.samples == run.samples
        assert check_run.id != run.id
        check = author.analyze_as(
            check_run.id,
            f"{analysis_module}:verify_peak",
            PeakVerification,
            arguments={
                "expected_frequency": sc.Quantity(4.8, "GHz"),
                "tolerance": sc.Quantity(0.05, "GHz"),
            },
        )
        other = author.config.stage(
            fit,
            name="other-carrier",
            table="qubits",
            key="q0",
            fields={"drive_carrier_frequency": "frequency"},
        )
        with pytest.raises(ValueError, match="exact candidate"):
            other.verify(check)
        verified = candidate.verify(check)
        assert lab.parameters.checkout("daily").head == branch
        next_prepared = author.prepare("signal", candidate=verified.select())
        assert (
            next_prepared.preview.reviewed.config_source
            == prepared.preview.reviewed.config_source
        )
        assert lab.config.registry() == registry
        # Editing the branch does not change an already verified candidate.
        author.params["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(
            5.0, "GHz"
        )
        advanced = author.params.save(note="Independent manual edit")
        retained = next_prepared.run().wait(timeout=60).result()
        assert retained.config == check_run.config
        assert (
            retained.snapshot.config_source == prepared.preview.reviewed.config_source
        )

        assert lab.config.registry() == registry
        assert lab.setup.active() == setup
        assert lab.parameters.get(baseline.id) == baseline
        assert lab.parameters.checkout("daily").head.generation == branch.generation + 1
        assert lab.parameters.checkout("daily").head.revision == advanced.ref


def test_drag_candidate_retains_science_without_default_publication(
    candidate_daemon: ReferenceLabDaemon,
) -> None:
    with create_application(candidate_daemon.root).connect(candidate_daemon.url) as lab:
        config = bootstrap_config()
        parameters = lab.parameters.save(
            name="drag-baseline",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
        branch = lab.parameters.create_branch("drag/daily", revision=parameters)
        registry = lab.config.registry()
        assert registry.entries == () and registry.activation is None
        setup = lab.setup.active()
        resolved = lab.parameters.resolve(parameters, setup=setup.revision)
        invocation = drag_beta_experiment.build()
        baseline = lab.run(invocation, config=resolved)
        assert baseline.status == "completed"
        assert len(baseline.measurements().records) == 15
        analysis = baseline.analyze(drag_beta_analysis())
        [proposal] = analysis.parameter_proposals
        assert proposal.evidence_output_ids == ("quadratic-fit", "observations")
        assert analysis.artifact("fit-report").entry.filename == "drag-beta-fit.md"
        figure = analysis.figure("observations-by-amplification")
        assert [layer.id for layer in figure.layers] == ["measured", "fit"]
        assert [layer.preview.kind for layer in figure.layers] == ["scatter", "line"]
        assert figure.layers[0].total_points == 15
        assert figure.layers[1].total_points == 243
        uncertainty = figure.layers[1].projection.uncertainty
        assert uncertainty is not None
        assert "not a confidence interval" in uncertainty.meaning
        assert all(
            series.y_lower is not None for series in figure.layers[1].preview.series
        )

        candidate = analysis.candidate_config()
        check = lab.run(invocation, config=candidate)
        assert check.status == "completed"
        source = check.snapshot.config_source
        assert isinstance(source, AnalysisCandidateRunConfigSource)
        assert source.analysis_record_id == analysis.id
        assert source.proposal_id == candidate.proposal_id
        verification = lab.analyze(
            drag_beta_candidate_verification(baseline_run=baseline, candidate_run=check)
        )
        decision = verification.fact_as("decision", DRAG_BETA_VERIFICATION_SCHEMA)
        assert decision.accepted and decision.improvement >= 0.001
        assert verification.view.analysis.subject.kind == "project"
        assert [(item.id, item.role) for item in verification.inputs] == [
            ("baseline", "baseline"),
            ("candidate", "candidate"),
        ]
        assert verification.artifact("verification-report").entry.filename == (
            "drag-beta-verification.md"
        )
        assert all(
            entry.id != verification.id
            for run in (baseline, check)
            for entry in run.contents(role="record").items
        )
        assert lab.config.registry() == registry
        assert lab.setup.active() == setup
        assert lab.parameters.checkout("drag/daily").head == branch
        assert lab.parameters.get(parameters.id) == parameters
