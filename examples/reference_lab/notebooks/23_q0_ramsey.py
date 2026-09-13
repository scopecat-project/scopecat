"""Run the first Ramsey delay scan on the q0 channel set."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.ramsey_experiments import q0_ramsey

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    invocation = q0_ramsey.build()
    preview = lab.preview(invocation)
    run = lab.run(invocation, name="q0 Ramsey", tags=("gallery", "ramsey", "q0"))
    data = run.measurements()
    probability = data[
        invocation.output.probabilities.probability_1
    ].require_quantities("ratio")
    status = run.status

q0_ramsey_summary = {
    "points": preview.point_count,
    "records": len(data),
    "probability_samples": len(probability),
    "status": status,
}
show(q0_ramsey_summary)
