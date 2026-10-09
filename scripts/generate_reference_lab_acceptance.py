"""Capture production reference-lab responses in a temporary, hardware-free project."""

from __future__ import annotations

import argparse
import math
import shutil
from difflib import unified_diff
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from pydantic import JsonValue

from reference_lab.acceptance import (
    FIXTURE_WORKSPACE,
    acceptance_json,
    acceptance_json_matches,
    capture_acceptance_fixtures,
    checked_launch_preview,
)
from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from scopecat.api.lab import LabClient
from scopecat.application.controls import edit_controls
from scopecat.application.launch import LaunchCatalog, LaunchPreview
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import MeasurementTracePreviewQuery
from scopecat.kernel.quantity import Quantity
from scopecat.project import load_project
from scopecat.records.control_edit import ControlEdit
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.measurement import MeasurementScalar, MeasurementUnavailable
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat_server.lifecycle import start_project, stop_project

OUTPUT = EXAMPLE_ROOT / "fixtures" / "acceptance.json"


def _check_independent_readout(lab: LabClient, client: DaemonClient) -> None:
    """Check the captured worker result without another gallery acquisition."""
    [candidate_source] = [
        run.snapshot.config_source
        for run in client.list_runs().items
        if run.snapshot.config_source is not None
        and run.snapshot.config_source.kind == "analysis_candidate"
    ]
    assert candidate_source.kind == "analysis_candidate"
    source = lab.get_run(candidate_source.source_run_id)
    # Reuse this real worker acquisition for the retired unavailable-channel
    # gallery contract. No second run or presentation summary is needed.
    assert source.status == "completed"
    data = source.measurements()
    schema = data.schema
    iq_ref = "iq_shots"
    iq = data[iq_ref]
    [entity_axis] = [
        dimension
        for dimension in schema.dimensions
        if dimension.kind == "entity" and dimension.id in iq.dims
    ]
    assert entity_axis.index is not None
    entities = entity_axis.index.values
    assert [entity.id for entity in entities] == ["q0", "q1"]
    assert len(data) == 2 and iq.shape == (2, 2, 64)
    products = iq.definition.source_entity_products
    assert products is not None
    assert len(products.product_ids) == len(entities)
    for entity, product_id in zip(entities, products.product_ids, strict=True):
        assert product_id is not None
        assert product_id.endswith(f"{entity.id}_iq_shots")
        selected = data.sel({entity_axis.id: entity})[iq_ref]
        failures = [
            value.reason if isinstance(value, MeasurementUnavailable) else None
            for value in selected.raw_values
        ]
        assert failures == ([None, None] if entity.id == "q0" else [None, "missing"])
    acquisition = iq.definition.entity_acquisition
    assert acquisition is not None and acquisition.policy == "independent"
    trace = client.measurement_trace_preview(
        source.id,
        MeasurementTracePreviewQuery(
            observable_id=iq.id,
            entity_indices=(0, 1),
            max_series=4,
            max_samples=256,
        ),
    )
    assert [series.label for series in trace.series] == [
        "Delay 88 ns · q0",
        "Delay 88 ns · q1",
        "Delay 128 ns · q0",
    ]
    assert [failure.label for failure in trace.failures] == ["Delay 128 ns · q1"]


