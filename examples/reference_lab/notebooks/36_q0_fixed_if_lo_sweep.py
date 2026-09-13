"""Combine a host-controlled LO scan with a fixed-IF quantum target program."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.ramsey_experiments import (
    q0_fixed_if_lo_sweep,
)

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    invocation = q0_fixed_if_lo_sweep.build()
    preview = lab.preview(invocation)
    run = lab.run(
        invocation,
        name="q0 fixed-IF LO sweep",
        tags=("gallery", "ramsey", "lo-sweep", "fixed-if"),
    )
    data = run.measurements()
    signed_ifs = data[invocation.output.signed_if_frequency].require_quantities("MHz")
    carriers = data[invocation.output.carrier_frequency].require_quantities("GHz")
    status = run.status

q0_fixed_if_lo_sweep_summary = {
    "points": preview.point_count,
    "signed_if_mhz": sorted({round(value.value, 6) for value in signed_ifs}),
    "carrier_ghz": [round(value.value, 6) for value in carriers],
    "status": status,
}
show(q0_fixed_if_lo_sweep_summary)
