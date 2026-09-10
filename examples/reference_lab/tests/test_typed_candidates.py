"""Receipt cells → independent retained policy → explicit publish and restore."""

import shutil
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
import scopecat as sc
from scopecat.analysis.facts import ordinary_result_schema
from scopecat.application import LabApplication
from scopecat.automation.wire import ProcedureRunListQuery
from scopecat.daemon.client import DaemonConflictError
from scopecat.project import load_project
from scopecat.records.parameter import TableParameterValue
from scopecat.records.run import AnalysisCandidateRunConfigSource
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.exploration import exploration_config
from reference_lab.workflows.authored.ordinary_analysis import (
    PeakResult,
    PeakVerification,
)
from reference_lab.workflows.exploratory_signal import exploratory_signal


def test_typed_candidates_retain_cells_and_independent_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "candidate-project"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    analysis_module = "reference_lab.workflows.authored.ordinary_analysis"
    try:
        with (
            LabApplication().connect(endpoint.base_url) as lab,
            project.authoring() as author,
        ):
            author.config.set_default(
                exploration_config(sc.Quantity(4.83, "GHz")), entry_id="baseline"
            )
            baseline_default = author.config.active()
            sample = lab.samples.create(
                "candidate-sample",
                kind="synthetic",
                content=SampleRevisionDraft(display_name="Candidate sample"),
            )
            author.refresh()
            run = (
                author.prepare(
                    "signal",
                    sample=sample.id,
                    scans={"frequency": [4.7, 4.8, 4.9, 5.0]},
                )
                .run()
                .wait(timeout=60)
                .result()
            )
            fit = author.analyze_as(
                run.id, f"{analysis_module}:estimate_peak", PeakResult
            )
            assert fit.value.frequency is not None
            # The materialized dataclass is never authority, even if mutated locally.
            edited = replace(
                fit, value=replace(fit.value, frequency=sc.Quantity(5.9, "GHz"))
            )
            candidate = author.config.stage(
                edited,
                name="carrier",
                table="qubits",
                key="q0",
                fields={"drive_carrier_frequency": "frequency"},
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
            assert author.config.active() == baseline_default
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
                arguments={"expected_frequency_ghz": 4.8, "tolerance_ghz": 0.05},
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
            rejected = author.analyze_as(
                check_run.id,
                f"{analysis_module}:verify_peak",
                PeakVerification,
                arguments={"expected_frequency_ghz": 5.8, "tolerance_ghz": 0.01},
            )
            with pytest.raises(ValueError, match="verification rejected"):
                candidate.verify(
                    replace(rejected, value=replace(rejected.value, accepted=True))
                )
            wrong_point_run = lab.run(
                exploratory_signal(),
                config=candidate.config,
                sample=SampleSelector(
                    sample_id=sample.id, context_id="different-point"
                ),
            )
            wrong_point = author.analyze_as(
                wrong_point_run.id,
                f"{analysis_module}:verify_peak",
                PeakVerification,
                source="current",
                arguments={"expected_frequency_ghz": 4.8, "tolerance_ghz": 0.05},
            )
            with pytest.raises(ValueError, match="sample revision/workpoint"):
                candidate.verify(wrong_point)
            # The underlying verified-acceptance endpoint enforces the same scope.
            unsafe = lab.analysis("wrong point", key="wrong-point")
            unsafe.measurements(run, id="baseline")
            unsafe.measurements(wrong_point_run, id="candidate")
            unsafe_decision = (
                unsafe.result()
                .fact(
                    "decision",
                    wrong_point.value,
                    schema=ordinary_result_schema(PeakVerification),
                )
                .save()
            )
            with pytest.raises(
                DaemonConflictError, match="same sample revision/workpoint"
            ):
                author.config.accept_verified(
                    candidate.config,
                    verified_by=(unsafe_decision, "decision"),
                    entry_id="wrong-point",
                )
            verified = candidate.verify(check)
            next_prepared = author.prepare("signal", candidate=verified.select())
            assert next_prepared.preview.config_source == prepared.preview.config_source
            assert author.config.active() == baseline_default
            verified.publish_default(name="verified-carrier")
            assert author.config.active().entry.id == "verified-carrier"
            runs_before_stale = author.list_runs()
            procedures_before_stale = author.list_procedures(ProcedureRunListQuery())
            with pytest.raises(httpx2.HTTPStatusError) as stale_preview:
                next_prepared.run()
            assert (
                "active configuration changed since preview"
                in stale_preview.value.response.text
            )
            assert author.list_runs() == runs_before_stale
            assert (
                author.list_procedures(ProcedureRunListQuery())
                == procedures_before_stale
            )
            reviewed_again = author.prepare("signal", candidate=verified.select())
            reviewed_source = reviewed_again.preview.config_source
            assert isinstance(reviewed_source, AnalysisCandidateRunConfigSource)
            assert (
                reviewed_source.registry_generation
                == author.config.active().activation.generation
            )
            reviewed_again.run().wait(timeout=60).result()

            author.config.undo()
            assert author.config.active().entry.id == baseline_default.entry.id
            author.config.set_default(
                exploration_config(sc.Quantity(5.0, "GHz")), entry_id="new-baseline"
            )
            with pytest.raises(ValueError, match=r"drive_carrier_frequency.*stale"):
                verified.publish_default(name="stale-carrier")
    finally:
        stop_project(project)
