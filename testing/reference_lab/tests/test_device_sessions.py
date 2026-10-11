"""Retained worker contracts from the final direct-control and bias galleries."""

from __future__ import annotations

import numpy as np
import pytest
import scopecat as sc
from scopecat.records.execution import InstrumentStateEvidence
from scopecat.records.measurement import (
    EntityAcquisitionEvidence,
    MeasurementArray,
    MeasurementScalar,
)
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.run import ParameterRunConfigSource
from scopecat_instruments import (
    DCBiasGroupTarget,
    DCBiasReadbackProducts,
    DCSourceGroupTarget,
    dc_bias,
    dc_source,
    network_sweep,
    temperature_readout,
)

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.parameters import BiasProfile, ChannelCalibration


@sc.experiment
def _calibrated_bias_readback(
    experiment: sc.ExperimentContext,
) -> sc.PerEntity[DCBiasReadbackProducts]:
    qubits = sc.each(
        *(sc.EntityRef(id=f"q{i}", kind="logical_qubit") for i in range(4))
    )

    def profile(name: str) -> sc.PerEntity[sc.ValueRef[sc.Quantity]]:
        return sc.PerEntity(
            (
                qubit,
                sc.parameter_ref(BiasProfile.logical_bias, (name, qubit))
                * sc.parameter_ref(ChannelCalibration.flux_gain, qubit)
                * sc.parameter_ref(ChannelCalibration.flux_polarity, qubit)
                + sc.parameter_ref(ChannelCalibration.flux_offset, qubit),
            )
            for qubit in qubits
        )

    source = dc_source(experiment, for_=qubits)
    bias = dc_bias(experiment, for_=qubits)
    source.ensure(current_protection=sc.Quantity(100, "uA"), output_enabled=False)
    bias.ensure(
        target_voltage=profile("operate"),
        ramp_duration=sc.Quantity(250, "ms"),
        settle_tolerance=sc.Quantity(0.1, "mV"),
    )
    source.ensure(output_enabled=True)
    readback = bias.readback()
    experiment.on_success(bias, DCBiasGroupTarget(target_voltage=profile("parked")))
    experiment.on_success(source, DCSourceGroupTarget(output_enabled=False))
    return readback


