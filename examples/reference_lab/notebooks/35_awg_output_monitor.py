"""Capture a temporarily cabled AWG output without qubit entity routing."""

from __future__ import annotations

from typing import cast

import numpy as np
import scopecat as sc
from numpy.typing import NDArray

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import gallery_inputs, show
from reference_lab.workflows.awg_output_monitor import AWG_OUTPUT_MONITOR

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    inputs = gallery_inputs(lab)
    run = lab.run(
        AWG_OUTPUT_MONITOR,
        config=inputs,
        name="AWG CH1 pulse shape after bench recabling",
        tags=("diagnostic", "awg-monitor"),
        description=(
            "Scope CH1 <- drive AWG CH1 through a direct 50 ohm cable; "
            "external trigger from the AWG marker is assumed."
        ),
    )
    data = run.measurements()
    request = run.request
    status = run.status
    time_s = cast(
        "NDArray[np.float64]",
        data[AWG_OUTPUT_MONITOR.output.time].require_values()[0],
    )
    voltage_v = cast(
        "NDArray[np.float64]",
        data[AWG_OUTPUT_MONITOR.output.voltage].require_values()[0],
    )
    time_values = cast("list[float]", time_s.tolist())
    voltage_values = cast("list[float]", voltage_v.tolist())

awg_output_monitor_summary = {
    "name": request.display_name,
    "tags": list(request.tags),
    "description_mentions_wiring": "Scope CH1 <- drive AWG CH1"
    in (request.description or ""),
    "samples": len(voltage_values),
    "time_end_ns": round(time_values[-1] * 1e9, 6),
    "peak_mv": round(max(voltage_values) * 1e3, 6),
    "minimum_mv": round(min(voltage_values) * 1e3, 6),
    "status": status,
}
show(awg_output_monitor_summary)
