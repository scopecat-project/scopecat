"""Lab-owned XY-drive composition over an external LO and IQ AWG."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Literal, cast

import scopecat as sc
from scopecat.authoring import (
    CapabilityResource,
    EachEntity,
    ExperimentContext,
    ModuleContext,
    PerEntity,
    StateTarget,
    Symbolic,
    ValueRef,
    capability_resource,
    ensure_state_targets,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity
from scopecat.sdk.instruments import InstrumentCapabilityRef
from scopecat_instruments import RFSourceTarget, rf_source
from scopecat_instruments.members import (
    REFERENCE_CLOCK_REFERENCE_SOURCE,
    RF_OUTPUT_ENABLED,
    RF_OUTPUT_FREQUENCY,
    RF_OUTPUT_POWER,
)

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
    ANALOG_WAVEFORM_OUTPUT_ENABLED,
    ANALOG_WAVEFORM_OUTPUT_OFFSET,
    ANALOG_WAVEFORM_OUTPUT_PLAY,
    ANALOG_WAVEFORM_OUTPUT_WAVEFORM,
    AWG_RUN_MODE,
    AWG_SAMPLE_RATE,
)
from reference_lab.interfaces import (
    CLOCK_TIMING_FREQUENCY,
)
from reference_lab.payloads import SAMPLED_WAVEFORM_SCHEMA_ID

type SampledWaveform = Annotated[
    dict[str, object],
    sc.ScalarType(sc.PayloadType(SAMPLED_WAVEFORM_SCHEMA_ID)),
]

Q0 = EntityRef(id="q0", kind="logical_qubit")
Q1 = EntityRef(id="q1", kind="logical_qubit")
XY_QUBITS = sc.each(Q0, Q1)
DEFAULT_LO_POWER = sc.Quantity(-10.0, "dBm")
DEFAULT_REFERENCE_FREQUENCY = sc.Quantity(10.0, "MHz")
AWG_SAMPLE_CLOCK = sc.Quantity(1.0, "GHz")
DEFAULT_AWG_AMPLITUDE = sc.Quantity(200.0, "mV")
XY_WAVEFORM_SAMPLES = 40


@dataclass(frozen=True, slots=True)
class XYDriveFrequencies:
    """Requested frequencies selected by one facade configuration.

    These values express author intent. Instrument state snapshots and pulse
    receipts separately report the programmed IF and readiness confirmation.
    """

    requested_lo_frequency: PerEntity[Symbolic[Quantity]]
    requested_if_frequency: PerEntity[Symbolic[Quantity]]
    requested_carrier_frequency: PerEntity[Symbolic[Quantity]]


class XYDriveGroup:
    """Compose shared LO/clock state with independently mounted IQ drives.

    The facade accepts signed IF directly and returns the derived carrier using
    ``RF = LO + IF``. Routing coalesces equal demands on shared LO and AWG clock
    owners while preserving independent I/Q output mounts. This lab-local
    composition keeps ordinary carrier-driven experiments simple and gives the
    few fixed-IF LO scans an explicit host schedule.
    """

    __slots__ = ("_context", "_entities", "_i", "_lo", "_q")

    def __init__(
        self,
        context: ExperimentContext | ModuleContext,
        *,
        for_: EachEntity,
    ) -> None:
        self._context = context
        self._entities = for_
        self._lo = rf_source(
            context,
            name="xy_drive.lo",
            requires=(
                RF_OUTPUT_FREQUENCY,
                RF_OUTPUT_POWER,
                RF_OUTPUT_ENABLED,
                REFERENCE_CLOCK_REFERENCE_SOURCE,
            ),
            for_=self._entities,
            role="drive-lo",
        )
        awg_capabilities = (
            AWG_SAMPLE_RATE,
            AWG_RUN_MODE,
            ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
            ANALOG_WAVEFORM_OUTPUT_OFFSET,
            ANALOG_WAVEFORM_OUTPUT_ENABLED,
            REFERENCE_CLOCK_REFERENCE_SOURCE,
            CLOCK_TIMING_FREQUENCY,
            ANALOG_WAVEFORM_OUTPUT_PLAY,
        )
        self._i = self._resources(
            context,
            "xy_drive.i",
            awg_capabilities,
            role="drive-i",
        )
        self._q = self._resources(
            context,
            "xy_drive.q",
            awg_capabilities,
            role="drive-q",
        )

    def ensure(
        self,
        *,
        lo_frequency: Symbolic[Quantity] | PerEntity[Symbolic[Quantity]],
        if_frequency: Symbolic[Quantity] | PerEntity[Symbolic[Quantity]],
        lo_power: Symbolic[Quantity] = DEFAULT_LO_POWER,
        amplitude: Symbolic[Quantity]
        | PerEntity[Symbolic[Quantity]] = DEFAULT_AWG_AMPLITUDE,
        output_enabled: Symbolic[bool] = True,
        reference_source: Literal["internal", "external"] = "external",
        reference_frequency: Symbolic[Quantity] = DEFAULT_REFERENCE_FREQUENCY,
    ) -> XYDriveFrequencies:
        """Apply one coherent target and return automatically converted carriers."""

        lo_by_entity = self._entities.align(lo_frequency)
        if_by_entity = self._entities.align(if_frequency)
        amplitude_by_entity = self._entities.align(amplitude)
        targets: list[StateTarget] = []
        for entity in self._entities:
            targets.extend(
                self._lo[entity].state_targets(
                    RFSourceTarget(
                        frequency=lo_by_entity[entity],
                        power=lo_power,
                        output_enabled=output_enabled,
                        reference_source=reference_source,
                    )
                )
            )
            targets.extend(
                (
                    self._i[entity].state_target(
                        {
                            AWG_SAMPLE_RATE: AWG_SAMPLE_CLOCK,
                            AWG_RUN_MODE: "once",
                            ANALOG_WAVEFORM_OUTPUT_AMPLITUDE: amplitude_by_entity[
                                entity
                            ],
                            ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.Quantity(0.0, "V"),
                            ANALOG_WAVEFORM_OUTPUT_ENABLED: output_enabled,
                            REFERENCE_CLOCK_REFERENCE_SOURCE: reference_source,
                            CLOCK_TIMING_FREQUENCY: reference_frequency,
                        },
                    ),
                    self._q[entity].state_target(
                        {
                            AWG_SAMPLE_RATE: AWG_SAMPLE_CLOCK,
                            AWG_RUN_MODE: "once",
                            ANALOG_WAVEFORM_OUTPUT_AMPLITUDE: amplitude_by_entity[
                                entity
                            ],
                            ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.Quantity(0.0, "V"),
                            ANALOG_WAVEFORM_OUTPUT_ENABLED: output_enabled,
                            REFERENCE_CLOCK_REFERENCE_SOURCE: reference_source,
                            CLOCK_TIMING_FREQUENCY: reference_frequency,
                        },
                    ),
                )
            )
        ensure_state_targets(self._context, targets)
        return XYDriveFrequencies(
            requested_lo_frequency=lo_by_entity,
            requested_if_frequency=if_by_entity,
            requested_carrier_frequency=PerEntity(
                (
                    entity,
                    _carrier_frequency(
                        lo_by_entity[entity],
                        if_by_entity[entity],
                    ),
                )
                for entity in self._entities
            ),
        )

    def play(
        self,
        if_frequency: Symbolic[Quantity] | PerEntity[Symbolic[Quantity]],
    ) -> None:
        """Render signed-IF I/Q samples and play both physical DAC outputs."""

        frequencies = self._entities.align(if_frequency)
        for entity in self._entities:
            i_waveform = self._context.compute(
                f"xy_i_waveform_{entity.id}",
                fn=_i_waveform,
                if_frequency=frequencies[entity],
            )
            q_waveform = self._context.compute(
                f"xy_q_waveform_{entity.id}",
                fn=_q_waveform,
                if_frequency=frequencies[entity],
            )
            self._i[entity].invoke(
                ANALOG_WAVEFORM_OUTPUT_PLAY,
                arguments={
                    ANALOG_WAVEFORM_OUTPUT_WAVEFORM: cast(
                        "ValueRef[PayloadValue]", i_waveform
                    )
                },
            )
            self._q[entity].invoke(
                ANALOG_WAVEFORM_OUTPUT_PLAY,
                arguments={
                    ANALOG_WAVEFORM_OUTPUT_WAVEFORM: cast(
                        "ValueRef[PayloadValue]", q_waveform
                    )
                },
            )

    def _resources(
        self,
        context: ExperimentContext | ModuleContext,
        name: str,
        capabilities: tuple[InstrumentCapabilityRef, ...],
        role: str | None = None,
    ) -> PerEntity[CapabilityResource]:
        return capability_resource(
            context,
            name,
            requires=capabilities,
            for_=self._entities,
            role=role,
        )


def _carrier_frequency(
    lo_frequency: Symbolic[Quantity],
    if_frequency: Symbolic[Quantity],
) -> Symbolic[Quantity]:
    if isinstance(lo_frequency, ValueRef):
        return lo_frequency + if_frequency
    if isinstance(if_frequency, ValueRef):
        return if_frequency + lo_frequency
    return lo_frequency + if_frequency


def xy_drive(
    context: ExperimentContext | ModuleContext,
    *,
    for_: EachEntity,
) -> XYDriveGroup:
    """Create the lab's compact XY-drive authoring facade."""

    return XYDriveGroup(context, for_=for_)


