"""Inspect ragged waveform data from one physical oscilloscope input."""

from __future__ import annotations

import scopecat as sc
from scopecat.records.measurement import MeasurementArray, MeasurementValue

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import gallery_inputs, show
from reference_lab.workflows.ragged_scope_capture import RAGGED_SCOPE_CAPTURE


def available_shape(value: MeasurementValue) -> list[int | None]:
    assert isinstance(value, MeasurementArray)
    return list(value.shape)


# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    inputs = gallery_inputs(lab)
    run = lab.run(
        RAGGED_SCOPE_CAPTURE,
        config=inputs,
        name="Variable record-length AWG monitor",
        tags=("gallery", "ragged", "diagnostic"),
        description=(
            "Scope CH1 <- drive AWG CH1; record length intentionally varies by "
            "point while the temporary cable stays fixed."
        ),
    )
    data = run.measurements()
    voltage_view = data[RAGGED_SCOPE_CAPTURE.output.voltage]
    sample_dimension = voltage_view.dims[1]
    voltage_id = voltage_view.id
    first_two_samples = data.isel_ragged(
        {sample_dimension: slice(0, 2)},
        variable=voltage_id,
    )
    status = run.status

ragged_scope_summary = {
    "record_lengths": [4, 7, 10],
    "ragged_shapes": [
        available_shape(value)
        for value in data[RAGGED_SCOPE_CAPTURE.output.voltage].raw_values
    ],
    "window_shapes": [
        available_shape(value)
        for value in first_two_samples[RAGGED_SCOPE_CAPTURE.output.voltage].raw_values
    ],
    "status": status,
}
show(ragged_scope_summary)