def test_four_calibrated_routes_read_back_and_park_through_workers(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    with create_application(EXAMPLE_ROOT).connect(independent_lab_daemon) as lab:
        setup = lab.setup.get("initial")
        inputs = lab.parameters.resolve(independent_parameters, setup=setup)
        invocation = _calibrated_bias_readback.build()
        run = lab.prepare(invocation, config=inputs).run()
        assert run.status == "completed"
        source = run.snapshot.config_source
        assert isinstance(source, ParameterRunConfigSource)
        assert source.parameters == independent_parameters.ref
        assert source.setup == setup.ref
        data = run.measurements()
        assert len(data) == 1
        actual = data[invocation.entity_result_ref("actual_voltage")]
        [axis] = [
            dimension
            for dimension in data.schema.dimensions
            if dimension.kind == "entity"
        ]
        assert axis.index is not None
        assert [entity.id for entity in axis.index.values] == ["q0", "q1", "q2", "q3"]
        np.testing.assert_allclose(
            np.asarray(actual.require_magnitudes("mV")[0], dtype=np.float64),
            [-78.4, 22.4, 39.4, -96.0],
            rtol=0,
            atol=1e-10,
        )
        settled = data[invocation.entity_result_ref("settled")]
        np.testing.assert_array_equal(settled.require_values()[0], [True] * 4)
        routes = [
            ("flux-dac-a", ("channels", "ch1")),
            ("flux-dac-a", ("channels", "ch2")),
            ("flux-dac-b", ("channels", "ch1")),
            ("flux-dac-b", ("channels", "ch2")),
        ]
        [record] = data.records
        for variable in (actual, settled):
            evidence = record.acquisition_evidence.for_variable(variable.id)
            assert isinstance(evidence, EntityAcquisitionEvidence)
            assert evidence.dimension_id == axis.id
            assert len(evidence.values) == len(routes)
            for origin, (instrument, component) in zip(
                evidence.values, routes, strict=True
            ):
                assert origin is not None
                assert (origin.instrument_id, origin.component_path) == (
                    instrument,
                    component,
                )
                assert origin.interface_id == "scopecat.dc_bias/v1"
                assert origin.acquisition_id == "readback"
                assert origin.result_id == variable.id
        state = InstrumentStateEvidence.model_validate(
            run.record_json("instrument-state-evidence").content
        )
        observed = {
            (
                device.instrument_id,
                observation.target.component_path,
                observation.target.property_id,
            ): observation.value.root
            for device in state.final_state
            for observation in device.observations
        }
        for (instrument, component), parked_mv in zip(
            routes, (0.0, 2.0, -1.0, 3.0), strict=True
        ):
            for property_id in ("actual_voltage", "target_voltage"):
                value = observed[instrument, component, property_id]
                assert isinstance(value, sc.Quantity)
                assert value.to("mV").value == pytest.approx(parked_mv, abs=1e-10)
            assert observed[instrument, component, "output_enabled"] is False
            assert observed[instrument, component, "settled"] is True
        assert lab.parameters.get(independent_parameters.id) == independent_parameters
        assert lab.setup.get("initial") == setup
        assert lab.config.registry().entries == ()


def test_typed_direct_session_operates_and_releases_multiple_worker_devices(
    independent_lab_daemon: str,
) -> None:
    source_ref = dc_source("bench-source")
    thermometer_ref = temperature_readout("mixing-chamber")
    vna_ref = network_sweep("readout-vna")
    with create_application(EXAMPLE_ROOT).connect(independent_lab_daemon) as lab:
        setup = lab.setup.get("initial")
        with lab.instruments.open(
            source_ref, thermometer_ref, vna_ref, setup=setup.ref
        ) as devices:
            source = devices[source_ref]
            thermometer = devices[thermometer_ref]
            vna = devices[vna_ref]
            source.apply(output_enabled=False)
            baseline = thermometer.sample().temperature
            assert isinstance(baseline, MeasurementScalar)
            assert isinstance(baseline.value, float)
            invoked = source.source_voltage(
                range=sc.Quantity(1, "V"), level=sc.Quantity(0.05, "V")
            )
            source.apply(output_enabled=True)
            try:
                temperature = thermometer.sample()
                vna.apply(
                    start_frequency=sc.Quantity(4.8, "GHz"),
                    stop_frequency=sc.Quantity(5.2, "GHz"),
                    points=7,
                )
                trace = vna.sweep()
                assert invoked.status == "invoked"
                assert temperature.receipt.status == trace.receipt.status == "collected"
                assert isinstance(temperature.temperature, MeasurementScalar)
                assert temperature.temperature.unit == "K"
                assert temperature.temperature.value == pytest.approx(
                    baseline.value + 5e-6
                )
                assert isinstance(trace.frequency, MeasurementArray)
                assert trace.frequency.unit == "Hz"
                np.testing.assert_allclose(
                    np.asarray(trace.frequency.values, dtype=np.float64),
                    np.linspace(4.8e9, 5.2e9, 7),
                )
                assert isinstance(trace.s_parameter, MeasurementArray)
                assert trace.s_parameter.dtype == "complex128"
                assert (
                    trace.s_parameter.unit == "ratio"
                    and trace.s_parameter.shape == (7,)
                )
                samples = np.asarray(trace.s_parameter.values, dtype=np.complex128)
                assert np.all(np.isfinite(samples))
                assert np.any(np.iscomplex(samples))
                owned = {
                    item.instrument_id: item
                    for item in lab.instruments.list(setup=setup.ref).items
                }
                for instrument in ("bench-source", "mixing-chamber", "readout-vna"):
                    assert owned[instrument].availability == "active"
                    assert owned[instrument].owner_kind == "instrument_session"
            finally:
                source.apply(output_enabled=False)
            assert source.output_enabled.read() is False
        released = {
            item.instrument_id: item
            for item in lab.instruments.list(setup=setup.ref).items
        }
        for instrument in ("bench-source", "mixing-chamber", "readout-vna"):
            assert released[instrument].availability == "available"
            assert released[instrument].owner_id is None
        assert lab.setup.get("initial") == setup
        assert lab.config.registry().entries == ()