@dataclass(frozen=True, slots=True)
class XYLoSweepDataset:
    requested_lo_frequency: sc.CoordinateRef[Quantity]
    requested_carrier_frequency: PerEntity[sc.ValueRef[Quantity]]


def _i_waveform(if_frequency: Quantity) -> SampledWaveform:
    return _sampled_if_waveform(if_frequency, quadrature=False)


def _q_waveform(if_frequency: Quantity) -> SampledWaveform:
    return _sampled_if_waveform(if_frequency, quadrature=True)


def _sampled_if_waveform(
    if_frequency: Quantity,
    *,
    quadrature: bool,
) -> dict[str, object]:
    frequency_hz = if_frequency.to("Hz").value
    sample_rate_hz = AWG_SAMPLE_CLOCK.to("Hz").value
    phase_offset = -math.pi / 2 if quadrature else 0.0
    return {
        "samples": [
            math.cos(
                2.0 * math.pi * frequency_hz * sample_index / sample_rate_hz
                + phase_offset
            )
            for sample_index in range(XY_WAVEFORM_SAMPLES)
        ]
    }


@sc.experiment(id="reference_lab.xy_lo_sweep")
def xy_lo_sweep(experiment: sc.ExperimentContext) -> XYLoSweepDataset:
    """Sweep the shared LO at fixed signed IF and record requested carriers."""

    lo_frequency = experiment.scan(
        "lo_frequency",
        start=sc.Quantity(4.90, "GHz"),
        stop=sc.Quantity(4.92, "GHz"),
        points=3,
    )
    drives = xy_drive(experiment, for_=XY_QUBITS)
    frequencies = drives.ensure(
        lo_frequency=lo_frequency,
        if_frequency=PerEntity(
            (
                (Q0, sc.Quantity(100.0, "MHz")),
                (Q1, sc.Quantity(-100.0, "MHz")),
            )
        ),
    )
    drives.play(frequencies.requested_if_frequency)
    return XYLoSweepDataset(
        requested_lo_frequency=lo_frequency,
        requested_carrier_frequency=PerEntity(
            (
                entity,
                cast(
                    "ValueRef[Quantity]",
                    frequencies.requested_carrier_frequency[entity],
                ),
            )
            for entity in XY_QUBITS
        ),
    )


XY_LO_SWEEP = xy_lo_sweep.build()


__all__ = [
    "DEFAULT_LO_POWER",
    "DEFAULT_REFERENCE_FREQUENCY",
    "Q0",
    "Q1",
    "XY_LO_SWEEP",
    "XY_QUBITS",
    "XYDriveFrequencies",
    "XYDriveGroup",
    "XYLoSweepDataset",
    "xy_drive",
    "xy_lo_sweep",
]
