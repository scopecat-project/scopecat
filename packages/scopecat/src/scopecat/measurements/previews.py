"""Bounded presentation values; summaries are not scientific measurement values."""

from collections.abc import Iterable
from math import prod
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from scopecat.program.measurement_types import MeasurementDType
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementPartitionedArray,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementSegmentedArray,
    MeasurementUnavailable,
    MeasurementUnavailableReason,
    MeasurementValue,
)


class MeasurementArraySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["array_summary"] = "array_summary"
    dtype: MeasurementDType
    unit: str | None
    shape: tuple[int | None, ...]
    sample_count: int | None
    available_sample_count: int
    unavailable_reasons: tuple[MeasurementUnavailableReason, ...]


type MeasurementPreviewValue = Annotated[
    MeasurementScalar
    | MeasurementArray
    | MeasurementPartitionedArray
    | MeasurementSegmentedArray
    | MeasurementUnavailable
    | MeasurementArraySummary,
    Field(discriminator="kind"),
]


class MeasurementRecordPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    point_index: int
    logical_point_id: str | None
    coordinates: dict[str, MeasurementPreviewValue]
    observables: dict[str, MeasurementPreviewValue]


def preview_measurement_records(
    records: Iterable[MeasurementRecord], *, array_sample_budget: int = 4096
) -> tuple[MeasurementRecordPreview, ...]:
    """Keep small arrays within one shared page budget; summarize larger values.

    Do not join partitions or replace scientific arrays with truncated samples.
    Full arrays and acquisition evidence remain available through data reads.
    """
    remaining = array_sample_budget

    def project(value: MeasurementValue) -> MeasurementPreviewValue:
        nonlocal remaining
        if isinstance(value, MeasurementScalar | MeasurementUnavailable):
            return value
        parts = (
            value.partitions
            if isinstance(value, MeasurementPartitionedArray)
            else value.segments
            if isinstance(value, MeasurementSegmentedArray)
            else (value,)
        )
        sample_counts = [
            None
            if any(extent is None for extent in part.shape)
            else prod(extent for extent in part.shape if extent is not None)
            for part in parts
        ]
        sample_count = (
            None
            if None in sample_counts
            else sum(count for count in sample_counts if count is not None)
        )
        # Count unavailable leaves too: their masks/indices also have wire cost.
        if sample_count is not None and sample_count <= remaining:
            remaining -= sample_count
            return value
        available = 0
        reasons: set[MeasurementUnavailableReason] = set()
        for part in parts:
            if isinstance(part, MeasurementUnavailable):
                reasons.add(part.reason)
            elif part.availability is None:
                available += prod(part.shape)
            else:
                available += int(np.count_nonzero(part.availability.valid))
                reasons.update(group.reason for group in part.availability.unavailable)
        return MeasurementArraySummary(
            dtype=value.dtype,
            unit=value.unit,
            shape=value.shape,
            sample_count=sample_count,
            available_sample_count=available,
            unavailable_reasons=tuple(sorted(reasons)),
        )

    return tuple(
        MeasurementRecordPreview(
            run_id=record.run_id,
            point_index=record.point_index,
            logical_point_id=record.logical_point_id,
            coordinates={
                key: project(value) for key, value in record.coordinates.items()
            },
            observables={
                key: project(value) for key, value in record.observables.items()
            },
        )
        for record in records
    )
