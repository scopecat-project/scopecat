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
from reference_lab.workflows.ragged_scope_capture import ragged_scope_capture

from ._bench_probe import WaveformEmission


@pytest.mark.parametrize("ragged", [False, True], ids=["awg-monitor", "ragged-scope"])
def test_bench_capture_crosses_daemon_and_instrument_worker(
    tmp_path: Path,
    ragged: bool,
) -> None:
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, tmp_path / name)
    shutil.copyfile(
        Path(__file__).with_name("_bench_probe.py"),
        tmp_path / "src" / "_bench_probe.py",
    )
    (tmp_path / "scopecat.toml").write_text(
        (EXAMPLE_ROOT / "scopecat.toml")
        .read_text(encoding="utf-8")
        .replace("reference_lab.backend:create_backend", "_bench_probe:create_backend"),
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
            invocation = (
                ragged_scope_capture.build() if ragged else awg_output_monitor.build()
            )
            lengths = [4, 7, 10] if ragged else [16]
            amplitude = 0.1 if ragged else 0.25
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
            if ragged:
                expected_samples = np.array([0, 0.5, 1, 0.5, 0, -0.5, -1, -0.5])
                np.testing.assert_array_equal(
                    data["record_length"].require_values(), lengths
                )
            time_values = data[invocation.output.time].require_values()
            voltage_values = data[invocation.output.voltage].require_values()
            assert len(time_values) == len(voltage_values) == len(lengths)
            for index, length in enumerate(lengths):
                # A ten-sample record must wrap the eight-sample continuous probe.
                expected_voltage = (
                    amplitude
                    * expected_samples[np.arange(length) % len(expected_samples)]
                )
                assert (
                    time_values[index].shape == voltage_values[index].shape == (length,)
                )
                np.testing.assert_allclose(
                    np.asarray(time_values[index], dtype=np.float64),
                    np.arange(length) / 1e9,
                    rtol=0,
                    atol=1e-20,
                )
                np.testing.assert_allclose(
                    np.asarray(voltage_values[index], dtype=np.float64),
                    expected_voltage,
                    rtol=0,
                    atol=1e-14,
                )
            if ragged:
                voltage = data[invocation.output.voltage]
                assert voltage.layout == "ragged"
                window = data.isel_ragged(
                    {voltage.dims[1]: slice(0, 2)},
                    variable=voltage.id,
                )[invocation.output.voltage].require_values()
                assert len(window) == 3
                for values in window:
                    assert values.shape == (2,)
                    np.testing.assert_allclose(
                        np.asarray(values, dtype=np.float64),
                        [0, 0.05],
                        rtol=0,
                        atol=1e-14,
                    )

            evidence = InstrumentStateEvidence.model_validate(
                run.record_json(INSTRUMENT_STATE_EVIDENCE_ID).content
            )
            assert evidence.state_actions is not None
            assert evidence.state_actions.detail_complete
            if ragged:
                assert [
                    setting.value.root
                    for action in evidence.state_actions.retained_prefix
                    if action.instrument_id == "bench-scope"
                    for setting in action.assignments
                    if setting.target.property_id == "record_length"
                ] == lengths
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
            assert final["drive-awg", (), "run_mode"] == (
                "continuous" if ragged else "once"
            )
            assert final["drive-awg", ("outputs", "ch1"), "output_enabled"] is True
            assert final["bench-scope", (), "sample_rate"] == sc.Quantity(1e9, "Hz")
            assert final["bench-scope", (), "record_length"] == lengths[-1]
            assert final["bench-scope", (), "trigger_source"] == "external"
            assert final["bench-scope", (), "armed"] is False
            assert final["bench-scope", ("inputs", "ch1"), "impedance"] == "50_ohm"
            emitted = (tmp_path / "bench-emissions.jsonl").read_text(encoding="utf-8")
            emissions = [
                WaveformEmission.model_validate_json(line)
                for line in emitted.splitlines()
            ]
            assert len(emissions) == len(lengths)
            assert len({item.pid for item in emissions}) == 1
            for emission in emissions:
                assert emission.pid not in {os.getpid(), endpoint.pid}
                assert emission.component_path == ("outputs", "ch1")
                np.testing.assert_allclose(
                    emission.samples, expected_samples, rtol=0, atol=1e-14
                )
                assert emission.sample_rate_hz == 1e9
                assert emission.amplitude_v == pytest.approx(amplitude)
                assert emission.offset_v == 0
                assert emission.output_enabled
                assert emission.repeat is ragged
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
            restored_time = restored[invocation.output.time].require_values()
            restored_voltage = restored[invocation.output.voltage].require_values()
            assert len(restored_time) == len(restored_voltage) == len(lengths)
            for index in range(len(lengths)):
                np.testing.assert_array_equal(restored_time[index], time_values[index])
                np.testing.assert_array_equal(
                    restored_voltage[index], voltage_values[index]
                )
            if ragged:
                voltage = restored[invocation.output.voltage]
                restored_window = restored.isel_ragged(
                    {voltage.dims[1]: slice(0, 2)},
                    variable=voltage.id,
                )[invocation.output.voltage].require_values()
                assert len(restored_window) == 3
                for values in restored_window:
                    np.testing.assert_array_equal(values, np.array([0, 0.05]))
            assert (tmp_path / "bench-emissions.jsonl").read_text(
                encoding="utf-8"
            ) == emitted
    finally:
        stop_project(project)
