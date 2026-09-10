"""Ordinary functions publish real retained evidence and survive source changes."""

import shutil
from pathlib import Path

import pytest
import scopecat as sc
from scopecat.application import LabApplication
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.everyday_author import acquire_everyday_author_inputs
from reference_lab.workflows.authored.ordinary_analysis import PeakResult, estimate_peak


def test_ordinary_analysis_retained_source_arguments_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "ordinary"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    name = "reference_lab.workflows.authored.ordinary_analysis:estimate_peak"
    try:
        with (
            LabApplication().connect(endpoint.base_url) as lab,
            project.authoring() as authors,
        ):
            acquired = acquire_everyday_author_inputs(lab)
            authors.refresh()
            managed = (
                authors.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]})
                .run()
                .wait(timeout=60)
                .result()
            )
            original = authors.analyze_as(managed.id, name, PeakResult)
            assert original.value.status == "estimated"
            with pytest.raises(ValueError, match="source='current'"):
                authors.analyze_as(acquired.peaked, name, PeakResult)
            first = authors.analyze_as(
                acquired.peaked, name, PeakResult, source="current"
            )
            assert first.value.status == "estimated"
            assert first.value.frequency == sc.Quantity(4.8, "GHz")
            assert first.publication.fact("result").value is not None
            assert first.publication.figure("figure").layers
            assert first.publication.dataset("curve").schema.fields[0].unit == "GHz"
            assert first.publication.executions[0].input_bindings[1].value == 0.2
            flat = authors.analyze_as(acquired.flat, name, PeakResult, source="current")
            assert flat.value.status == "no_response" and flat.value.frequency is None
            changed = authors.analyze_as(
                acquired.peaked,
                name,
                PeakResult,
                source="current",
                arguments={"minimum_contrast": 2.0},
            )
            assert changed.value.frequency is None
            assert changed.publication.id != first.publication.id
            assert changed.publication.executions[0].input_bindings[1].value == 2.0
            original_revision = authors.state().active
            assert original_revision is not None
            source_path = (
                root / "src/reference_lab/workflows/authored/ordinary_analysis.py"
            )
            source_path.write_text(
                source_path.read_text().replace(
                    "minimum_contrast: float = 0.2", "minimum_contrast: float = 2.0"
                )
            )
            authors.refresh()
            edited = authors.analyze_as(
                acquired.peaked, name, PeakResult, source="current"
            )
            assert edited.value.frequency is None
            assert (
                authors.analyze_as(managed.id, name, PeakResult).value == original.value
            )
            assert (
                authors.analyze_as(
                    managed.id, name, PeakResult, source="current"
                ).value.frequency
                is None
            )
            assert edited.publication.fact(
                "author_code_revision"
            ) != first.publication.fact("author_code_revision")
            old = authors.analyze(
                acquired.peaked, name, code_revision=original_revision
            )
            assert lab.get_run(acquired.peaked).published_analysis(
                old.analysis_id
            ).fact("result") == first.publication.fact("result")
            materialized = authors.run(acquired.peaked).measurements().materialize()
            publication_id = first.publication.id
            curve_schema = first.publication.dataset("curve").schema
    finally:
        stop_project(project)
    assert first.value.frequency == sc.Quantity(4.8, "GHz")
    assert len(materialized.project().to_xarray().coords["point"]) == 5
    with pytest.raises(ValueError, match="at least 3"):
        estimate_peak.function(materialized.isel({"point": [0]}))
    with pytest.raises((ValueError, TypeError), match=r"unit|convert|dimension"):
        materialized.project(
            {"frequency": "frequency"}, units={"frequency": "ns"}
        ).to_xarray()
    endpoint = start_project(project)
    try:
        with LabApplication().connect(endpoint.base_url) as lab:
            publication = lab.get_run(acquired.peaked).published_analysis(
                publication_id
            )
            assert publication.publication_hash == first.publication.publication_hash
            assert publication.fact("result") == first.publication.fact("result")
            assert publication.result_as(PeakResult).value == first.value
            assert publication.dataset("curve").schema == curve_schema
    finally:
        stop_project(project)
