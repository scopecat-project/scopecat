"""Vendor-neutral OO drivers backed by one shared virtual world."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, override

import numpy as np
from scopecat.kernel.quantity import Quantity
from scopecat.records.measurement import MeasurementArray, MeasurementScalar
from scopecat.sdk.instruments import (
    DriverOutcome,
    DriverRejected,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    ObjectInstrumentDriver,
    implements,
    instrument_driver,
    query,
    read,
    write,
)

from scopecat_instruments._support import (
    LinearSweepSettings,
    NetworkTrace,
    execution_problem,
    invoke_unknown,
    quantity_value,
    state_property_problem,
)
from scopecat_instruments.driver_observations import (
    DCMonitorCurrentObservation,
    DCMonitorVoltageObservation,
    NetworkSweepObservation,
    TemperatureSampleObservation,
)
from scopecat_instruments.interface_declarations import (
    DCMonitorInterface,
    DCSourceInterface,
    ExistingNetworkTraceInterface,
    NetworkSweepInterface,
    ReferenceClockInterface,
    ReferenceSource,
    RFOutputInterface,
    SParameter,
    TemperatureReadoutInterface,
)
from scopecat_instruments.members import (
    DC_MONITOR_MEASUREMENT_ENABLED,
    DC_SOURCE_MODE,
    DC_SOURCE_OUTPUT_ENABLED,
)
from scopecat_instruments.package_manifest import (
    VIRTUAL_DC_SOURCE_DRIVER,
    VIRTUAL_RF_SOURCE_DRIVER,
    VIRTUAL_TEMPERATURE_MONITOR_DRIVER,
    VIRTUAL_VNA_DRIVER,
)
from scopecat_instruments.virtual.world import VirtualLabWorld


class _VirtualDriver(ObjectInstrumentDriver):
    world: VirtualLabWorld  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        readback = super().read_state(request)
        return readback.with_observation_metadata(
            {"mode": "virtual", "world_seed": self.world.seed}
        )


@instrument_driver(
    VIRTUAL_RF_SOURCE_DRIVER.id,
    VIRTUAL_RF_SOURCE_DRIVER.implementation_version,
    interfaces=(RFOutputInterface, ReferenceClockInterface),
    label="Virtual RF source",
)
class VirtualRfSource(_VirtualDriver):
    def __init__(self, instrument_id: str, world: VirtualLabWorld) -> None:
        self.instrument_id = instrument_id
        self.world = world
        self.world.rf_source(instrument_id)

    @read(RFOutputInterface.frequency)
    def read_frequency(self) -> Quantity:
        with self.world.lock:
            return Quantity(self.world.rf_source(self.instrument_id).frequency_hz, "Hz")

    @write(RFOutputInterface.frequency)
    def write_frequency(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.rf_source(self.instrument_id).frequency_hz = quantity_value(
                value, "Hz"
            )

    @read(RFOutputInterface.power)
    def read_power(self) -> Quantity:
        with self.world.lock:
            return Quantity(self.world.rf_source(self.instrument_id).power_dbm, "dBm")

    @write(RFOutputInterface.power)
    def write_power(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.rf_source(self.instrument_id).power_dbm = quantity_value(
                value, "dBm"
            )

    @read(RFOutputInterface.output_enabled)
    def read_output_enabled(self) -> bool:
        with self.world.lock:
            return self.world.rf_source(self.instrument_id).output_enabled

    @write(RFOutputInterface.output_enabled)
    def write_output_enabled(self, value: bool) -> None:
        with self.world.lock:
            self.world.rf_source(self.instrument_id).output_enabled = value

    @read(ReferenceClockInterface.reference_source)
    def read_reference_source(self) -> ReferenceSource:
        with self.world.lock:
            return self.world.rf_source(self.instrument_id).reference_source

    @write(ReferenceClockInterface.reference_source)
    def write_reference_source(self, value: ReferenceSource) -> None:
        with self.world.lock:
            self.world.rf_source(self.instrument_id).reference_source = value

    def set_frequency(self, frequency_hz: float) -> None:
        self.write_frequency(Quantity(frequency_hz, "Hz"))

    def set_power(self, power_dbm: float) -> None:
        self.write_power(Quantity(power_dbm, "dBm"))

    def set_output(self, enabled: bool) -> None:
        self.write_output_enabled(enabled)

    def set_reference_source(self, source: ReferenceSource) -> None:
        self.write_reference_source(source)


@instrument_driver(
    VIRTUAL_DC_SOURCE_DRIVER.id,
    VIRTUAL_DC_SOURCE_DRIVER.implementation_version,
    interfaces=(DCSourceInterface, DCMonitorInterface),
    label="Virtual DC source",
)
class VirtualDcSource(_VirtualDriver):
    def __init__(self, instrument_id: str, world: VirtualLabWorld) -> None:
        self.instrument_id = instrument_id
        self.world = world
        self.world.dc_source(instrument_id)

    @read(DCSourceInterface.source_mode)
    def read_source_mode(self) -> Literal["voltage", "current"]:
        with self.world.lock:
            return self.world.dc_source(self.instrument_id).source_mode

    @read(DCSourceInterface.voltage_protection)
    def read_voltage_protection(self) -> Quantity:
        with self.world.lock:
            return Quantity(
                self.world.dc_source(self.instrument_id).voltage_protection_v, "V"
            )

    @write(DCSourceInterface.voltage_protection)
    def write_voltage_protection(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.dc_source(
                self.instrument_id
            ).voltage_protection_v = quantity_value(value, "V")

    @read(DCSourceInterface.current_protection)
    def read_current_protection(self) -> Quantity:
        with self.world.lock:
            return Quantity(
                self.world.dc_source(self.instrument_id).current_protection_a, "A"
            )

    @write(DCSourceInterface.current_protection)
    def write_current_protection(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.dc_source(
                self.instrument_id
            ).current_protection_a = quantity_value(value, "A")

    @read(DCSourceInterface.output_enabled)
    def read_output_enabled(self) -> bool:
        with self.world.lock:
            return self.world.dc_source(self.instrument_id).output_enabled

    @write(DCSourceInterface.output_enabled)
    def write_output_enabled(self, value: bool) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).output_enabled = value

    @read(DCMonitorInterface.measurement_enabled)
    def read_measurement_enabled(self) -> bool:
        with self.world.lock:
            return self.world.dc_source(self.instrument_id).measurement_enabled

    @write(DCMonitorInterface.measurement_enabled)
    def write_measurement_enabled(self, value: bool) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).measurement_enabled = value

    @read(DCMonitorInterface.integration_cycles)
    def read_integration_cycles(self) -> int:
        with self.world.lock:
            return self.world.dc_source(self.instrument_id).integration_cycles

    @write(DCMonitorInterface.integration_cycles)
    def write_integration_cycles(self, value: int) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).integration_cycles = value

    @read(DCMonitorInterface.measurement_delay)
    def read_measurement_delay(self) -> Quantity:
        with self.world.lock:
            return Quantity(
                self.world.dc_source(self.instrument_id).measurement_delay_s, "s"
            )

    @write(DCMonitorInterface.measurement_delay)
    def write_measurement_delay(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.dc_source(
                self.instrument_id
            ).measurement_delay_s = quantity_value(value, "s")

    @implements(DCSourceInterface.source_voltage)
    def source_voltage(
        self, *, range: Quantity, level: Quantity
    ) -> DriverOutcome[None]:
        try:
            with self.world.lock:
                source = self.world.dc_source(self.instrument_id)
                source.source_mode = "voltage"
                source.voltage_range_v = quantity_value(range, "V")
                source.voltage_level_v = quantity_value(level, "V")
            return DriverSuccess(None)
        except Exception as error:
            return invoke_unknown(self.instrument_id, error)

    @implements(DCSourceInterface.source_current)
    def source_current(
        self, *, range: Quantity, level: Quantity
    ) -> DriverOutcome[None]:
        try:
            with self.world.lock:
                source = self.world.dc_source(self.instrument_id)
                source.source_mode = "current"
                source.current_range_a = quantity_value(range, "A")
                source.current_level_a = quantity_value(level, "A")
            return DriverSuccess(None)
        except Exception as error:
            return invoke_unknown(self.instrument_id, error)

    @implements(DCMonitorInterface.measure_current)
    def measure_current(self) -> DriverOutcome[DCMonitorCurrentObservation]:
        outcome = self._measure_monitor(expected_mode="voltage")
        if not isinstance(outcome, DriverSuccess):
            return outcome
        return DriverSuccess(
            DCMonitorCurrentObservation(
                current=outcome.value,
                evidence={"mode": "virtual", "world_seed": self.world.seed},
            )
        )

    @implements(DCMonitorInterface.measure_voltage)
    def measure_voltage(self) -> DriverOutcome[DCMonitorVoltageObservation]:
        outcome = self._measure_monitor(expected_mode="current")
        if not isinstance(outcome, DriverSuccess):
            return outcome
        return DriverSuccess(
            DCMonitorVoltageObservation(
                voltage=outcome.value,
                evidence={"mode": "virtual", "world_seed": self.world.seed},
            )
        )

    def _measure_monitor(
        self, *, expected_mode: Literal["voltage", "current"]
    ) -> DriverOutcome[MeasurementScalar]:
        with self.world.lock:
            source = self.world.dc_source(self.instrument_id)
            if not source.output_enabled:
                return _state_rejection(
                    "virtual_dc_monitor_output_disabled",
                    "DC source output is disabled",
                    DC_SOURCE_OUTPUT_ENABLED,
                )
            if not source.measurement_enabled:
                return _state_rejection(
                    "virtual_dc_monitor_disabled",
                    "DC monitor measurement is disabled",
                    DC_MONITOR_MEASUREMENT_ENABLED,
                )
            if source.source_mode != expected_mode:
                return _state_rejection(
                    "virtual_dc_monitor_source_mode_mismatch",
                    f"measurement requires {expected_mode} source mode",
                    DC_SOURCE_MODE,
                )
            measured = (
                MeasurementScalar.create(
                    dtype="float64", unit="A", value=source.voltage_level_v / 1e3
                )
                if expected_mode == "voltage"
                else MeasurementScalar.create(
                    dtype="float64", unit="V", value=source.current_level_a * 1e3
                )
            )
        return DriverSuccess(measured)

    def set_source_mode(self, mode: Literal["voltage", "current"]) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).source_mode = mode

    def set_voltage_range(self, value_v: float) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).voltage_range_v = value_v

    def set_current_range(self, value_a: float) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).current_range_a = value_a

    def set_voltage_level(self, value_v: float) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).voltage_level_v = value_v

    def set_current_level(self, value_a: float) -> None:
        with self.world.lock:
            self.world.dc_source(self.instrument_id).current_level_a = value_a

    def set_voltage_protection(self, value_v: float) -> None:
        self.write_voltage_protection(Quantity(value_v, "V"))

    def set_current_protection(self, value_a: float) -> None:
        self.write_current_protection(Quantity(value_a, "A"))

    def set_output(self, enabled: bool) -> None:
        self.write_output_enabled(enabled)

    def set_measurement_enabled(self, enabled: bool) -> None:
        self.write_measurement_enabled(enabled)

    def set_integration_cycles(self, cycles: int) -> None:
        self.write_integration_cycles(cycles)

    def set_measurement_delay(self, delay_s: float) -> None:
        self.write_measurement_delay(Quantity(delay_s, "s"))


@dataclass(frozen=True)
class _VirtualTemperatureSample:
    scan_channel: int
    autoscan_enabled: bool
    temperature_k: float
    resistance_ohm: float


@instrument_driver(
    VIRTUAL_TEMPERATURE_MONITOR_DRIVER.id,
    VIRTUAL_TEMPERATURE_MONITOR_DRIVER.implementation_version,
    interfaces=(TemperatureReadoutInterface,),
    label="Virtual temperature monitor",
)
class VirtualTemperatureMonitor(_VirtualDriver):
    def __init__(self, instrument_id: str, world: VirtualLabWorld) -> None:
        self.instrument_id = instrument_id
        self.world = world
        self.world.temperature_monitor(instrument_id)

    @read(TemperatureReadoutInterface.scan_channel)
    def read_scan_channel(self) -> int:
        with self.world.lock:
            return self.world.temperature_monitor(self.instrument_id).scan_channel

    @read(TemperatureReadoutInterface.autoscan_enabled)
    def read_autoscan_enabled(self) -> bool:
        with self.world.lock:
            return self.world.temperature_monitor(self.instrument_id).autoscan_enabled

    @implements(TemperatureReadoutInterface.sample)
    def sample(self) -> DriverOutcome[TemperatureSampleObservation]:
        sample = self.read_sample()
        return DriverSuccess(
            TemperatureSampleObservation(
                temperature=MeasurementScalar.create(
                    dtype="float64", unit="K", value=sample.temperature_k
                ),
                resistance=MeasurementScalar.create(
                    dtype="float64", unit="Ohm", value=sample.resistance_ohm
                ),
                evidence={
                    "mode": "virtual",
                    "world_seed": self.world.seed,
                    "scan_channel": sample.scan_channel,
                    "autoscan_enabled": sample.autoscan_enabled,
                    "reading_status": 0,
                },
            )
        )

    def read_sample(self) -> _VirtualTemperatureSample:
        with self.world.lock:
            state = self.world.temperature_monitor(self.instrument_id)
            return _VirtualTemperatureSample(
                scan_channel=state.scan_channel,
                autoscan_enabled=state.autoscan_enabled,
                temperature_k=self.world.temperature_k(),
                resistance_ohm=self.world.sensor_resistance_ohm(),
            )


@instrument_driver(
    VIRTUAL_VNA_DRIVER.id,
    VIRTUAL_VNA_DRIVER.implementation_version,
    interfaces=(NetworkSweepInterface, ExistingNetworkTraceInterface),
    label="Virtual VNA",
)
class VirtualNetworkAnalyzer(_VirtualDriver):
    def __init__(self, instrument_id: str, world: VirtualLabWorld) -> None:
        self.instrument_id = instrument_id
        self.world = world
        self.world.vna(instrument_id)

    @query(
        NetworkSweepInterface.start_frequency,
        NetworkSweepInterface.stop_frequency,
        NetworkSweepInterface.points,
        NetworkSweepInterface.if_bandwidth,
        NetworkSweepInterface.source_power,
        NetworkSweepInterface.s_parameter,
    )
    def query_sweep_settings(
        self,
    ) -> tuple[Quantity, Quantity, int, Quantity, Quantity, SParameter]:
        settings = self.sweep_settings()
        return (
            Quantity(settings.start_frequency_hz, "Hz"),
            Quantity(settings.stop_frequency_hz, "Hz"),
            settings.points,
            Quantity(settings.if_bandwidth_hz, "Hz"),
            Quantity(settings.source_power_dbm, "dBm"),
            settings.s_parameter,
        )

    @write(NetworkSweepInterface.start_frequency)
    def write_start_frequency(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.vna(self.instrument_id).start_frequency_hz = quantity_value(
                value, "Hz"
            )

    @write(NetworkSweepInterface.stop_frequency)
    def write_stop_frequency(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.vna(self.instrument_id).stop_frequency_hz = quantity_value(
                value, "Hz"
            )

    @write(NetworkSweepInterface.points)
    def write_points(self, value: int) -> None:
        with self.world.lock:
            self.world.vna(self.instrument_id).points = value

    @write(NetworkSweepInterface.if_bandwidth)
    def write_if_bandwidth(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.vna(self.instrument_id).if_bandwidth_hz = quantity_value(
                value, "Hz"
            )

    @write(NetworkSweepInterface.source_power)
    def write_source_power(self, value: Quantity) -> None:
        with self.world.lock:
            self.world.vna(self.instrument_id).source_power_dbm = quantity_value(
                value, "dBm"
            )

    @write(NetworkSweepInterface.s_parameter)
    def write_s_parameter(self, value: SParameter) -> None:
        with self.world.lock:
            self.world.vna(self.instrument_id).s_parameter = value

    @implements(NetworkSweepInterface.sweep)
    def sweep(self) -> DriverOutcome[NetworkSweepObservation]:
        with self.world.lock:
            trace = self.acquire_trace()
            return DriverSuccess(
                NetworkSweepObservation(
                    frequency=MeasurementArray.create(
                        dtype="float64",
                        unit="Hz",
                        values=np.asarray(trace.frequencies_hz, dtype=np.float64),
                    ),
                    s_parameter=MeasurementArray.create(
                        dtype="complex128",
                        unit="ratio",
                        values=np.asarray(trace.values, dtype=np.complex128),
                    ),
                    evidence=deepcopy(
                        self.world.vna(self.instrument_id).trace_evidence
                    ),
                )
            )

    @implements(ExistingNetworkTraceInterface.read_trace)
    def read_trace(self) -> DriverOutcome[NetworkSweepObservation]:
        with self.world.lock:
            state = self.world.vna(self.instrument_id)
            trace = state.retained_trace
            if trace is None:
                return DriverRejected(
                    problems=(
                        execution_problem(
                            "no_retained_trace",
                            "No existing trace; explicitly acquire a new sweep first.",
                            "acquisition",
                        ),
                    )
                )
            return DriverSuccess(
                NetworkSweepObservation(
                    frequency=MeasurementArray.create(
                        dtype="float64",
                        unit="Hz",
                        values=np.asarray(trace.frequencies_hz, dtype=np.float64),
                    ),
                    s_parameter=MeasurementArray.create(
                        dtype="complex128",
                        unit="ratio",
                        values=np.asarray(trace.values, dtype=np.complex128),
                    ),
                    evidence=deepcopy(state.trace_evidence),
                )
            )

    def configure_linear_sweep(self, settings: LinearSweepSettings) -> None:
        if settings.start_frequency_hz >= settings.stop_frequency_hz:
            raise ValueError(
                "linear sweep start frequency must be below stop frequency"
            )
        if settings.points < 2:
            raise ValueError("linear sweep requires at least two points")
        with self.world.lock:
            state = self.world.vna(self.instrument_id)
            state.start_frequency_hz = settings.start_frequency_hz
            state.stop_frequency_hz = settings.stop_frequency_hz
            state.points = settings.points
            state.if_bandwidth_hz = settings.if_bandwidth_hz
            state.source_power_dbm = settings.source_power_dbm
            state.s_parameter = settings.s_parameter

    def sweep_settings(self) -> LinearSweepSettings:
        with self.world.lock:
            state = self.world.vna(self.instrument_id)
            return LinearSweepSettings(
                start_frequency_hz=state.start_frequency_hz,
                stop_frequency_hz=state.stop_frequency_hz,
                points=state.points,
                if_bandwidth_hz=state.if_bandwidth_hz,
                source_power_dbm=state.source_power_dbm,
                s_parameter=state.s_parameter,
            )

    def single_trigger(self) -> None:
        with self.world.lock:
            state = self.world.vna(self.instrument_id)
            state.retained_trace = self.world.network_trace(self.instrument_id)
            state.trace_evidence = {
                "mode": "virtual",
                "world_seed": self.world.seed,
                "acquired_at": datetime.now(UTC).isoformat(),
                "settings_source": "virtual_trigger_snapshot",
                "settings": {
                    "start_frequency_hz": state.start_frequency_hz,
                    "stop_frequency_hz": state.stop_frequency_hz,
                    "points": state.points,
                    "if_bandwidth_hz": state.if_bandwidth_hz,
                    "source_power_dbm": state.source_power_dbm,
                    "s_parameter": state.s_parameter,
                },
            }

    def read_trace_ascii(self) -> NetworkTrace:
        with self.world.lock:
            trace = self.world.vna(self.instrument_id).retained_trace
            if trace is None:
                raise ValueError("No retained trace; trigger a sweep explicitly")
            return trace

    def acquire_trace(self) -> NetworkTrace:
        self.single_trigger()
        return self.read_trace_ascii()


def _state_rejection(code: str, message: str, target: object) -> DriverRejected:
    return DriverRejected(
        problems=(state_property_problem(code, message, target),)  # pyright: ignore[reportArgumentType]
    )


__all__ = [
    "VirtualDcSource",
    "VirtualNetworkAnalyzer",
    "VirtualRfSource",
    "VirtualTemperatureMonitor",
]
