"""Offline groups retain failures, recovery and readable historical evidence."""

import shutil
from pathlib import Path

import pytest
import scopecat as sc
from scopecat.application.author_project import AuthorPreparedLaunch
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.parameters import QubitParameters
from reference_lab.workflows.authored.ordinary_analysis import PeakResult
from reference_lab.workflows.authored.signal import signal as signal_declaration


def test_grouped_analysis_recovery_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "groups"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    signal = root / "src/reference_lab/workflows/authored/signal.py"
    signal.write_text(
        signal.read_text().replace(
            'sc.ControlSpec(title="Gain")',
            'sc.ControlSpec(title="Gain", scannable=True)',
        )
    )
    source = root / "src/reference_lab/workflows/authored/ordinary_analysis.py"
    source.write_text(
        source.read_text()
        + """

@sc.analysis_function
def group_peak(data: Dataset) -> sc.AnalysisProducts[PeakResult]:
    if float(data["gain"].require_values()[0]) == 0:
        raise ValueError("zero gain rejected by draft policy")
    return estimate_peak.function(data)
"""
    )
    project = load_project(root / "scopecat.toml")
    start_project(project)
    analysis = "reference_lab.workflows.authored.ordinary_analysis:group_peak"
    try:
        with project.authoring() as author:
            author.refresh()
            declaration = signal_declaration
            run = (
                author.prepare(
                    "signal", scans={"frequency": [4.7, 4.8, 4.9], "gain": [0, 1]}
                )
                .run()
                .wait(timeout=60)
                .result()
            )
            draft = declaration().sweep_parameter(
                QubitParameters.drive_carrier_frequency,
                "q0",
                [4.8, 4.9, 5.0],
                name="center",
            )
            prepared = author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]})
            request = prepared.request.model_copy(
                update={
                    "scan_mode": "paired",
                    "parameter_sweeps": draft.parameter_sweeps,
                }
            )
            paired = (
                AuthorPreparedLaunch(author, request, author.preview(request))
                .run()
                .wait(timeout=60)
                .result()
            )
            assert len(author.run(paired.id).measurements()) == 3
            assert author.run(paired.id).measurements()["center"].require_values() == (
                4.8,
                4.9,
                5.0,
            )
            first = author.analyze_groups_as(
                run.id, analysis, PeakResult, by=("gain",), fitting="frequency"
            )
            assert len(first.groups) == 2
            failed, success = first.groups
            assert failed.value is None
            assert "zero gain" in (failed.receipt.error or "")
            assert success.value is not None
            assert success.value.frequency == sc.Quantity(4.8, "GHz")
            assert len(success.receipt.point_indices) == 3
            assert success.publication.artifact("group_selection").text()
            assert (
                author.analyze_groups_as(
                    run.id, analysis, PeakResult, by=("gain",), fitting="frequency"
                ).publication.id
                == first.publication.id
            )
            source.write_text(
                source.read_text().replace(
                    'if float(data["gain"].require_values()[0]) == 0:',
                    "if False:",
                )
            )
            author.refresh()
            fixed = author.analyze_groups_as(
                run.id,
                analysis,
                PeakResult,
                by=("gain",),
                fitting="frequency",
                source="current",
            )
            assert all(group.receipt.error is None for group in fixed.groups)
            assert fixed.groups[0].value is not None
            assert fixed.groups[0].value.status == "no_response"
            assert fixed.publication.id != first.publication.id
            assert (
                author.read_groups_as(run.id, first.publication.id, PeakResult)
                .groups[0]
                .receipt.error
                == failed.receipt.error
            )
    finally:
        stop_project(project)
    start_project(project)
    try:
        with project.authoring() as author:
            restored = author.read_groups_as(run.id, first.publication.id, PeakResult)
            assert restored.groups[1].value == success.value
            assert restored.groups[0].receipt.error == failed.receipt.error
            assert (
                author.read_groups_as(run.id, fixed.publication.id, PeakResult)
                .groups[0]
                .value
                == fixed.groups[0].value
            )
    finally:
        stop_project(project)
