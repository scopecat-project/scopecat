"""Run q0/q1 drive channels in parallel with multiplexed readout."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.ramsey_experiments import parallel_two_qubit_ramsey

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    invocation = parallel_two_qubit_ramsey.build()
    preview = lab.preview(invocation)
    run = lab.run(
        invocation,
        name="Parallel two-qubit Ramsey",
        tags=("gallery", "parallel", "multi-channel", "multiplexed-readout"),
    )
    data = run.measurements()
    q0 = data[invocation.output.q0.probability_1].require_quantities("ratio")
    q1 = data[invocation.output.q1.probability_1].require_quantities("ratio")
    status = run.status

parallel_ramsey_summary = {
    "points": preview.point_count,
    "records": len(data),
    "q0_samples": len(q0),
    "q1_samples": len(q1),
    "status": status,
}
show(parallel_ramsey_summary)
