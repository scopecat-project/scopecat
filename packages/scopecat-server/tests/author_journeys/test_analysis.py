"""Ordinary functions publish real retained evidence and survive source changes."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import httpx2
import pytest
import scopecat as sc
from scopecat.application import LabApplication
from scopecat.project import load_project
from ui_signal.analysis import (
    PeakResult,
    PeakVerification,
    estimate_peak,
)
from ui_signal.application import initial_parameters
from ui_signal.ordinary import signal

from scopecat_server.lifecycle import start_project, stop_project

from .conftest import FIXTURE_ROOT


def test_ordinary_analysis_retained_source_arguments_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    select_author_source: Callable[[Path], None],
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "ordinary"
    root.mkdir()
    shutil.copytree(FIXTURE_ROOT / "src", root / "src")
    shutil.copy2(FIXTURE_ROOT / "scopecat.toml", root / "scopecat.toml")
    select_author_source(root)
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    name = "ui_signal.analysis:estimate_peak"
    try:
        with (
            LabApplication().connect(endpoint.base_url) as lab,
            project.authoring() as authors,
        ):
            content = initial_parameters()
            revision = authors.parameters.save(
                name="analysis-inputs",
                catalog=content.catalog,
                parameters=content.parameters,
            )
            authors.parameters.create_branch("analysis-inputs", revision=revision)
            authors.use(
                parameter_branch="analysis-inputs",
                setup=lab.setup.get("initial").ref,
            )
            # Deliberately unmanaged inputs exercise explicit analysis source selection.
            resolved = lab.parameters.resolve(revision, setup=lab.setup.get("initial"))

            def acquire(gain: float) -> str:
                return lab.run(
                    signal.build(gain=gain).with_axis(
                        sc.axis(
                            next(
                                control
                                for control in signal.controls.fields
                                if control.id == "frequency"
                            ).ref,
                            [sc.Quantity(v, "GHz") for v in (4.7, 4.8, 4.9, 5.0, 5.1)],
                        )
                    ),
                    config=resolved,
                ).id

            peaked, flat_run = acquire(1.0), acquire(0.0)
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
                authors.analyze_as(peaked, name, PeakResult)
            first = authors.analyze_as(peaked, name, PeakResult, source="current")
            assert first.value.status == "estimated"
            assert first.value.frequency == sc.Quantity(4.8, "GHz")
            assert first.publication.fact("result").value is not None
            assert first.publication.figure("figure").layers
            assert first.publication.dataset("curve").schema.fields[0].unit == "GHz"
            assert first.publication.executions[0].input_bindings[1].value == 0.2
            flat = authors.analyze_as(flat_run, name, PeakResult, source="current")
            assert flat.value.status == "no_response" and flat.value.frequency is None
            changed = authors.analyze_as(
                peaked,
                name,
                PeakResult,
                source="current",
                arguments={"minimum_contrast": 2.0},
            )
            assert changed.value.frequency is None
            assert changed.publication.id != first.publication.id
            assert changed.publication.executions[0].input_bindings[1].value == 2.0
            verify_name = name.replace("estimate_peak", "verify_peak")
            verified = authors.analyze_as(
                peaked,
                verify_name,
                PeakVerification,
                source="current",
                arguments={
                    "expected_frequency": first.value.frequency,
                    "tolerance": sc.Quantity(50, "MHz"),
                },
            )
            assert verified.value.accepted
            assert verified.value.tolerance == sc.Quantity(50, "MHz")
            context_name = name.replace("estimate_peak", "verify_with_context")
            context_verified = authors.analyze_as(
                peaked,
                context_name,
                PeakVerification,
                source="current",
                arguments={"expected_frequency": first.value.frequency},
            )
            assert context_verified.value == verified.value
            assert json.loads(
                context_verified.publication.artifact(
                    "author_analysis_effective_arguments"
                ).text()
            ) == {
                "expected_frequency": {"value": 4.8, "unit": "GHz"},
                "tolerance": {"value": 50, "unit": "MHz"},
            }
            with pytest.raises(httpx2.HTTPStatusError) as invalid_context:
                authors.analyze_as(
                    peaked,
                    context_name,
                    PeakVerification,
                    source="current",
                    arguments={"expected_frequency": "bad"},
                )
            assert "expected_frequency" in str(invalid_context.value.__notes__)
            assert (
                authors.analyze_as(
                    peaked,
                    verify_name,
                    PeakVerification,
                    source="current",
                    arguments={
                        "expected_frequency": first.value.frequency,
                        "tolerance": sc.Quantity(50, "MHz"),
                    },
                ).publication.id
                == verified.publication.id
            )
            other_units = authors.analyze_as(
                peaked,
                verify_name,
                PeakVerification,
                source="current",
                arguments={
                    "expected_frequency": first.value.frequency,
                    "tolerance": sc.Quantity(0.05, "GHz"),
                },
            )
            assert other_units.publication.id != verified.publication.id
            with pytest.raises(httpx2.HTTPStatusError) as invalid:
                authors.analyze_as(
                    peaked,
                    verify_name,
                    PeakVerification,
                    source="current",
                    arguments={"expected_frequency": "bad", "tolerance": 0.05},
                )
            assert "expected_frequency" in str(invalid.value.__notes__)
            original_revision = authors.state().active
            assert original_revision is not None
            source_path = root / "src/ui_signal/analysis.py"
            source_path.write_text(
                source_path.read_text()
                .replace(
                    "minimum_contrast: float = 0.2", "minimum_contrast: float = 2.0"
                )
                .replace('sc.Quantity(50, "MHz")', 'sc.Quantity(25, "MHz")')
            )
            authors.refresh()
            original_typed = authors.analyze_as(
                managed.id,
                verify_name,
                PeakVerification,
                arguments={"expected_frequency": sc.Quantity(4.8, "GHz")},
            )
            current_typed = authors.analyze_as(
                managed.id,
                verify_name,
                PeakVerification,
                source="current",
                arguments={"expected_frequency": sc.Quantity(4.8, "GHz")},
            )
            assert original_typed.value.tolerance == sc.Quantity(50, "MHz")
            assert current_typed.value.tolerance == sc.Quantity(25, "MHz")
            selected_sources: tuple[Literal["original", "current"], ...] = (
                "original",
                "current",
            )
            for selected_source in selected_sources:
                context_result = authors.analyze_as(
                    managed.id,
                    context_name,
                    PeakVerification,
                    source=selected_source,
                    arguments={"expected_frequency": sc.Quantity(4.8, "GHz")},
                )
                assert context_result.value.tolerance == sc.Quantity(
                    50 if selected_source == "original" else 25, "MHz"
                )
            edited = authors.analyze_as(peaked, name, PeakResult, source="current")
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
            old = authors.analyze(peaked, name, code_revision=original_revision)
            assert lab.get_run(peaked).published_analysis(old.analysis_id).fact(
                "result"
            ) == first.publication.fact("result")
            materialized = authors.run(peaked).measurements().materialize()
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
            publication = lab.get_run(peaked).published_analysis(publication_id)
            assert publication.publication_hash == first.publication.publication_hash
            assert publication.fact("result") == first.publication.fact("result")
            assert publication.result_as(PeakResult).value == first.value
            assert publication.dataset("curve").schema == curve_schema
            restored = (
                lab.get_run(peaked)
                .published_analysis(verified.publication.id)
                .result_as(PeakVerification)
            )
            assert restored.value == verified.value
            assert (
                lab.get_run(peaked)
                .published_analysis(context_verified.publication.id)
                .result_as(PeakVerification)
                .value
                == context_verified.value
            )
    finally:
        stop_project(project)
