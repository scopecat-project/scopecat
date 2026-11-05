"""Shared state and lightweight physics for virtual laboratory devices."""

from __future__ import annotations

import cmath
import math
import random
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

from pydantic import JsonValue

from scopecat_instruments._support import NetworkTrace
from scopecat_instruments.interface_declarations import ReferenceSource, SParameter


@dataclass
class VirtualRfSourceState:
    frequency_hz: float = 5.0e9
    power_dbm: float = -30.0
    output_enabled: bool = False
    reference_source: ReferenceSource = "internal"


@dataclass
class VirtualDcSourceState:
    source_mode: Literal["voltage", "current"] = "voltage"
    voltage_range_v: float = 1.0
    current_range_a: float = 0.01
    voltage_level_v: float = 0.0
    current_level_a: float = 0.0
    voltage_protection_v: float = 10.0
    current_protection_a: float = 0.01
    output_enabled: bool = False
    measurement_enabled: bool = True
    integration_cycles: int = 1
    measurement_delay_s: float = 0.0


@dataclass
class VirtualTemperatureState:
    scan_channel: int = 1
    autoscan_enabled: bool = False
    heater_output: float = 0.0
    heater_range: int = 0


@dataclass
class VirtualVnaState:
    retained_trace: NetworkTrace | None = None
    trace_evidence: dict[str, JsonValue] = field(default_factory=dict)
    start_frequency_hz: float = 4.8e9
    stop_frequency_hz: float = 5.2e9
    points: int = 201
    if_bandwidth_hz: float = 1.0e3
    source_power_dbm: float = -30.0
    s_parameter: SParameter = "S21"


class VirtualLabWorld:
    """Device state shared across driver instances, with deterministic traces."""

    def __init__(
        self,
        *,
        seed: int = 0,
        base_temperature_k: float = 0.02,
        base_resonance_hz: float = 5.0e9,
        base_linewidth_hz: float = 1.0e6,
    ) -> None:
        self.seed = seed
        self.base_temperature_k = base_temperature_k
        self._reference_temperature_k = base_temperature_k
        self.base_resonance_hz = base_resonance_hz
        self.base_linewidth_hz = base_linewidth_hz
        self.lock = RLock()
        # This RNG models repeatable noise; it is not used for security.
        self._random = random.Random(seed)  # noqa: S311
        self._rf_sources: dict[str, VirtualRfSourceState] = {}
        self._dc_sources: dict[str, VirtualDcSourceState] = {}
        self._temperature_monitors: dict[str, VirtualTemperatureState] = {}
        self._vnas: dict[str, VirtualVnaState] = {}

    def rf_source(self, instrument_id: str) -> VirtualRfSourceState:
        with self.lock:
            return self._rf_sources.setdefault(instrument_id, VirtualRfSourceState())

    def dc_source(self, instrument_id: str) -> VirtualDcSourceState:
        with self.lock:
            return self._dc_sources.setdefault(instrument_id, VirtualDcSourceState())

    def temperature_monitor(self, instrument_id: str) -> VirtualTemperatureState:
        with self.lock:
            return self._temperature_monitors.setdefault(
                instrument_id,
                VirtualTemperatureState(),
            )

    def vna(self, instrument_id: str) -> VirtualVnaState:
        with self.lock:
            return self._vnas.setdefault(instrument_id, VirtualVnaState())

    def set_base_temperature(self, temperature_k: float) -> None:
        if temperature_k <= 0:
            raise ValueError("virtual lab temperature must be positive")
        with self.lock:
            self.base_temperature_k = temperature_k

    def flux_bias(self) -> float:
        with self.lock:
            total = 0.0
            for source in self._dc_sources.values():
                if not source.output_enabled:
                    continue
                if source.source_mode == "voltage":
                    total += source.voltage_level_v / 0.25
                else:
                    total += source.current_level_a / 0.002
            return total

    def temperature_k(self) -> float:
        with self.lock:
            dc_heating = 0.0
            for source in self._dc_sources.values():
                if not source.output_enabled:
                    continue
                level = (
                    source.voltage_level_v
                    if source.source_mode == "voltage"
                    else source.current_level_a * 100.0
                )
                dc_heating += 2.0e-3 * level * level
            rf_heating = sum(
                2.0e-4 * 10 ** (source.power_dbm / 10.0)
                for source in self._rf_sources.values()
                if source.output_enabled
            )
            heater_heating = sum(
                max(0.0, monitor.heater_output) * 1.0e-5
                for monitor in self._temperature_monitors.values()
                if monitor.heater_range > 0
            )
            return self.base_temperature_k + dc_heating + rf_heating + heater_heating

    def sensor_resistance_ohm(self) -> float:
        temperature = self.temperature_k()
        return 1.0e3 * math.pow(0.1 / temperature, 1.2)

    def network_trace(self, instrument_id: str) -> NetworkTrace:
        with self.lock:
            state = self.vna(instrument_id)
            step = (state.stop_frequency_hz - state.start_frequency_hz) / (
                state.points - 1
            )
            frequencies = tuple(
                state.start_frequency_hz + index * step for index in range(state.points)
            )
            flux = self.flux_bias()
            temperature = self.temperature_k()
            resonance = self.base_resonance_hz + 60.0e6 * math.cos(math.pi * flux)
            excess_temperature = max(
                0.0,
                temperature - self._reference_temperature_k,
            )
            linewidth = self.base_linewidth_hz * (1.0 + 80.0 * excess_temperature)
            depth = 0.72 / (1.0 + 30.0 * excess_temperature)
            noise_scale = 2.0e-4 * math.sqrt(max(state.if_bandwidth_hz, 1.0) / 1.0e3)
            values = tuple(
                self._network_value(
                    frequency_hz=frequency,
                    resonance_hz=resonance,
                    linewidth_hz=linewidth,
                    depth=depth,
                    parameter=state.s_parameter,
                    noise_scale=noise_scale,
                )
                for frequency in frequencies
            )
            return NetworkTrace(frequencies_hz=frequencies, values=values)

    def _network_value(
        self,
        *,
        frequency_hz: float,
        resonance_hz: float,
        linewidth_hz: float,
        depth: float,
        parameter: SParameter,
        noise_scale: float,
    ) -> complex:
        detuning = 2.0 * (frequency_hz - resonance_hz) / linewidth_hz
        resonant = depth / complex(1.0, detuning)
        cable_phase = cmath.exp(complex(0.0, -2.0 * math.pi * frequency_hz * 0.35e-9))
        if parameter in {"S21", "S12"}:
            ideal = (1.0 - resonant) * cable_phase
        else:
            ideal = 0.45 * resonant * cable_phase
        noise = complex(
            self._random.gauss(0.0, noise_scale),
            self._random.gauss(0.0, noise_scale),
        )
        return ideal + noise


__all__ = [
    "VirtualDcSourceState",
    "VirtualLabWorld",
    "VirtualRfSourceState",
    "VirtualTemperatureState",
    "VirtualVnaState",
]
