"""Inspect authored, logical, scheduled, and physical quantum layers."""

from __future__ import annotations

# %%
import scopecat as sc
from scopecat.inspection import CompiledProgramInspectionQuery

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.ramsey import topology_scaled_ramsey_program
from reference_lab.workflows.ramsey_experiments import topology_scaled_ramsey

# %%
program_description = topology_scaled_ramsey_program.describe()
program_tree = topology_scaled_ramsey_program.draw()

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    preview = lab.preview(
        topology_scaled_ramsey.build(),
        inspection_query=CompiledProgramInspectionQuery(
            layer_id="physical",
            limit=8,
        ),
    )

[domain_inspection] = preview.domain_inspections
assert domain_inspection.content.program is not None
compiled_program = domain_inspection.content.program
physical_layer = next(
    layer for layer in compiled_program.layers if layer.id == "physical"
)

program_inspection_summary = {
    "program_id": topology_scaled_ramsey_program.id,
    "description_has_ports": "ports:" in program_description,
    "tree_has_parallel_each": "parallel_each $targets" in program_tree,
    "layers": [layer.id for layer in compiled_program.layers],
    "physical_matching_nodes": physical_layer.page.matching_node_count,
    "physical_returned_nodes": physical_layer.page.returned_node_count,
    "snapshot_matches_artifact": (
        compiled_program.snapshot_id == domain_inspection.artifact_fingerprint
    ),
}

show(program_description)
show(program_tree)
show(program_inspection_summary)
