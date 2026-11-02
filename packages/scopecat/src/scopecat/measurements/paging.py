"""Shared projection boundary for finite measurement pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pyarrow as pa

from scopecat.measurements.dataset import Dataset
from scopecat.measurements.datasets import select_measurement_schema
from scopecat.measurements.interop import (
    ProjectionDiagnostics,
    ProjectionLayout,
)
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    MeasurementDataset,
    MeasurementDatasetSchema,
    MeasurementRecord,
)


# PyArrow native Table/IPC types are untyped; keep this exception at the boundary.
def project_measurement_page(  # pyright: ignore[reportUnknownParameterType]
    records: Sequence[MeasurementRecord],
    *,
    schema: MeasurementDatasetSchema,
    entry: ContentEntry,
    columns: Mapping[str, str],
    units: Mapping[str, str],
    diagnostics: ProjectionDiagnostics,
    include_identity: bool,
    layout: ProjectionLayout,
) -> pa.Table:  # pyright: ignore[reportUnknownMemberType]
    """Project a selected-schema page through the canonical ecosystem adapter."""

    selected_schema = select_measurement_schema(schema, tuple(columns.values()))
    return (  # pyright: ignore[reportUnknownVariableType]
        Dataset(  # pyright: ignore[reportUnknownMemberType]
            raw=MeasurementDataset(
                dataset_schema=selected_schema,
                records=records,
                metadata=entry.metadata,
            ),
            entry=entry,
        )
        .project(
            columns,
            units=units,
            diagnostics=diagnostics,
            identity=include_identity,
            layout=layout,
        )
        .to_arrow()
    )


__all__ = ["project_measurement_page"]
