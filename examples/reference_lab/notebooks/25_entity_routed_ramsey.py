"""Reuse one Ramsey workflow while scanning the selected qubit channel set."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import gallery_inputs, show
from reference_lab.workflows.ramsey_experiments import entity_routed_ramsey

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    inputs = gallery_inputs(lab)
    invocation = entity_routed_ramsey.build()
    preview = lab.preview(invocation, config=inputs)
    run = lab.run(
        invocation,
        config=inputs,
        name="Entity-routed Ramsey",
        tags=("gallery", "ramsey", "entity-routing"),
    )
    data = run.measurements()
    qubit_id = data[invocation.output.qubit].id
    groups = data.groupby(qubit_id)
    status = run.status

entity_ramsey_summary = {
    "points": preview.point_count,
    "records": len(data),
    "qubit_groups": len(groups),
    "status": status,
}
show(entity_ramsey_summary)
