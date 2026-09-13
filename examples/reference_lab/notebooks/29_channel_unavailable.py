"""Explore entity-indexed IQ shots with one unavailable qubit readout."""

from __future__ import annotations

# %%
import scopecat as sc
from scopecat.records.measurement import MeasurementUnavailable

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.ramsey_experiments import parallel_raw_ramsey

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    invocation = parallel_raw_ramsey.build()
    run = lab.run(
        invocation,
        name="Entity-axis multiplexed Ramsey readout",
        tags=("gallery", "entity-axis", "multi-channel", "unavailable"),
        description=(
            "Q0 and Q1 share one logical_qubit axis over 64 IQ shots. Q1 is "
            "intentionally unavailable at the 128 ns point while Q0 remains usable."
        ),
    )
    data = run.measurements()
    iq_shots_ref = invocation.entity_result_ref("iq_shots")
    iq_shots = data[iq_shots_ref]
    entity_dimension = next(
        dimension
        for dimension in data.schema.dimensions
        if dimension.kind == "entity" and dimension.id in iq_shots.dims
    )
    assert entity_dimension.index is not None
    entities = tuple(entity_dimension.index.values)
    iq_by_entity = {
        entity.id: data.sel({entity_dimension.id: entity})[iq_shots_ref]
        for entity in entities
    }
    source = iq_shots.definition.source_entity_products
    acquisition = iq_shots.definition.entity_acquisition
    run_id = run.id
    status = run.status

channel_unavailable_summary = {
    "run_id": run_id,
    "status": status,
    "records": len(data),
    "variable": iq_shots.id,
    "dims": list(iq_shots.dims),
    "shape": list(iq_shots.shape),
    "entities": [entity.id for entity in entities],
    "available_points": {
        entity_id: sum(
            not isinstance(value, MeasurementUnavailable)
            for value in variable.raw_values
        )
        for entity_id, variable in iq_by_entity.items()
    },
    "unavailable_reasons": {
        entity_id: sorted(
            {
                value.reason
                for value in variable.raw_values
                if isinstance(value, MeasurementUnavailable)
            }
        )
        for entity_id, variable in iq_by_entity.items()
    },
    "source_results": (
        None
        if source is None
        else {
            entity.id: product_id
            for entity, product_id in zip(entities, source.product_ids, strict=True)
        }
    ),
    "acquisition_policy": None if acquisition is None else acquisition.policy,
}
# In the Runs workspace, compare All 2, then select Q0 and Q1 separately. The
# 128 ns Q1 failure keeps its entity slot and acquisition evidence while Q0 plots.
show(channel_unavailable_summary)
