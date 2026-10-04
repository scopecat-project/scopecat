from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import scopecat as sc
from scopecat.compiler.bind import bind_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.config.environment import build_config_environment
from scopecat.execution.evidence import INSTRUMENT_STATE_EVIDENCE_ID
from scopecat.execution.local.program import ApplyStateOperation, InvokeOperation
from scopecat.execution.program import RunCoverageEffect
from scopecat.planning.compilation import compile_run_program
from scopecat.project import load_project
from scopecat.records.execution import InstrumentStateEvidence
from scopecat.records.run import ParameterRunConfigSource
from scopecat.sdk.instruments import InterfaceRef
from scopecat_instruments.members import REFERENCE_CLOCK, RF_OUTPUT
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_testkit.instrument_host import compose_test_instruments

from reference_lab.application import create_application
from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT,
    AWG_SEQUENCER,
)
from reference_lab.configuration import (
    EXAMPLE_ROOT,
    bootstrap_config,
    initial_parameters,
)
from reference_lab.interfaces import CLOCK_TIMING
from reference_lab.payloads import reference_lab_payload_codecs
from reference_lab.provider import ReferenceLabProvider
from reference_lab.workflows.xy_drive import xy_lo_sweep

from ._bench_probe import WaveformEmission


def test_xy_drive_declares_physical_i_and_q_resources_per_entity() -> None:
    logical = compile_invocation(xy_lo_sweep.build()).program.program

    resource_ids = [port.id for port in logical.resource_ports]
    assert resource_ids[0].startswith("xy_drive.lo.logical-qubit-q0-")
    assert resource_ids[1].startswith("xy_drive.lo.logical-qubit-q1-")
    assert resource_ids[2:] == [
        "xy_drive.i.q0",
        "xy_drive.i.q1",
        "xy_drive.q.q0",
        "xy_drive.q.q1",
    ]
    i_ports = logical.resource_ports[2:4]
    q_ports = logical.resource_ports[4:]
    assert all(
        len(port.selector.capabilities) == 4
        and all(
            not isinstance(capability, InterfaceRef)
            for capability in port.selector.capabilities
        )
        for port in logical.resource_ports[:2]
    )
    assert all(
        len(port.selector.capabilities) == 8
        and all(
            not isinstance(capability, InterfaceRef)
            for capability in port.selector.capabilities
        )
        for port in (*i_ports, *q_ports)
    )
    assert all(
        port.selector.interfaces
        == (RF_OUTPUT.interface_id, REFERENCE_CLOCK.interface_id)
        for port in logical.resource_ports[:2]
    )
    assert all(
        port.selector.interfaces
        == (
            AWG_SEQUENCER.interface_id,
            ANALOG_WAVEFORM_OUTPUT.interface_id,
            REFERENCE_CLOCK.interface_id,
            CLOCK_TIMING.interface_id,
        )
        for port in (*i_ports, *q_ports)
    )
    assert all(port.selector.role.role_id == "drive-i" for port in i_ports)
    assert all(port.selector.role.role_id == "drive-q" for port in q_ports)
    assert len(logical.compute_nodes) == 4
    assert [record.id for record in logical.value_record_selections] == [
        "requested_carrier_frequency/logical_qubit/q0",
        "requested_carrier_frequency/logical_qubit/q1",
    ]


def test_xy_drive_composes_shared_awg_state_and_real_dac_operations() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider()
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        payload_codecs=reference_lab_payload_codecs(),
    )
    bound = bind_program(
        compile_invocation(xy_lo_sweep.build()).program,
        build_config_environment(config),
    )
    plan = compile_run_program(composition.system, bound=bound)
    coverage = tuple(plan.coverage)
    operations = {
        effect.operation.instrument_id: effect.operation
        for effect in coverage
        if isinstance(effect, RunCoverageEffect)
        and effect.point_index == 0
        and isinstance(effect.operation, ApplyStateOperation)
    }

    lo = operations["drive-lo-a"]
    assert {target.property_id for target in lo.targets} == {
        "frequency",
        "power",
        "output_enabled",
        "reference_source",
    }
    assert all(target.component_path == () for target in lo.targets)
    assert all(len(target.origins) == 2 for target in lo.targets)
    assert all(target.entity_ids == ("q0", "q1") for target in lo.targets)

    awg = operations["drive-awg"]
    shared = [target for target in awg.targets if not target.component_path]
    outputs = [target for target in awg.targets if target.component_path]
    assert {target.property_id for target in shared} == {
        "sample_rate",
        "run_mode",
        "reference_source",
        "frequency",
    }
    assert all(len(target.origins) == 4 for target in shared)
    assert {target.component_path for target in outputs} == {
        ("outputs", "ch1"),
        ("outputs", "ch2"),
        ("outputs", "ch3"),
        ("outputs", "ch4"),
    }
    assert len(outputs) == 12
    assert all(len(target.origins) == 1 for target in outputs)

    point_operations = [
        effect.operation
        for effect in coverage
        if isinstance(effect, RunCoverageEffect) and effect.point_index == 0
    ]
    invocations = [
        operation
        for operation in point_operations
        if isinstance(operation, InvokeOperation)
    ]
    assert [operation.instrument_id for operation in invocations] == [
        "drive-awg",
        "drive-awg",
        "drive-awg",
        "drive-awg",
    ]
    assert {operation.component_path for operation in invocations} == {
        ("outputs", "ch1"),
        ("outputs", "ch2"),
        ("outputs", "ch3"),
        ("outputs", "ch4"),
    }
    assert {operation.resource.route_role_id for operation in invocations} == {
        "drive-i",
        "drive-q",
    }
    assert all(operation.operation_id == "play" for operation in invocations)
    assert max(
        index
        for index, operation in enumerate(point_operations)
        if isinstance(operation, ApplyStateOperation)
    ) < min(
        index
        for index, operation in enumerate(point_operations)
        if isinstance(operation, InvokeOperation)
    )


