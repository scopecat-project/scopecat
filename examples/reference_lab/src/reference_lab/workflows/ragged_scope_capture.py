"""Variable-record-length capture on the shared AWG/scope bench."""

# Product declaration remains a low-level lab-facade concern.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

import scopecat as sc
from scopecat.kernel.payloads import PayloadValue
from scopecat.program.measurement_types import MeasurementArrayData
from scopecat.program.products import product_axis

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT,
    ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
    ANALOG_WAVEFORM_OUTPUT_ENABLED,
    ANALOG_WAVEFORM_OUTPUT_OFFSET,
    ANALOG_WAVEFORM_OUTPUT_PLAY,
    ANALOG_WAVEFORM_OUTPUT_WAVEFORM,
    AWG_RUN_MODE,
    AWG_SAMPLE_RATE,
    AWG_SEQUENCER,
    OSCILLOSCOPE_ARM,
    OSCILLOSCOPE_BANDWIDTH_LIMIT,
    OSCILLOSCOPE_CONTROL,
    OSCILLOSCOPE_COUPLING,
    OSCILLOSCOPE_FETCH_TIME,
    OSCILLOSCOPE_FETCH_VOLTAGE,
    OSCILLOSCOPE_IMPEDANCE,
    OSCILLOSCOPE_INPUT,
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

RECORD_LENGTH = sc.coordinate("record_length", sc.IntType())
SAMPLE_RATE = sc.Quantity(1.0, "GHz")


@dataclass(frozen=True, slots=True)
class RaggedScopeDataset:
    record_length: sc.CoordinateRef[int]
    time: sc.ProductRef[MeasurementArrayData]
    voltage: sc.ProductRef[MeasurementArrayData]


def _repeating_probe_waveform() -> SampledWaveform:
    return {"samples": [0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5]}


@sc.experiment(id="reference_lab.ragged_scope_capture")
def ragged_scope_capture(
    experiment: sc.ExperimentContext,
) -> RaggedScopeDataset:
    """Capture one real AWG output with a point-varying scope record length."""

    experiment.points(
        (
            {RECORD_LENGTH: 4},
            {RECORD_LENGTH: 7},
            {RECORD_LENGTH: 10},
        )
    )
    source = sc.capability_resource(
        experiment,
        "source",
        requires=(AWG_SEQUENCER, ANALOG_WAVEFORM_OUTPUT),
    )
    monitor = sc.capability_resource(
        experiment,
        "monitor",
        requires=(OSCILLOSCOPE_CONTROL, OSCILLOSCOPE_INPUT),
    )
    sc.ensure_state_targets(
        experiment,
        (
            source.state_target(
                {
                    AWG_SAMPLE_RATE: SAMPLE_RATE,
                    AWG_RUN_MODE: "continuous",
                    ANALOG_WAVEFORM_OUTPUT_AMPLITUDE: sc.Quantity(100.0, "mV"),
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
                    OSCILLOSCOPE_VERTICAL_SCALE: sc.Quantity(50.0, "mV"),
                    OSCILLOSCOPE_VERTICAL_OFFSET: sc.Quantity(0.0, "V"),
                    OSCILLOSCOPE_COUPLING: "dc",
                    OSCILLOSCOPE_IMPEDANCE: "50_ohm",
                    OSCILLOSCOPE_BANDWIDTH_LIMIT: sc.Quantity(500.0, "MHz"),
                }
            ),
        ),
    )
    waveform = experiment.compute(
        "repeating_probe_waveform",
        fn=_repeating_probe_waveform,
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

    sample_axis = product_axis("sample", size=None, kind="time", unit="s")
    time = experiment._product("time", unit="s", axes=(sample_axis,))
    voltage = experiment._product("voltage", unit="V", axes=(sample_axis,))
    monitor.acquire(
        {
            OSCILLOSCOPE_FETCH_TIME: time,
            OSCILLOSCOPE_FETCH_VOLTAGE: voltage,
        },
        id="fetch_trace",
    )
    return RaggedScopeDataset(
        record_length=RECORD_LENGTH,
        time=cast(
            "sc.ProductRef[MeasurementArrayData]",
            time,
        ),
        voltage=cast(
            "sc.ProductRef[MeasurementArrayData]",
            voltage,
        ),
    )


RAGGED_SCOPE_CAPTURE = ragged_scope_capture.build()


__all__ = [
    "RAGGED_SCOPE_CAPTURE",
    "RECORD_LENGTH",
    "RaggedScopeDataset",
    "ragged_scope_capture",
]
