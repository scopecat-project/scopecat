"""Entityless bench capture of one physical AWG output."""

# Product declaration remains a low-level lab-facade concern; resource state,
# operations, and acquisition use the public capability composition boundary.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

import scopecat as sc
from scopecat.kernel.payloads import PayloadValue
from scopecat.program.measurement_types import MeasurementArrayData
from scopecat.program.products import product_axis

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
    ANALOG_WAVEFORM_OUTPUT_ENABLED,
    ANALOG_WAVEFORM_OUTPUT_OFFSET,
    ANALOG_WAVEFORM_OUTPUT_PLAY,
    ANALOG_WAVEFORM_OUTPUT_WAVEFORM,
    AWG_RUN_MODE,
    AWG_SAMPLE_RATE,
    OSCILLOSCOPE_ARM,
    OSCILLOSCOPE_BANDWIDTH_LIMIT,
    OSCILLOSCOPE_COUPLING,
    OSCILLOSCOPE_FETCH_TIME,
    OSCILLOSCOPE_FETCH_VOLTAGE,
    OSCILLOSCOPE_IMPEDANCE,
    OSCILLOSCOPE_INPUT_ENABLED,
    OSCILLOSCOPE_RECORD_LENGTH,
    OSCILLOSCOPE_SAMPLE_RATE,
    OSCILLOSCOPE_TRIGGER_LEVEL,
    OSCILLOSCOPE_TRIGGER_SOURCE,
    OSCILLOSCOPE_VERTICAL_OFFSET,
    OSCILLOSCOPE_VERTICAL_SCALE,
)
from reference_lab.payloads import SAMPLED_WAVEFORM_SCHEMA_ID

type SampledWaveform = Annotated[
    dict[str, object],
    sc.ScalarType(sc.PayloadType(SAMPLED_WAVEFORM_SCHEMA_ID)),
]

SAMPLE_RATE = sc.Quantity(1.0, "GHz")
RECORD_LENGTH = 16


@dataclass(frozen=True, slots=True)
class AwgOutputMonitorDataset:
    time: sc.ProductRef[MeasurementArrayData]
    voltage: sc.ProductRef[MeasurementArrayData]


def _diagnostic_waveform() -> SampledWaveform:
    return {
        "samples": [
            0.0,
            0.02,
            0.12,
            0.45,
            0.82,
            1.0,
            0.82,
            0.45,
            0.12,
            0.02,
            0.0,
            -0.08,
            -0.04,
            0.0,
            0.0,
            0.0,
        ]
    }


@sc.experiment(id="reference_lab.awg_output_monitor")
def awg_output_monitor(
    experiment: sc.ExperimentContext,
) -> AwgOutputMonitorDataset:
    """Capture one temporarily cabled AWG output with entityless resources.

    Both devices are known inventory; only their cable is temporary. The run
    name and description carry operator intent, routing identifies the physical
    source and scope input, and the dataset records only the scientific trace.
    An unregistered diagnostic scope instead belongs in a direct
    ``temporary_instrument`` session.
    """

    source = sc.capability_resource(
        experiment,
        "source",
        requires=(
            AWG_SAMPLE_RATE,
            AWG_RUN_MODE,
            ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
            ANALOG_WAVEFORM_OUTPUT_OFFSET,
            ANALOG_WAVEFORM_OUTPUT_ENABLED,
            ANALOG_WAVEFORM_OUTPUT_PLAY,
        ),
    )
    monitor = sc.capability_resource(
        experiment,
        "monitor",
        requires=(
            OSCILLOSCOPE_SAMPLE_RATE,
            OSCILLOSCOPE_RECORD_LENGTH,
            OSCILLOSCOPE_TRIGGER_SOURCE,
            OSCILLOSCOPE_TRIGGER_LEVEL,
            OSCILLOSCOPE_ARM,
            OSCILLOSCOPE_INPUT_ENABLED,
            OSCILLOSCOPE_VERTICAL_SCALE,
            OSCILLOSCOPE_VERTICAL_OFFSET,
            OSCILLOSCOPE_COUPLING,
            OSCILLOSCOPE_IMPEDANCE,
            OSCILLOSCOPE_BANDWIDTH_LIMIT,
            OSCILLOSCOPE_FETCH_TIME.acquisition,
        ),
    )
    sc.ensure_state_targets(
        experiment,
        (
            source.state_target(
                {
                    AWG_SAMPLE_RATE: SAMPLE_RATE,
                    AWG_RUN_MODE: "once",
                    ANALOG_WAVEFORM_OUTPUT_AMPLITUDE: sc.Quantity(250.0, "mV"),
                    ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.Quantity(0.0, "V"),
                    ANALOG_WAVEFORM_OUTPUT_ENABLED: True,
                }
            ),
            monitor.state_target(
                {
                    OSCILLOSCOPE_SAMPLE_RATE: SAMPLE_RATE,
                    OSCILLOSCOPE_RECORD_LENGTH: RECORD_LENGTH,
                    OSCILLOSCOPE_TRIGGER_SOURCE: "external",
                    OSCILLOSCOPE_TRIGGER_LEVEL: sc.Quantity(0.0, "V"),
                    OSCILLOSCOPE_INPUT_ENABLED: True,
                    OSCILLOSCOPE_VERTICAL_SCALE: sc.Quantity(100.0, "mV"),
                    OSCILLOSCOPE_VERTICAL_OFFSET: sc.Quantity(0.0, "V"),
                    OSCILLOSCOPE_COUPLING: "dc",
                    OSCILLOSCOPE_IMPEDANCE: "50_ohm",
                    OSCILLOSCOPE_BANDWIDTH_LIMIT: sc.Quantity(500.0, "MHz"),
                }
            ),
        ),
    )
    waveform = experiment.compute(
        "diagnostic_waveform",
        fn=_diagnostic_waveform,
    )

    monitor.invoke(OSCILLOSCOPE_ARM)
    source.invoke(
        ANALOG_WAVEFORM_OUTPUT_PLAY,
        arguments={
            ANALOG_WAVEFORM_OUTPUT_WAVEFORM: cast(
                "sc.ValueRef[PayloadValue]",
                waveform,
            )
        },
    )

    sample_axis = product_axis(
        "sample",
        size=RECORD_LENGTH,
        kind="time",
        unit="s",
    )
    time = experiment._product("time", unit="s", axes=(sample_axis,))
    voltage = experiment._product("voltage", unit="V", axes=(sample_axis,))
    monitor.acquire(
        {
            OSCILLOSCOPE_FETCH_TIME: time,
            OSCILLOSCOPE_FETCH_VOLTAGE: voltage,
        },
        id="fetch_trace",
    )
    return AwgOutputMonitorDataset(
        time=cast(
            "sc.ProductRef[MeasurementArrayData]",
            time,
        ),
        voltage=cast(
            "sc.ProductRef[MeasurementArrayData]",
            voltage,
        ),
    )


AWG_OUTPUT_MONITOR = awg_output_monitor.build()


__all__ = [
    "AWG_OUTPUT_MONITOR",
    "RECORD_LENGTH",
    "SAMPLE_RATE",
    "AwgOutputMonitorDataset",
    "awg_output_monitor",
]
