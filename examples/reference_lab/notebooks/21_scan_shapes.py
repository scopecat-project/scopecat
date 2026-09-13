"""Run grid policies and an explicit point cloud in the same lab."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.drag_beta_experiment import drag_beta_experiment
from reference_lab.workflows.flux_spectroscopy import flux_spectroscopy

# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    flux = flux_spectroscopy.build()
    sparse_flux = flux.points(
        (
            {flux.output.dc_bias: sc.Quantity(-0.20, "V")},
            {flux.output.dc_bias: sc.Quantity(-0.05, "V")},
            {flux.output.dc_bias: sc.Quantity(-0.05, "V")},
            {flux.output.dc_bias: sc.Quantity(0.18, "V")},
        )
    )
    point_cloud_preview = lab.preview(sparse_flux)
    point_cloud_run = lab.run(
        sparse_flux,
        name="Sparse resonator point cloud",
        tags=("gallery", "point-cloud"),
    )

    drag = drag_beta_experiment.build()
    repeated_snake = (
        drag.grid(
            sc.axis(
                drag.output.beta,
                (sc.Quantity(-0.5, "ns"), sc.Quantity(0.5, "ns")),
            ),
            sc.axis(drag.output.amplification, (1, 3)),
        )
        .with_repeat(2, mode="sweep")
        .with_traversal("snake")
    )
    repeated_preview = lab.preview(repeated_snake)
    repeated_run = lab.run(
        repeated_snake,
        name="Repeated snake DRAG grid",
        tags=("gallery", "product-grid", "repeat", "snake"),
    )
    point_cloud_data = point_cloud_run.measurements()
    repeated_data = repeated_run.measurements()
    scan_shapes_summary = {
        "point_cloud_points": point_cloud_preview.point_count,
        "point_cloud_layout": point_cloud_data.schema.point_domain.kind,
        "point_cloud_rows": len(point_cloud_data),
        "repeated_grid_points": repeated_preview.point_count,
        "repeated_grid_layout": repeated_data.schema.point_domain.kind,
        "repeated_grid_rows": len(repeated_data),
    }
show(scan_shapes_summary)
