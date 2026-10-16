"""Receipt cells and independent verification without publishing shared defaults."""

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
import scopecat as sc
from scopecat.analysis.facts import ordinary_result_schema
from scopecat.application import LabApplication
from scopecat.project import load_project
from scopecat.records.parameter import TableParameterValue
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
)
from scopecat.records.sample import SampleRevisionDraft
from ui_signal.analysis import PeakResult, PeakVerification
from ui_signal.application import initial_parameters
from ui_signal.signal import SignalParameters

from scopecat_server.lifecycle import start_project, stop_project
from scopecat_server.storage.sqlite.upgrade_115 import upgrade_115

from .conftest import FIXTURE_ROOT


def test_typed_candidates_retain_cells_and_independent_policy(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    select_author_source: Callable[[Path], None],
) -> None:
    root = tmp_path / "candidate"
    shutil.copytree(FIXTURE_ROOT, root)
    select_author_source(root)
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    request.addfinalizer(lambda: stop_project(project))
    analysis_module = "ui_signal.analysis"
    with (
        LabApplication().connect(endpoint.base_url) as lab,
        project.authoring() as author,
    ):
        config = initial_parameters(center=4.83)
        baseline = lab.parameters.save(
            name="baseline",
            catalog=config.catalog,
            parameters=config.parameters,
        )
        branch = lab.parameters.create_branch("daily", revision=baseline)
        registry = lab.config.registry()
        assert registry.entries == ()
        setup = lab.setup.get("initial")
        sample = lab.samples.create(
            "candidate-sample",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Candidate sample"),
        )
        author.use(sample=sample.id, parameter_branch="daily", setup=setup)
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
            table=SignalParameters,
            key="signal",
            fields={SignalParameters.center: "frequency"},
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
                table="signal",
                key="signal",
                fields={"center": "frequency"},
            )
        claimed = (
            run.analysis("claimed managed estimate")
            .result()
            .fact(
                "result",
                replace(fit.value, frequency=sc.Quantity(5.9, "GHz")),
                schema=ordinary_result_schema(PeakResult),
            )
            .fact(
                "author_code_revision",
                fit.publication.fact("author_code_revision").value,
            )
            .save()
            .result_as(PeakResult)
        )
        with pytest.raises(ValueError, match="no managed author source receipt"):
            author.config.stage(
                claimed,
                name="claimed-manual",
                table="signal",
                key="signal",
                fields={"center": "frequency"},
            )
        with httpx2.Client(base_url=endpoint.base_url) as client:
            path = f"/api/v1/runs/{run.id}/analyses/{fit.publication.id}/managed-source"
            assert (
                client.get(path, params={"publication_hash": "wrong"}).json() is False
            )
        original = run.config.parameter_snapshot.get("signal")
        resolved = author.config.resolve(candidate.config).parameter_snapshot.get(
            "signal"
        )
        assert isinstance(original, TableParameterValue) and isinstance(
            resolved, TableParameterValue
        )
        for before, after in zip(original.rows, resolved.rows, strict=True):
            for field, value in before.items():
                if field != "center":
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
                table="signal",
                key="signal",
                fields={"center": "frequency"},
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
            table="signal",
            key="signal",
            fields={"center": "frequency"},
        )
        with pytest.raises(ValueError, match="exact candidate"):
            other.verify(check)
        manual_check = (
            check_run.analysis("claimed managed verification")
            .result()
            .fact(
                "result", check.value, schema=ordinary_result_schema(PeakVerification)
            )
            .fact(
                "author_code_revision",
                check.publication.fact("author_code_revision").value,
            )
            .save()
            .result_as(PeakVerification)
        )
        with pytest.raises(ValueError, match="no managed author source receipt"):
            candidate.verify(manual_check)
        rejected = author.analyze_as(
            check_run.id,
            f"{analysis_module}:verify_peak",
            PeakVerification,
            arguments={
                "expected_frequency": sc.Quantity(5.9, "GHz"),
                "tolerance": sc.Quantity(0.01, "GHz"),
            },
        )
        with pytest.raises(ValueError, match="verification rejected"):
            candidate.verify(rejected)
        verified = candidate.verify(check)
        # Discovery applies the same exact-candidate gate as publication.
        with httpx2.Client(base_url=endpoint.base_url) as client:
            path = f"/api/v1/runs/{run.id}/parameter-proposals"
            eligible = client.get(f"{path}/{candidate.name}/verifications").json()
            assert any(
                item["reference"]["analysis_record_id"] == verified.verification.id
                and item["unavailable_reason"] is None
                for item in eligible["items"]
            )
            wrong = client.get(f"{path}/{other.name}/verifications").json()
            assert all(item["unavailable_reason"] for item in wrong["items"])
            assert (
                client.get(
                    f"{path}/{candidate.name}/verifications", params={"limit": 0}
                ).status_code
                == 422
            )
        assert lab.parameters.checkout("daily").head == branch
        next_prepared = author.prepare("signal", candidate=verified.select())
        assert (
            next_prepared.preview.reviewed.config_source
            == prepared.preview.reviewed.config_source
        )
        assert lab.config.registry() == registry
        publication_branch = lab.parameters.create_branch("accepted", revision=baseline)
        published = verified.publish_to_branch(
            publication_branch,
            name="verified-carrier",
            note="Independent policy passed",
        )
        assert (
            verified.publish_to_branch(
                publication_branch,
                name="verified-carrier",
                note="Independent policy passed",
            )
            == published
        )
        assert published.publication is not None
        assert (
            published.publication.verification.analysis_record_id
            == verified.verification.id
        )
        assert (
            lab.parameters.get(published.revision.revision_id).parameters
            == check_run.config.parameter_snapshot
        )
        # Editing the branch does not change an already verified candidate.
        author.params["signal"]["signal"]["center"] = sc.Quantity(5.0, "GHz")
        advanced = author.params.save(note="Independent manual edit")
        retained = next_prepared.run().wait(timeout=60).result()
        assert retained.config == check_run.config
        assert (
            retained.snapshot.config_source == prepared.preview.reviewed.config_source
        )

        assert lab.config.registry() == registry
        assert lab.setup.get("initial") == setup
        assert lab.parameters.get(baseline.id) == baseline
        assert lab.parameters.checkout("daily").head.generation == branch.generation + 1
        assert lab.parameters.checkout("daily").head.revision == advanced.ref

    # Reconnect after a real daemon restart: bindings are durable, not session facts.
    stop_project(project)
    endpoint = start_project(project)
    with project.authoring() as author:
        retained = (
            author.run(run.id)
            .published_analysis(fit.publication.id)
            .result_as(PeakResult)
        )
        reopened = author.config.candidate(run.id, candidate.name)
        reopened.verify(
            author.run(check_run.id)
            .published_analysis(check.publication.id)
            .result_as(PeakVerification)
        )
        author.config.stage(
            retained,
            name="reopened",
            table="signal",
            key="signal",
            fields={"center": "frequency"},
        )
        for bad, owner, result_type in (
            (claimed, run, PeakResult),
            (manual_check, check_run, PeakVerification),
        ):
            reread = (
                author.run(owner.id)
                .published_analysis(bad.publication.id)
                .result_as(result_type)
            )
            assert reread.value == bad.value
            with pytest.raises(ValueError, match="no managed author source receipt"):
                reopened.verify(reread)

    # Model the exact old schema, where publications existed without managed bindings.
    stop_project(project)
    database = project.runtime_binding.data_root / "control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE managed_analysis_publications")
        connection.execute("UPDATE project_schema SET version=115")
    assert upgrade_115(database, tmp_path / "upgrade-backup") is not None
    start_project(project)
    with project.authoring() as author:
        old_fit = (
            author.run(run.id)
            .published_analysis(fit.publication.id)
            .result_as(PeakResult)
        )
        assert old_fit.value == fit.value
        with pytest.raises(ValueError, match="no managed author source receipt"):
            author.config.stage(
                old_fit,
                name="old",
                table="signal",
                key="signal",
                fields={"center": "frequency"},
            )
        repeated = author.analyze_as(
            run.id, f"{analysis_module}:estimate_peak", PeakResult
        )
        author.config.stage(
            repeated,
            name="reanalyzed",
            table="signal",
            key="signal",
            fields={"center": "frequency"},
        )
