"""Bounded scientific scope projected from an already compiled experiment preview."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.planning.preview_models import (
    ExperimentPreview,
    ExperimentPreviewDomainInspection,
)
from scopecat.records.config import ConfigContentHash


class _PreflightModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ExactQuantity(_PreflightModel):
    kind: Literal["exact"] = "exact"
    value: float
    unit: str
    basis: str


class BoundedQuantity(_PreflightModel):
    kind: Literal["bounded"] = "bounded"
    lower: float
    upper: float
    unit: str
    basis: str


class UnknownQuantity(_PreflightModel):
    kind: Literal["unknown"] = "unknown"
    unit: str
    basis: str


type PreflightQuantity = Annotated[
    ExactQuantity | BoundedQuantity | UnknownQuantity, Field(discriminator="kind")
]


class PreflightProduct(_PreflightModel):
    id: str
    retention: Literal["retained", "transient"]
    dtype: str
    unit: str | None
    dims: tuple[str, ...]
    shape: tuple[int | None, ...]


class PreflightCost(_PreflightModel):
    metric: str
    quantity: PreflightQuantity
    scope: Literal["inspected_artifact", "experiment", "procedure"]
    target_id: str | None = None
    artifact_fingerprint: str | None = None


class PreflightStage(_PreflightModel):
    id: str
    label: str
    experiment_id: str
    configuration: Literal["accepted", "proposed_candidate"]
    config_content_hash: ConfigContentHash
    configuration_meaning: str
    executions: PreflightQuantity
    point_scope: Literal["static_plan", "adaptive_limit"]
    initial_proposed_points: int
    points_per_execution: PreflightQuantity
    shots_per_point_per_entity: PreflightQuantity
    entity_ids: tuple[str, ...] | None
    products: tuple[PreflightProduct, ...]
    costs: tuple[PreflightCost, ...]
    sampled_point_limit: int
    sampled_points: int
    selected_point_limit: int
    selected_points: int
    inspections: tuple[ExperimentPreviewDomainInspection, ...] = ()


class PreflightSummary(_PreflightModel):
    """Project-declared stages; each stage retains its own science and cost scope."""

    stages: tuple[PreflightStage, ...]
    scope_basis: str


def summarize_preflight(
    preview: ExperimentPreview,
    *,
    stage_id: str,
    label: str,
    config_content_hash: ConfigContentHash,
    configuration: Literal["accepted", "proposed_candidate"],
    configuration_meaning: str,
    executions: PreflightQuantity,
    shots_per_point_per_entity: PreflightQuantity | None = None,
    entity_ids: tuple[str, ...] | None = None,
) -> PreflightStage:
    """Read closed contracts only; never enumerate or compile additional points."""
    points: PreflightQuantity = (
        ExactQuantity(
            value=preview.total_point_count,
            unit="points",
            basis="Static point-plan cardinality",
        )
        if preview.total_point_count is not None
        else BoundedQuantity(
            lower=0,
            upper=preview.point_limit,
            unit="points",
            basis=(
                "Possible acquired points up to the adaptive limit; early termination "
                "may precede initial proposals"
            ),
        )
    )
    if entity_ids is None and preview.schema is not None:
        indexed = tuple(
            dict.fromkeys(
                entity.id
                for dimension in preview.schema.dimensions
                if dimension.index is not None
                for entity in dimension.index.values
            )
        )
        if indexed:
            entity_ids = indexed
    products = tuple(
        PreflightProduct(
            id=record.id,
            retention="retained",
            dtype=record.dtype,
            unit=record.unit,
            dims=record.dims,
            shape=record.shape,
        )
        for record in preview.records
    ) + tuple(
        PreflightProduct(
            id=product.id,
            retention="transient",
            dtype=product.dtype,
            unit=product.unit,
            dims=product.dims,
            shape=product.shape,
        )
        for product in preview.transient_products
    )
    costs = tuple(
        PreflightCost(
            metric=estimate.metric,
            scope="inspected_artifact",
            target_id=inspection.target_id,
            artifact_fingerprint=inspection.artifact_fingerprint,
            quantity=(
                ExactQuantity(
                    value=estimate.lower, unit=estimate.unit, basis=estimate.basis
                )
                if estimate.lower == estimate.upper
                else BoundedQuantity(
                    lower=estimate.lower,
                    upper=estimate.upper,
                    unit=estimate.unit,
                    basis=estimate.basis,
                )
            ),
        )
        for inspection in preview.domain_inspections
        for estimate in inspection.content.work_estimates
    )
    costs += tuple(
        PreflightCost(
            metric=metric,
            scope="experiment",
            quantity=UnknownQuantity(
                unit=unit,
                basis=basis,
            ),
        )
        for metric, unit, basis in (
            (
                "wall_time",
                "s",
                (
                    "No experiment wall-time bound supplied; queue, transfer "
                    "and host time are not inferred"
                ),
            ),
            (
                "retained_bytes",
                "bytes",
                (
                    "Dataset encoding, evidence and storage overhead have not "
                    "been estimated"
                ),
            ),
            (
                "batches",
                "batches",
                (
                    "Selected-artifact capacity does not establish the full "
                    "experiment batch partition"
                ),
            ),
        )
    )
    return PreflightStage(
        id=stage_id,
        label=label,
        experiment_id=preview.experiment_id,
        configuration=configuration,
        config_content_hash=config_content_hash,
        configuration_meaning=configuration_meaning,
        executions=executions,
        point_scope="static_plan"
        if preview.total_point_count is not None
        else "adaptive_limit",
        initial_proposed_points=preview.initial_point_count,
        points_per_execution=points,
        shots_per_point_per_entity=shots_per_point_per_entity
        or UnknownQuantity(unit="shots", basis="No shot count declared by the project"),
        entity_ids=entity_ids,
        products=products,
        costs=costs,
        sampled_point_limit=preview.sampled_point_limit,
        sampled_points=len(preview.points),
        selected_point_limit=preview.selected_point_limit,
        selected_points=int(preview.selected_point is not None),
        inspections=preview.domain_inspections,
    )
