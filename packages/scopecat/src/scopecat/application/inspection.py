"""Read-only projections shared by managed authors and live notebook reviews."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from scopecat.daemon.review_projection import inspection_domain, inspection_point
from scopecat.daemon.reviews import ReviewInspectionView, ReviewPointView
from scopecat.planning.preview_models import (
    ExperimentPreview,
    ExperimentPreviewBinding,
    ExperimentPreviewBindingEdge,
    ExperimentPreviewCompute,
    ExperimentPreviewParameter,
    ExperimentPreviewRecord,
    ExperimentPreviewTransientProduct,
)

_INSPECTION_ITEM_LIMIT = 256


class LaunchInspection(BaseModel):
    """A bounded section of the exact launch preview, never an editable program.

    Keep the enclosing preview's request/configuration/source identity alongside
    these facts. Parameter dependencies name fields, not resolved values or rows.
    Domain inspection applies only to the selected point. Truncation is explicit
    for each collection; target-owned waveform/program budgets remain in content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_point_count: int | None
    point_limit: int
    sampled_point_limit: int
    points: tuple[ReviewPointView, ...]
    points_truncated: bool
    selected_point: ReviewPointView | None
    domain_inspections: tuple[ReviewInspectionView, ...]
    records: tuple[ExperimentPreviewRecord, ...]
    transient_products: tuple[ExperimentPreviewTransientProduct, ...]
    computes: tuple[ExperimentPreviewCompute, ...]
    parameters: tuple[ExperimentPreviewParameter, ...]
    bindings: tuple[ExperimentPreviewBinding, ...]
    binding_edges: tuple[ExperimentPreviewBindingEdge, ...]
    item_limit: int
    item_counts: dict[str, int]
    truncated: tuple[str, ...]

    @classmethod
    def from_preview(cls, preview: ExperimentPreview) -> LaunchInspection:
        counts: dict[str, int] = {}
        truncated: list[str] = []

        def take[T](name: str, items: tuple[T, ...]) -> tuple[T, ...]:
            counts[name] = len(items)
            if len(items) > _INSPECTION_ITEM_LIMIT:
                truncated.append(name)
            return items[:_INSPECTION_ITEM_LIMIT]

        return cls(
            total_point_count=preview.total_point_count,
            point_limit=preview.point_limit,
            sampled_point_limit=preview.sampled_point_limit,
            points=tuple(inspection_point(point) for point in preview.points),
            points_truncated=preview.points_truncated,
            selected_point=(
                None
                if preview.selected_point is None
                else inspection_point(preview.selected_point)
            ),
            domain_inspections=tuple(
                inspection_domain(item)
                for item in take("domain_inspections", preview.domain_inspections)
            ),
            records=take("records", preview.records),
            transient_products=take("transient_products", preview.transient_products),
            computes=take("computes", preview.computes),
            parameters=take("parameters", preview.parameters),
            bindings=take("bindings", preview.bindings),
            binding_edges=take("binding_edges", preview.binding_edges),
            item_limit=_INSPECTION_ITEM_LIMIT,
            item_counts=counts,
            truncated=tuple(truncated),
        )
