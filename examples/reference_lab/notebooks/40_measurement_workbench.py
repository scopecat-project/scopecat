"""Slice, group, export, and page one durable measurement dataset."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, cast

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.flux_spectroscopy import flux_spectroscopy


class _ArrowTable(Protocol):
    num_rows: int


class _ArrowRecordBatch(Protocol):
    num_rows: int


# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    invocation = flux_spectroscopy.build()
    compact = invocation.with_axis(
        sc.axis(
            invocation.output.dc_bias,
            (
                sc.Quantity(-0.1, "V"),
                sc.Quantity(0.0, "V"),
                sc.Quantity(0.1, "V"),
            ),
        )
    )
    run = lab.run(
        compact,
        name="Measurement workbench source",
        tags=("gallery", "data"),
    )
    data = run.measurements()
    bias_id = data[invocation.output.dc_bias].id
    near_zero = data.sel(
        {bias_id: sc.Quantity(0.01, "V")},
        method="nearest",
    )
    first_two = data.isel(point=slice(0, 2))
    available = data.where(data[invocation.output.temperature].is_available())
    grouped = data.groupby(bias_id)
    grid = data.to_xarray(layout="grid")
    arrow = cast(
        "_ArrowTable",
        data.project().to_arrow(),  # pyright: ignore[reportUnknownMemberType]
    )
    batches = tuple(
        cast(
            "Iterator[_ArrowRecordBatch]",
            data.project().to_record_batch_reader(  # pyright: ignore[reportUnknownMemberType]
                batch_size=2
            ),
        )
    )

measurement_summary: dict[str, object] = {
    "points": len(data),
    "nearest_points": len(near_zero),
    "first_two_points": len(first_two),
    "available_points": len(available),
    "groups": len(grouped),
    "grid_dims": dict(grid.sizes),
    "arrow_rows": arrow.num_rows,
    "batch_sizes": [batch.num_rows for batch in batches],
}
show(measurement_summary)
