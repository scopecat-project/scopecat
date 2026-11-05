from __future__ import annotations

from scopecat.kernel.quantity import Quantity
from scopecat.sdk.instruments import (
    DriverAcquisition,
    DriverOperation,
    DriverRejected,
    DriverStatePatch,
    DriverStateReadback,
    DriverSuccess,
    InstrumentDriver,
    ObjectInstrumentDriver,
    state_capture_request,
)

from scopecat_instruments._support import LinearSweepSettings
from scopecat_instruments.members import (
    DC_MONITOR_CURRENT_RESULT,
    DC_MONITOR_INTEGRATION_CYCLES,
    DC_MONITOR_MEASURE_CURRENT,
    DC_MONITOR_MEASUREMENT_DELAY,
    DC_MONITOR_MEASUREMENT_ENABLED,
    DC_SOURCE_CURRENT,
    DC_SOURCE_MODE,
    DC_SOURCE_OUTPUT_ENABLED,
    DC_SOURCE_VOLTAGE,
    NETWORK_SWEEP_POINTS,
    NETWORK_SWEEP_S_PARAMETER,
    RF_OUTPUT_ENABLED,
    RF_OUTPUT_FREQUENCY,
    TEMPERATURE_READOUT_RESISTANCE_RESULT,
    TEMPERATURE_READOUT_SAMPLE,
    TEMPERATURE_READOUT_TEMPERATURE_RESULT,
)
from scopecat_instruments.virtual import (
    VirtualDcSource,
    VirtualLabWorld,
    VirtualNetworkAnalyzer,
    VirtualRfSource,
    VirtualTemperatureMonitor,
)


def _capture(driver: InstrumentDriver) -> DriverStateReadback:
    return driver.read_state(state_capture_request(driver.describe()))


def _notch_frequency(
    trace_frequencies: tuple[float, ...],
    values: tuple[complex, ...],
) -> float:
    index = min(range(len(values)), key=lambda selected: abs(values[selected]))
    return trace_frequencies[index]


def test_virtual_drivers_use_oo_adapter() -> None:
    assert issubclass(VirtualRfSource, ObjectInstrumentDriver)
    assert issubclass(VirtualDcSource, ObjectInstrumentDriver)
    assert issubclass(VirtualTemperatureMonitor, ObjectInstrumentDriver)
    assert issubclass(VirtualNetworkAnalyzer, ObjectInstrumentDriver)


def test_virtual_state_survives_driver_disconnect() -> None:
    world = VirtualLabWorld(seed=11)
    first = VirtualDcSource("flux", world)
    transitioned = first.invoke(
        DriverOperation(
            target=DC_SOURCE_VOLTAGE,
            arguments={
                "range": Quantity(1.0, "V"),
                "level": Quantity(0.125, "V"),
            },
        )
    )
    first.set_output(True)
    first.disconnect()

    second = VirtualDcSource("flux", world)

    assert isinstance(transitioned, DriverSuccess)
    assert second.read_output_enabled() is True
    assert world.flux_bias() == 0.5
    assert _capture(second).values[DC_SOURCE_MODE] == "voltage"


def test_virtual_rf_driver_applies_typed_sparse_patch() -> None:
    driver = VirtualRfSource("readout-lo", VirtualLabWorld(seed=12))
    driver.set_output(True)

    receipt = driver.apply_state(
        DriverStatePatch(
            values={
                RF_OUTPUT_FREQUENCY: Quantity(6.0e9, "Hz"),
                RF_OUTPUT_ENABLED: False,
            }
        )
    )

    assert isinstance(receipt, DriverSuccess)
    assert receipt.value is None
    state = _capture(driver)
    assert state.values[RF_OUTPUT_FREQUENCY] == Quantity(6.0e9, "Hz")
    assert state.values[RF_OUTPUT_ENABLED] is False


