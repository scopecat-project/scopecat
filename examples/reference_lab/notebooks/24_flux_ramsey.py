"""Compose q0 flux bias and Ramsey delay into one real two-dimensional run."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import gallery_inputs, show
from reference_lab.workflows.ramsey_experiments import flux_ramsey

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    inputs = gallery_inputs(lab)
    invocation = flux_ramsey.build()
    preview = lab.preview(invocation, config=inputs)
    run = lab.run(
        invocation,
        config=inputs,
        name="q0 flux Ramsey map",
        tags=("gallery", "flux", "ramsey", "two-dimensional"),
    )
    data = run.measurements()
    grid = data.to_xarray(layout="grid")
    status = run.status

flux_ramsey_summary = {
    "points": preview.point_count,
    "records": len(data),
    "dimensions": dict(grid.sizes),
    "status": status,
}
show(flux_ramsey_summary)