def _capture_author_controls() -> dict[str, JsonValue]:
    fixture = Path(__file__).resolve().parents[1] / "testing/fixtures/retained-signal"
    with TemporaryDirectory(prefix="scopecat-controls-") as temporary:
        root = Path(temporary)
        shutil.copytree(fixture / "src", root / "src")
        shutil.copy2(fixture / "scopecat.toml", root / "scopecat.toml")
        project = load_project(root / "scopecat.toml")
        endpoint = start_project(project)
        try:
            with (
                project.connect() as lab,
                project.authoring() as authors,
                DaemonClient(endpoint.base_url) as client,
            ):
                from ui_signal.application import initial_parameters
                from ui_signal.signal import CONTROLS, signal

                content = initial_parameters()
                parameters = lab.parameters.save(
                    name="acceptance-controls",
                    catalog=content.catalog,
                    parameters=content.parameters,
                )
                setup = lab.setup.get("initial")
                resolved = lab.parameters.resolve(parameters, setup=setup)
                config = resolved.config
                selection = ScientificSelection(
                    configuration=ParameterConfiguration(
                        ref=parameters.ref, setup=setup.ref
                    )
                )
                workspace_id = authors.workspace_id
                current = authors.catalog()
                catalog = LaunchCatalog(
                    workspace_id=FIXTURE_WORKSPACE,
                    entries=tuple(
                        entry.model_copy(update={"version": "sha256:" + "0" * 64})
                        for entry in current.entries
                        if entry.id == "ui_signal.signal"
                    ),
                )
                scalar_request = LaunchRequest(
                    workspace_id=workspace_id,
                    action="preview",
                    experiment="ui_signal.signal",
                    version="1",
                    selection=selection,
                    control_edits={
                        "frequency": ControlEdit.model_validate(
                            {"mode": "fixed", "value": {"value": 4900.0, "unit": "MHz"}}
                        ),
                        "amplitude": ControlEdit.model_validate(
                            {"mode": "fixed", "value": {"value": 100.0, "unit": "mV"}}
                        ),
                    },
                )
                controls_scalar = checked_launch_preview(client, scalar_request)
                assert (
                    isinstance(controls_scalar, LaunchPreview)
                    and controls_scalar.point_count == 1
                )
                scan_request = scalar_request.model_copy(
                    update={
                        "control_edits": {
                            "frequency": ControlEdit.model_validate(
                                {
                                    "mode": "scan",
                                    "axis": {
                                        "kind": "range",
                                        "start": {"value": 4700.0, "unit": "MHz"},
                                        "stop": {"value": 4900.0, "unit": "MHz"},
                                        "points": 3,
                                    },
                                }
                            ),
                            "amplitude": ControlEdit.model_validate(
                                {
                                    "mode": "scan",
                                    "axis": {
                                        "kind": "values",
                                        "values": [
                                            {"value": 50.0, "unit": "mV"},
                                            {"value": 100.0, "unit": "mV"},
                                        ],
                                    },
                                }
                            ),
                        }
                    }
                )
                controls_scan = checked_launch_preview(client, scan_request)
                assert (
                    isinstance(controls_scan, LaunchPreview)
                    and controls_scan.point_count == 6
                )
                controlled = edit_controls(
                    CONTROLS,
                    signal.build(),
                    config=config,
                    edits=scan_request.control_edits,
                )
                controlled_run = lab.run(controlled, config=resolved)
                assert controlled_run.status == "completed"
                controlled_records = controlled_run.measurements().records
                assert len(controlled_records) == 6
                reference_value = next(
                    value.value
                    for value in controls_scan.controls
                    if value.id == "reference_frequency"
                )
                assert isinstance(reference_value, Quantity)
                for record in controlled_records:
                    frequency = record.coordinates["frequency"]
                    amplitude = record.coordinates["amplitude"]
                    response = record.observables["result"]
                    assert (
                        isinstance(frequency, MeasurementScalar)
                        and frequency.unit == "GHz"
                    )
                    assert (
                        isinstance(amplitude, MeasurementScalar)
                        and amplitude.unit == "V"
                    )
                    assert (
                        isinstance(response, MeasurementScalar) and response.unit == "V"
                    )
                    assert isinstance(frequency.value, float) and isinstance(
                        amplitude.value, float
                    )
                    assert isinstance(response.value, float)
                    expected = amplitude.value * math.cos(
                        2 * math.pi * (frequency.value - reference_value.value)
                    )
                    assert math.isclose(
                        response.value, expected, rel_tol=1e-12, abs_tol=1e-12
                    )
                return {
                    "controls_catalog": catalog.model_dump(mode="json"),
                    "controls_scalar": controls_scalar.model_dump(mode="json"),
                    "controls_scan": controls_scan.model_dump(mode="json"),
                }
        finally:
            stop_project(project)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scopecat-acceptance-") as temporary:
        root = Path(temporary)
        shutil.copytree(EXAMPLE_ROOT / "config", root / "config")
        shutil.copytree(EXAMPLE_ROOT / "src", root / "src")
        shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
        project = load_project(root / "scopecat.toml")
        endpoint = start_project(project)
        try:
            with (
                create_application(root).connect(endpoint.base_url) as lab,
                DaemonClient(endpoint.base_url) as client,
            ):
                assert lab.config.registry().entries == ()
                fixtures = capture_acceptance_fixtures(lab, client)
                _check_independent_readout(lab, client)
                assert lab.config.registry().entries == ()
        finally:
            stop_project(project)
    fixtures.update(_capture_author_controls())
    content = acceptance_json(fixtures)
    if cast("bool", args.check):
        expected = OUTPUT.read_text() if OUTPUT.is_file() else ""
        if not expected or not acceptance_json_matches(expected, content):
            differences = unified_diff(
                expected.splitlines(),
                content.splitlines(),
                fromfile="committed acceptance.json",
                tofile="fresh acceptance.json",
                n=2,
                lineterm="",
            )
            # Keep CI diagnostics bounded without rewriting captured scientific data.
            for line in islice(differences, 80):
                print(line[:300])
            raise SystemExit(
                "Reference-lab acceptance fixture is stale; run "
                "uv run python scripts/generate_reference_lab_acceptance.py"
            )
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(content)


if __name__ == "__main__":
    main()