def test_virtual_dc_current_case_drives_physics_and_snapshot_shape() -> None:
    world = VirtualLabWorld(seed=13)
    driver = VirtualDcSource("flux", world)
    driver.set_output(True)

    transitioned = driver.invoke(
        DriverOperation(
            target=DC_SOURCE_CURRENT,
            arguments={
                "range": Quantity(0.01, "A"),
                "level": Quantity(0.001, "A"),
            },
        )
    )
    configured = driver.apply_state(
        DriverStatePatch(
            values={
                DC_MONITOR_INTEGRATION_CYCLES: 5,
                RF_OUTPUT_FREQUENCY: Quantity(6.0e9, "Hz"),
            },
        )
    )

    assert isinstance(transitioned, DriverSuccess)
    assert transitioned.value is None
    assert isinstance(configured, DriverSuccess)
    property_targets = _capture(driver).values
    assert property_targets[DC_SOURCE_MODE] == "current"
    assert property_targets[DC_SOURCE_OUTPUT_ENABLED] is True
    assert property_targets[DC_MONITOR_INTEGRATION_CYCLES] == 5
    assert RF_OUTPUT_FREQUENCY not in property_targets
    source = world.dc_source("flux")
    assert source.current_range_a == 0.01
    assert source.current_level_a == 0.001
    assert world.flux_bias() == 0.5


def test_virtual_dc_source_operations_select_mode() -> None:
    driver = VirtualDcSource("flux", VirtualLabWorld(seed=13))

    current = driver.invoke(
        DriverOperation(
            target=DC_SOURCE_CURRENT,
            arguments={
                "range": Quantity(0.01, "A"),
                "level": Quantity(0.001, "A"),
            },
        )
    )
    assert isinstance(current, DriverSuccess)
    assert driver.read_source_mode() == "current"

    voltage = driver.invoke(
        DriverOperation(
            target=DC_SOURCE_VOLTAGE,
            arguments={
                "range": Quantity(1.0, "V"),
                "level": Quantity(0.125, "V"),
            },
        )
    )

    assert isinstance(voltage, DriverSuccess)
    assert driver.read_source_mode() == "voltage"


def test_virtual_dc_monitor_configuration_round_trips_through_state() -> None:
    driver = VirtualDcSource("flux", VirtualLabWorld(seed=13))

    receipt = driver.apply_state(
        DriverStatePatch(
            values={
                DC_MONITOR_MEASUREMENT_ENABLED: False,
                DC_MONITOR_INTEGRATION_CYCLES: 5,
                DC_MONITOR_MEASUREMENT_DELAY: Quantity(0.25, "s"),
            }
        )
    )

    assert isinstance(receipt, DriverSuccess)
    assert receipt.value is None
    properties = _capture(driver).values
    assert properties[DC_MONITOR_MEASUREMENT_ENABLED] is False
    assert properties[DC_MONITOR_INTEGRATION_CYCLES] == 5
    delay = properties[DC_MONITOR_MEASUREMENT_DELAY]
    assert isinstance(delay, Quantity)
    assert delay == Quantity(0.25, "s")


def test_virtual_dc_monitor_requires_source_output_and_measurement_enabled() -> None:
    driver = VirtualDcSource("flux", VirtualLabWorld(seed=13))
    request = DriverAcquisition(
        target=DC_MONITOR_MEASURE_CURRENT,
        results=frozenset({DC_MONITOR_CURRENT_RESULT}),
    )

    driver.set_output(True)
    assert isinstance(driver.collect(request), DriverSuccess)

    driver.set_output(False)
    receipt = driver.collect(request)

    assert isinstance(receipt, DriverRejected)
    assert receipt.problems[0].code == "virtual_dc_monitor_output_disabled"

    driver.set_output(True)
    driver.set_measurement_enabled(False)
    receipt = driver.collect(request)

    assert isinstance(receipt, DriverRejected)
    assert receipt.problems[0].code == "virtual_dc_monitor_disabled"


def test_virtual_temperature_adapter_preserves_readback_metadata() -> None:
    driver = VirtualTemperatureMonitor("mixing-chamber", VirtualLabWorld(seed=19))

    receipt = driver.collect(
        DriverAcquisition(
            target=TEMPERATURE_READOUT_SAMPLE,
            results=frozenset(
                {
                    TEMPERATURE_READOUT_TEMPERATURE_RESULT,
                    TEMPERATURE_READOUT_RESISTANCE_RESULT,
                }
            ),
        )
    )

    assert isinstance(receipt, DriverSuccess)
    assert set(receipt.value.values) == {
        TEMPERATURE_READOUT_TEMPERATURE_RESULT,
        TEMPERATURE_READOUT_RESISTANCE_RESULT,
    }
    assert receipt.value.metadata == {
        "mode": "virtual",
        "world_seed": 19,
        "scan_channel": 1,
        "autoscan_enabled": False,
        "reading_status": 0,
    }
    state = _capture(driver)
    assert state.observations
    assert all(
        observation.metadata == {"mode": "virtual", "world_seed": 19}
        for observation in state.observations
    )


