"""Physical virtual-bench captures through the daemon and instrument worker."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import scopecat as sc
from scopecat.execution.evidence import INSTRUMENT_STATE_EVIDENCE_ID
from scopecat.project import load_project
from scopecat.records.execution import InstrumentStateEvidence
from scopecat.records.run import ParameterRunConfigSource
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT, initial_parameters
from reference_lab.workflows.awg_output_monitor import awg_output_monitor

from ._xy_drive_probe import WaveformEmission


def test_bench_capture_crosses_daemon_and_instrument_worker(tmp_path: Path) -> None:
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, tmp_path / name)
    shutil.copyfile(
        Path(__file__).with_name("_xy_drive_probe.py"),
        tmp_path / "src" / "_xy_drive_probe.py",
    )
    (tmp_path / "scopecat.toml").write_text(
        (EXAMPLE_ROOT / "scopecat.toml")
        .read_text(encoding="utf-8")
        .replace(
            "reference_lab.backend:create_backend", "_xy_drive_probe:create_backend"
        ),
        encoding="utf-8",
    )
    project = load_project(tmp_path / "scopecat.toml")
    application = create_application(tmp_path)
    endpoint = start_project(project)
    try:
        with application.connect(endpoint.base_url) as lab:
            setup = lab.setup.get("initial")
            content = initial_parameters()
            parameters = lab.parameters.save(
                name="bench-capture-inputs",
                catalog=content.catalog,
                parameters=content.parameters,
            )
            invocation = awg_output_monitor.build()
            name = "AWG CH1 pulse shape after bench recabling"
            description = "Scope CH1 <- drive AWG CH1; 50 ohm cable, external trigger."
            run = lab.run(
                invocation,
                config=lab.parameters.resolve(parameters, setup=setup),
                name=name,
                tags=("diagnostic", "awg-monitor"),
                description=description,
            )
            snapshot = run.snapshot
            assert run.status == "completed"
            assert run.request.display_name == name
            assert run.request.tags == ("diagnostic", "awg-monitor")
            assert run.request.description == description
            assert isinstance(snapshot.config_source, ParameterRunConfigSource)
            assert snapshot.config_source.parameters == parameters.ref
            assert snapshot.config_source.setup == setup.ref
            data = run.measurements()
            expected_samples = np.array(
                [
                    0,
                    0.02,
                    0.12,
                    0.45,
                    0.82,
                    1,
                    0.82,
                    0.45,
                    0.12,
                    0.02,
                    0,
                    -0.08,
                    -0.04,
                    0,
                    0,
                    0,
                ]
            )
            time_values = data[invocation.output.time].require_values()
            voltage_values = data[invocation.output.voltage].require_values()
            assert len(time_values) == len(voltage_values) == 1
            np.testing.assert_allclose(
                np.asarray(time_values[0], dtype=np.float64),
                np.arange(16) / 1e9,
                rtol=0,
                atol=1e-20,
            )
            np.testing.assert_allclose(
                np.asarray(voltage_values[0], dtype=np.float64),
                0.25 * expected_samples,
                rtol=0,
                atol=1e-14,
            )

            evidence = InstrumentStateEvidence.model_validate(
                run.record_json(INSTRUMENT_STATE_EVIDENCE_ID).content
            )
            final = {
                (
                    state.instrument_id,
                    observation.target.component_path,
                    observation.target.property_id,
                ): observation.value.root
                for state in evidence.final_state
                for observation in state.observations
            }
            assert final["drive-awg", (), "sample_rate"] == sc.Quantity(1e9, "Hz")
            assert final["drive-awg", (), "run_mode"] == "once"
            assert final["drive-awg", ("outputs", "ch1"), "output_enabled"] is True
            assert final["bench-scope", (), "sample_rate"] == sc.Quantity(1e9, "Hz")
            assert final["bench-scope", (), "record_length"] == 16
            assert final["bench-scope", (), "trigger_source"] == "external"
            assert final["bench-scope", (), "armed"] is False
            assert final["bench-scope", ("inputs", "ch1"), "impedance"] == "50_ohm"
            emitted = (tmp_path / "xy-emissions.jsonl").read_text(encoding="utf-8")
            emissions = [
                WaveformEmission.model_validate_json(line)
                for line in emitted.splitlines()
            ]
            assert len(emissions) == 1
            emission = emissions[0]
            assert emission.pid not in {os.getpid(), endpoint.pid}
            assert emission.component_path == ("outputs", "ch1")
            np.testing.assert_allclose(
                emission.samples, expected_samples, rtol=0, atol=1e-14
            )
            assert emission.sample_rate_hz == 1e9
            assert emission.amplitude_v == pytest.approx(0.25)
            assert emission.offset_v == 0
            assert emission.output_enabled
            assert not emission.repeat
            assert lab.setup.get("initial") == setup
            assert lab.config.registry().entries == ()
            assert {
                item.instrument_id: item.availability
                for item in lab.instruments.list(setup=setup.ref).items
                if item.instrument_id in {"drive-awg", "bench-scope"}
            } == {"drive-awg": "available", "bench-scope": "available"}

        with application.connect(endpoint.base_url) as reopened:
            retained = reopened.get_run(snapshot.run_id)
            assert retained.snapshot == snapshot
            assert retained.record_json(
                INSTRUMENT_STATE_EVIDENCE_ID
            ).content == evidence.model_dump(mode="json")
            restored = retained.measurements()
            np.testing.assert_array_equal(
                restored[invocation.output.time].require_values()[0], time_values[0]
            )
            np.testing.assert_array_equal(
                restored[invocation.output.voltage].require_values()[0],
                voltage_values[0],
            )
            assert (tmp_path / "xy-emissions.jsonl").read_text(
                encoding="utf-8"
            ) == emitted
    finally:
        stop_project(project)
