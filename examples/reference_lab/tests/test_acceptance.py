"""The committed shared fixture must remain a real Python/HTTP lab result."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter
from scopecat.application.launch import LaunchPreview
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.daemon.reviews import ReviewSessionView
from scopecat.daemon.views import (
    MeasurementPreview,
    ParameterProposalPage,
    RunResourceView,
)
from scopecat.inspection import PlannedInstrumentSetting
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.measurement import MeasurementDatasetSchema, MeasurementScalar

from reference_lab.acceptance import acceptance_json_matches
from reference_lab.configuration import EXAMPLE_ROOT

FIXTURE = EXAMPLE_ROOT / "fixtures" / "acceptance.json"


def test_shared_fixture_is_current_python_and_http_behavior() -> None:
    # Capture requires fresh configuration and virtual device state. Reuse the
    # generator's isolated project even after other session tests have run.
    root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    environment.pop(DAEMON_URL_ENV, None)
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local generator
        [
            sys.executable,
            str(root / "scripts/generate_reference_lab_acceptance.py"),
            "--check",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_shared_fixture_retains_diagnostic_review_and_entity_contracts() -> None:
    fixture = cast("dict[str, JsonValue]", json.loads(FIXTURE.read_text()))
    diagnostic = MeasurementPreview.model_validate(fixture["diagnostic"])
    [record] = diagnostic.items
    temperature = record.observables["temperature"]
    assert isinstance(temperature, MeasurementScalar)
    assert temperature.value == 0.02 and temperature.unit == "K"
    assert record.acquisition_evidence.events[0].instrument_id == "mixing-chamber"
    assert (
        ReviewSessionView.model_validate(fixture["inspection"]).latest_result
        is not None
    )
    [reviewed] = ParameterProposalPage.model_validate(
        fixture["reviewed_candidate"]
    ).items
    assert reviewed.proposal.id == "q1-channel-delay"
    assert reviewed.approval is not None
    assert reviewed.approval.actor == "acceptance-operator"
    schema = MeasurementDatasetSchema.model_validate(fixture["entity_analysis"])
    [entity_axis] = [
        dimension for dimension in schema.dimensions if dimension.kind == "entity"
    ]
    assert entity_axis.index is not None
    assert [entity.id for entity in entity_axis.index.values] == ["q0", "q1"]
    [waiting] = TypeAdapter(tuple[RunResourceView, ...]).validate_python(
        fixture["resource_waiting"]
    )
    assert waiting.status == "blocked" and waiting.blocked_by is not None
    assert (
        RunOutcome.model_validate(fixture["resource_cancelled"]).result == "cancelled"
    )


def test_shared_fixture_retains_planned_instrument_values() -> None:
    fixture = cast("dict[str, JsonValue]", json.loads(FIXTURE.read_text()))
    planned = cast("dict[str, JsonValue]", fixture["planned_settings"])
    settings = TypeAdapter(tuple[PlannedInstrumentSetting, ...]).validate_python(
        planned["planned_settings"]
    )
    [frequency] = [
        setting
        for setting in settings
        if setting.instrument_id == "drive-lo-a"
        and setting.setting.target.property_id == "frequency"
    ]
    assert frequency.setting.value.root == Quantity(4_850_000_000, "Hz")
    assert frequency.point_index == 0
    assert frequency.proposal_fingerprint.startswith("sha256:")
    assert planned["planned_settings_truncated"] is False


def test_shared_fixture_retains_complex_scalar_without_a_local_axis() -> None:
    fixture = cast("dict[str, JsonValue]", json.loads(FIXTURE.read_text()))
    preview = MeasurementPreview.model_validate(fixture["coherent_scalar"])
    assert preview.dataset_schema is not None
    [mean] = [
        variable
        for variable in preview.dataset_schema.variables
        if variable.dtype == "complex128"
    ]
    assert mean.dims == ("point",) and mean.unit == "ratio"
    assert len(preview.items) == 4
    values: list[complex] = []
    for record in preview.items:
        value = record.observables[mean.id]
        assert isinstance(value, MeasurementScalar) and isinstance(value.value, complex)
        values.append(value.value)
    assert any(value.imag != 0 for value in values)
    assert len(set(values)) > 1


def test_fixture_comparison_limits_roundoff_to_complex_iq_components() -> None:
    expected = FIXTURE.read_text()
    # Use the observed cross-platform CI differences that motivated this tolerance.
    for real, imag in (
        ("0.7500000000000002", "1.915134717478395e-15"),
        ("0.7499999999999999", "1.925543058334256e-15"),
    ):
        roundoff = expected.replace('"real": 0.75', f'"real": {real}', 1).replace(
            '"imag": 2.060851489460447e-15', f'"imag": {imag}', 1
        )
        assert roundoff != expected
        assert acceptance_json_matches(expected, roundoff)
    changed = expected.replace('"real": 0.75', '"real": 0.750001', 1)
    assert not acceptance_json_matches(expected, changed)
    changed_identity = expected.replace(
        '"logical_point_id": "', '"logical_point_id": "changed-', 1
    )
    assert not acceptance_json_matches(expected, changed_identity)
    changed_coordinate = expected.replace(
        '"value": 8.0', '"value": 8.000000000000002', 1
    )
    assert changed_coordinate != expected
    assert not acceptance_json_matches(expected, changed_coordinate)


def test_shared_fixture_retains_control_sources_and_normalized_units() -> None:
    fixture = cast("dict[str, JsonValue]", json.loads(FIXTURE.read_text()))
    scalar = LaunchPreview.model_validate(fixture["controls_scalar"])
    scanned = LaunchPreview.model_validate(fixture["controls_scan"])
    assert scalar.point_count == 1 and scanned.point_count == 6
    assert [value.state for value in scalar.controls] == [
        "fixed",
        "fixed",
        "configuration",
        "derived",
    ]
    assert scalar.controls[0].value == Quantity(4.9, "GHz")
    assert scalar.controls[1].value == Quantity(0.1, "V")
    assert scalar.controls[2].value == Quantity(4.8, "GHz")
    assert "qubits[q0]" in scalar.controls[2].provenance
    assert [value.state for value in scanned.controls[:2]] == ["scanned", "scanned"]
    for value in scanned.controls[:2]:
        assert value.value is None and value.axis is not None
        assert value.axis.mode == "scan"