def test_virtual_network_noise_is_deterministic_for_seed() -> None:
    settings = LinearSweepSettings(
        start_frequency_hz=4.9e9,
        stop_frequency_hz=5.1e9,
        points=101,
        if_bandwidth_hz=1.0e3,
        source_power_dbm=-30.0,
        s_parameter="S21",
    )
    first = VirtualNetworkAnalyzer("vna", VirtualLabWorld(seed=23))
    second = VirtualNetworkAnalyzer("vna", VirtualLabWorld(seed=23))
    first.configure_linear_sweep(settings)
    second.configure_linear_sweep(settings)

    assert first.acquire_trace() == second.acquire_trace()


def test_virtual_network_analyzer_applies_typed_sparse_patch() -> None:
    driver = VirtualNetworkAnalyzer("vna", VirtualLabWorld(seed=23))

    receipt = driver.apply_state(
        DriverStatePatch(
            values={
                NETWORK_SWEEP_S_PARAMETER: "S11",
                NETWORK_SWEEP_POINTS: 17,
                RF_OUTPUT_FREQUENCY: Quantity(6.0e9, "Hz"),
            }
        )
    )

    assert isinstance(receipt, DriverSuccess)
    assert receipt.value is None
    state = _capture(driver)
    assert state.values[NETWORK_SWEEP_S_PARAMETER] == "S11"
    assert state.values[NETWORK_SWEEP_POINTS] == 17
    assert RF_OUTPUT_FREQUENCY not in state.values


def test_flux_moves_notch_and_temperature_broadens_response() -> None:
    world = VirtualLabWorld(seed=7)
    vna = VirtualNetworkAnalyzer("vna", world)
    vna.configure_linear_sweep(
        LinearSweepSettings(
            start_frequency_hz=4.9e9,
            stop_frequency_hz=5.1e9,
            points=401,
            if_bandwidth_hz=1.0,
            source_power_dbm=-30.0,
            s_parameter="S21",
        )
    )
    baseline = vna.acquire_trace()

    dc = VirtualDcSource("flux", world)
    transitioned = dc.invoke(
        DriverOperation(
            target=DC_SOURCE_VOLTAGE,
            arguments={
                "range": Quantity(1.0, "V"),
                "level": Quantity(0.125, "V"),
            },
        )
    )
    dc.set_output(True)
    flux_shifted = vna.acquire_trace()

    assert isinstance(transitioned, DriverSuccess)
    baseline_notch = _notch_frequency(
        baseline.frequencies_hz,
        baseline.values,
    )
    shifted_notch = _notch_frequency(
        flux_shifted.frequencies_hz,
        flux_shifted.values,
    )
    assert abs(shifted_notch - baseline_notch) > 20.0e6

    cold_count = sum(abs(value) < 0.9 for value in flux_shifted.values)
    world.set_base_temperature(0.2)
    hot = vna.acquire_trace()
    hot_count = sum(abs(value) < 0.9 for value in hot.values)
    assert hot_count > cold_count


def test_existing_trace_does_not_trigger_or_follow_current_settings() -> None:
    world = VirtualLabWorld(seed=23)
    driver = VirtualNetworkAnalyzer("vna", world)
    assert isinstance(driver.read_trace(), DriverRejected)
    first = driver.sweep()
    assert isinstance(first, DriverSuccess)
    driver.write_points(17)
    driver.write_start_frequency(Quantity(4.7e9, "Hz"))
    for _ in range(3):
        existing = driver.read_trace()
        assert isinstance(existing, DriverSuccess)
        assert existing.value == first.value
    # A second driver observes the same simulated panel; reading did not consume RNG.
    reopened = VirtualNetworkAnalyzer("vna", world)
    assert reopened.read_trace() == first
    next_trace = driver.sweep()
    assert isinstance(next_trace, DriverSuccess)
    assert next_trace.value.frequency != first.value.frequency
    assert next_trace.value.evidence["settings"] != first.value.evidence["settings"]
    assert driver.read_trace() == next_trace
