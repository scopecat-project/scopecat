"""Sweep a shared external LO while keeping signed IQ intermediate frequencies."""

from __future__ import annotations

import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import gallery_inputs, show
from reference_lab.workflows.xy_drive import XY_LO_SWEEP

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    inputs = gallery_inputs(lab)
    run = lab.run(XY_LO_SWEEP, config=inputs)
    data = run.measurements()
    requested_carrier_frequency_ghz = {
        entity.id: [
            round(value.value, 6) for value in data[record].require_quantities("GHz")
        ]
        for entity, record in XY_LO_SWEEP.output.requested_carrier_frequency.items()
    }
    status = run.status

xy_lo_sweep_summary = {
    "requested_lo_ghz": [4.90, 4.91, 4.92],
    "requested_signed_if_mhz": {"q0": 100.0, "q1": -100.0},
    "requested_carrier_ghz": requested_carrier_frequency_ghz,
    "status": status,
}
show(xy_lo_sweep_summary)
