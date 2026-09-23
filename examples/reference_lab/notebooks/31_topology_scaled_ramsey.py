"""Resolve one reusable Ramsey program over a connected qubit set."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import gallery_inputs, show
from reference_lab.workflows.ramsey import topology_scaled_ramsey_program
from reference_lab.workflows.ramsey_experiments import topology_scaled_ramsey

# %%
program_tree = topology_scaled_ramsey_program.draw()

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    inputs = gallery_inputs(lab)
    invocation = topology_scaled_ramsey.build()
    preview = lab.preview(invocation, config=inputs)
    run = lab.run(
        invocation,
        config=inputs,
        name="Topology-scaled Ramsey",
        tags=("gallery", "topology", "qubit-set", "parallel"),
        description=(
            "The program selects three connected logical qubits from accepted "
            "topology and maps one Ramsey branch over the resolved set."
        ),
    )
    data = run.measurements()
    iq_shots_ref = invocation.output.iq_shots
    iq_shots = data[iq_shots_ref]
    entity_dimension = next(
        dimension
        for dimension in data.schema.dimensions
        if dimension.kind == "entity" and dimension.id in iq_shots.dims
    )
    assert entity_dimension.index is not None
    entities = tuple(entity_dimension.index.values)
    topology_scaled_summary = {
        "points": preview.point_count,
        "records": len(data),
        "variable": iq_shots.id,
        "dims": list(iq_shots.dims),
        "shape": list(iq_shots.shape),
        "entities": [entity.id for entity in entities],
        "tree_has_parallel_each": "parallel_each $targets" in program_tree,
        "status": run.status,
    }
show(program_tree)
show(topology_scaled_summary)
