"""Typed Python declarations for first-party instrument capabilities."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from scopecat.kernel.quantity import Quantity
from scopecat.sdk.instruments.declarations import (
    Member,
    acquisition,
    argument,
    array_result,
    axis,
    instrument_interface,
    linear_coordinates,
    member,
    observation,
    operation,
    result_schema,
    scalar_result,
)

type ReferenceSource = Literal["internal", "external"]
type SParameter = Literal["S11", "S21", "S12", "S22"]


@instrument_interface(
    "scopecat.dc_bias/v1",
    label="DC bias ramp",
    description="Settled voltage transitions applied as one coherent batch.",
)
class DCBiasInterface(Protocol):
    target_voltage: Member[Quantity] = member(
        access="read_write", restore=True, unit="V", label="Target voltage"
    )
    ramp_duration: Member[Quantity] = member(
        access="read_write",
        restore=True,
        unit="s",
        minimum=0.0,
        label="Ramp duration",
    )
    settle_tolerance: Member[Quantity] = member(
        access="read_write",
        restore=True,
        unit="V",
        minimum=0.0,
        label="Settle tolerance",
    )
    actual_voltage: Member[Quantity] = member(
        access="read_only", unit="V", label="Actual voltage"
    )
    settled: Member[bool] = member(access="read_only", label="Settled")

    readback = observation(actual_voltage, settled, label="Read back bias")


@instrument_interface(
    "scopecat.dc_source/v3",
    label="DC source",
    description="DC source transitions, protection, and output control.",
)
class DCSourceInterface(Protocol):
    voltage_protection: Member[Quantity] = member(
        access="read_write", restore=True, unit="V", label="Voltage protection"
    )
    current_protection: Member[Quantity] = member(
        access="read_write", restore=True, unit="A", label="Current protection"
    )
    output_enabled: Member[bool] = member(
        access="read_write", restore=True, label="DC output"
    )
    source_mode: Member[Literal["voltage", "current"]] = member(
        access="read_only", label="Source mode"
    )

    @operation(label="Source voltage")
    def source_voltage(
        self,
        *,
        range: Annotated[Quantity, argument(unit="V")],
        level: Annotated[Quantity, argument(unit="V")],
    ) -> None: ...

    @operation(label="Source current")
    def source_current(
        self,
        *,
        range: Annotated[Quantity, argument(unit="A")],
        level: Annotated[Quantity, argument(unit="A")],
    ) -> None: ...


@result_schema
class DCMonitorCurrentResults:
    current = scalar_result(id="monitored_current", dtype="float64", unit="A")


@result_schema
class DCMonitorVoltageResults:
    voltage = scalar_result(id="monitored_voltage", dtype="float64", unit="V")


@instrument_interface(
    "scopecat.dc_monitor/v4",
    label="DC monitor",
    description="Independent current and voltage measurements for a DC source.",
)
class DCMonitorInterface(Protocol):
    measurement_enabled: Member[bool] = member(
        access="read_write", restore=True, label="Measurement"
    )
    integration_cycles: Member[int] = member(
        access="read_write", restore=True, minimum=1, label="Integration cycles"
    )
    measurement_delay: Member[Quantity] = member(
        access="read_write",
        restore=True,
        unit="s",
        minimum=0.0,
        label="Measurement delay",
    )

    @acquisition(results=DCMonitorCurrentResults, label="Measure current")
    def measure_current(self) -> None: ...

    @acquisition(results=DCMonitorVoltageResults, label="Measure voltage")
    def measure_voltage(self) -> None: ...


@result_schema
class TemperatureSampleResults:
    temperature = scalar_result(dtype="float64", unit="K")
    resistance = scalar_result(dtype="float64", unit="Ohm")


@instrument_interface(
    "scopecat.temperature_readout/v1",
    label="Temperature readout",
    description="Read-only scanner state and settled sensor acquisition.",
)
class TemperatureReadoutInterface(Protocol):
    scan_channel: Member[int] = member(
        access="read_only", minimum=1, label="Scan channel"
    )
    autoscan_enabled: Member[bool] = member(access="read_only", label="Autoscan")

    @acquisition(results=TemperatureSampleResults, label="Sample sensor")
    def sample(self) -> None: ...


@instrument_interface(
    "scopecat.rf_output/v2",
    label="RF output",
    description="Continuous-wave RF source controls independent of vendor syntax.",
)
class RFOutputInterface(Protocol):
    frequency: Member[Quantity] = member(
        access="read_write", restore=True, unit="Hz", label="CW frequency"
    )
    power: Member[Quantity] = member(
        access="read_write", restore=True, unit="dBm", label="Output power"
    )
    output_enabled: Member[bool] = member(
        access="read_write", restore=True, label="RF output"
    )


@instrument_interface(
    "scopecat.reference_clock/v1",
    label="Reference clock selection",
    description="Selection of an internal or external instrument reference clock.",
)
class ReferenceClockInterface(Protocol):
    reference_source: Member[ReferenceSource] = member(
        access="read_write", restore=True, label="Reference source"
    )


@result_schema
class NetworkSweepResults:
    frequency = array_result(
        dtype="float64", role="coordinate", unit="Hz", axes=("frequency",)
    )
    s_parameter = array_result(dtype="complex128", unit="ratio", axes=("frequency",))


@instrument_interface(
    "scopecat.network_sweep/v1",
    label="Network sweep",
    description="Linear, single-trigger complex S-parameter sweep.",
)
class NetworkSweepInterface(Protocol):
    start_frequency: Member[Quantity] = member(
        access="read_write", restore=True, unit="Hz", label="Start frequency"
    )
    stop_frequency: Member[Quantity] = member(
        access="read_write", restore=True, unit="Hz", label="Stop frequency"
    )
    points: Member[int] = member(
        access="read_write", restore=True, minimum=2, label="Sweep points"
    )
    if_bandwidth: Member[Quantity] = member(
        access="read_write", restore=True, unit="Hz", label="IF bandwidth"
    )
    source_power: Member[Quantity] = member(
        access="read_write", restore=True, unit="dBm", label="Source power"
    )
    s_parameter: Member[SParameter] = member(
        access="read_write", restore=True, label="S-parameter"
    )

    @acquisition(
        results=NetworkSweepResults,
        label="Acquire sweep",
        axes={
            "frequency": axis(
                size=points,
                kind="frequency",
                unit="Hz",
                coordinate_result="frequency",
                coordinates=linear_coordinates(
                    start=start_frequency,
                    stop=stop_frequency,
                ),
            )
        },
    )
    def sweep(self) -> None: ...


@instrument_interface(
    "scopecat.existing_network_trace/v1",
    label="Existing network trace",
    description=(
        "Read the retained complex trace without triggering or configuring a sweep. "
        "Coordinates and acquisition evidence belong to the retained trace, "
        "not current settings."
    ),
)
class ExistingNetworkTraceInterface(Protocol):
    @acquisition(
        results=NetworkSweepResults,
        label="Read existing trace (no trigger)",
        description="Read a retained trace without triggering a new sweep. "
        "Current settings may differ from the trace generation settings.",
        axes={
            "frequency": axis(
                kind="frequency", unit="Hz", coordinate_result="frequency"
            )
        },
    )
    def read_trace(self) -> None: ...


__all__ = [
    "DCBiasInterface",
    "DCMonitorCurrentResults",
    "DCMonitorInterface",
    "DCMonitorVoltageResults",
    "DCSourceInterface",
    "ExistingNetworkTraceInterface",
    "NetworkSweepInterface",
    "NetworkSweepResults",
    "RFOutputInterface",
    "ReferenceClockInterface",
    "ReferenceSource",
    "SParameter",
    "TemperatureReadoutInterface",
    "TemperatureSampleResults",
]