def test_xy_drive_signed_if_crosses_daemon_and_instrument_worker(
    tmp_path: Path,
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
                name="xy-drive-inputs",
                catalog=content.catalog,
                parameters=content.parameters,
            )
            invocation = xy_lo_sweep.build()
            run = lab.run(
                invocation, config=lab.parameters.resolve(parameters, setup=setup)
            )
            snapshot = run.snapshot
            assert run.status == "completed"
            assert isinstance(snapshot.config_source, ParameterRunConfigSource)
            assert snapshot.config_source.parameters == parameters.ref
            assert snapshot.config_source.setup == setup.ref
            data = run.measurements()
            assert [
                q.value
                for q in data[
                    invocation.output.requested_lo_frequency
                ].require_quantities("GHz")
            ] == pytest.approx([4.9, 4.91, 4.92])
            for entity, record in invocation.output.requested_carrier_frequency.items():
                expected = [5.0, 5.01, 5.02] if entity.id == "q0" else [4.8, 4.81, 4.82]
                assert [
                    q.value for q in data[record].require_quantities("GHz")
                ] == pytest.approx(expected)

            evidence = InstrumentStateEvidence.model_validate(
                run.record_json(INSTRUMENT_STATE_EVIDENCE_ID).content
            )
            assert evidence.state_actions is not None
            assert evidence.state_actions.detail_complete
            # Confirm actual shared-LO commands, not just computed carrier records.
            lo_frequencies = [
                setting.value.root.to("GHz").value
                for action in evidence.state_actions.retained_prefix
                if action.instrument_id == "drive-lo-a"
                for setting in action.assignments
                if setting.target.property_id == "frequency"
                and isinstance(setting.value.root, sc.Quantity)
            ]
            assert lo_frequencies == pytest.approx([4.9, 4.91, 4.92])
            final = {
                (
                    state.instrument_id,
                    observation.target.component_path,
                    observation.target.property_id,
                ): observation.value.root
                for state in evidence.final_state
                for observation in state.observations
            }
            assert final["drive-lo-a", (), "frequency"] == sc.Quantity(4.92e9, "Hz")
            assert final["drive-awg", (), "sample_rate"] == sc.Quantity(1e9, "Hz")
            assert final["drive-awg", (), "reference_source"] == "external"
            assert final["drive-awg", (), "locked"] is True

            emitted = (tmp_path / "bench-emissions.jsonl").read_text(encoding="utf-8")
            emissions = [
                WaveformEmission.model_validate_json(line)
                for line in emitted.splitlines()
            ]
            assert len(emissions) == 12
            assert len({item.pid for item in emissions}) == 1
            assert emissions[0].pid not in {os.getpid(), endpoint.pid}
            phase = 2 * np.pi * 0.1 * np.arange(40)
            expected_waveforms = (
                np.cos(phase),
                np.sin(phase),
                np.cos(phase),
                -np.sin(phase),
            )
            for index, emission in enumerate(emissions):
                channel = index % 4
                assert emission.component_path == ("outputs", f"ch{channel + 1}")
                np.testing.assert_allclose(
                    emission.samples, expected_waveforms[channel], atol=1e-14
                )
                assert emission.sample_rate_hz == 1e9
                assert emission.amplitude_v == pytest.approx(0.2)
                assert emission.offset_v == 0
                assert emission.output_enabled
                assert not emission.repeat
            assert lab.setup.get("initial") == setup
            assert lab.config.registry().entries == ()
            assert {
                item.instrument_id: item.availability
                for item in lab.instruments.list(setup=setup.ref).items
                if item.instrument_id in {"drive-lo-a", "drive-awg"}
            } == {"drive-lo-a": "available", "drive-awg": "available"}

        with application.connect(endpoint.base_url) as reopened:
            retained = reopened.get_run(snapshot.run_id)
            assert retained.snapshot == snapshot
            assert retained.record_json(
                INSTRUMENT_STATE_EVIDENCE_ID
            ).content == evidence.model_dump(mode="json")
            assert (tmp_path / "bench-emissions.jsonl").read_text(
                encoding="utf-8"
            ) == emitted
    finally:
        stop_project(project)
