from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from scopecat.compiler.bind import BoundPlan
from scopecat.execution.local.program import LocalOperation
from scopecat.execution.program import RunCoverageEffect
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.product_identity import ProductUseId
from scopecat.kernel.resource_identity import ResourceRequirement
from scopecat.measurements.projection import select_measurement_projection
from scopecat.planning.local_effects import local_operation_resource_requirements
from scopecat.planning.local_materialization import (
    materialize_local_execution as lower_local_execution,
)
from scopecat.planning.local_materialization import (
    prepare_local_target,
)
from scopecat.planning.measurement_projection import (
    project_measurement_catalog,
    project_run_point_catalog,
)
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.records.parameter_read import BindingParameterRead, HostPointParameterRead
from scopecat.sdk.payloads import EMPTY_PAYLOAD_CODECS, PayloadCodecRegistry


@dataclass(frozen=True, slots=True)
class LocalEffectInspection:
    """Production-aligned test view of points and exact effect coverage."""

    points: tuple[AcceptedRunPoint, ...]
    effects: tuple[RunCoverageEffect, ...]
    resource_order: tuple[str, ...]
    resource_requirements: tuple[ResourceRequirement, ...]
    parameter_reads: tuple[HostPointParameterRead, ...] = ()
    binding_parameter_reads: tuple[BindingParameterRead, ...] = ()

    @classmethod
    def at_point(
        cls,
        point: AcceptedRunPoint,
        operations: Sequence[LocalOperation],
        *,
        resource_order: Sequence[str] = (),
        resource_requirements: Sequence[ResourceRequirement] = (),
    ) -> LocalEffectInspection:
        """Build exact singleton coverage for a focused interpreter test."""

        return cls(
            points=(point,),
            effects=effects_at_point(point.ordinal, operations),
            resource_order=tuple(resource_order),
            resource_requirements=tuple(resource_requirements),
        )


def materialize_local_execution(
    bound: BoundPlan,
    *,
    product_use_ids: AbstractSet[ProductUseId] | None = None,
    instrument_order: Sequence[str] = (),
    payload_codecs: PayloadCodecRegistry = EMPTY_PAYLOAD_CODECS,
) -> LocalEffectInspection:
    """Lower a bound program for focused inspection of final effect coverage."""

    bound_points = prepare_bound_points(bound)
    selected_product_use_ids = (
        frozenset(use.id for use in bound.bindings.product_uses)
        if product_use_ids is None
        else frozenset(product_use_ids)
    )
    target = prepare_local_target(
        bound,
        product_use_ids=selected_product_use_ids,
        instrument_order=instrument_order,
        acquisition_cohorts=select_measurement_projection(
            project_measurement_catalog(bound_points),
            bound.bindings.record_uses,
        ).acquisition_cohorts,
        payload_codecs=payload_codecs,
    )
    lowered = lower_local_execution(
        bound_points,
        target=target,
    )
    ordered_effects = (
        *lowered.compute_operations,
        *(effect for group in lowered.effect_operations for effect in group),
    )
    claims = tuple(
        dict.fromkeys(
            claim
            for effect in ordered_effects
            for claim in local_operation_resource_requirements(effect.operation)
        )
    )
    instrument_ids = {claim.id for claim in claims if claim.kind == "instrument"}
    resource_order = (
        *(item for item in target.instrument_order if item in instrument_ids),
        *sorted(instrument_ids - set(target.instrument_order)),
    )
    return LocalEffectInspection(
        points=tuple(project_run_point_catalog(bound_points).points),
        effects=ordered_effects,
        resource_order=resource_order,
        resource_requirements=claims,
        parameter_reads=lowered.parameter_reads,
        binding_parameter_reads=lowered.binding_parameter_reads,
    )


def operations_of_type[T: LocalOperation](
    inspection: LocalEffectInspection | Sequence[LocalOperation],
    operation_type: type[T],
    *,
    point_index: int | None = None,
) -> tuple[T, ...]:
    """Select operations, optionally restricted to one logical point."""

    operations: Sequence[LocalOperation] = (
        tuple(
            effect.operation
            for effect in inspection.effects
            if point_index is None or point_index == effect.point_index
        )
        if isinstance(inspection, LocalEffectInspection)
        else inspection
    )
    return tuple(
        operation for operation in operations if isinstance(operation, operation_type)
    )


def effects_at_point(
    point_index: int,
    operations: Sequence[LocalOperation],
) -> tuple[RunCoverageEffect, ...]:
    """Attach exact singleton coverage to synthetic interpreter operations."""

    return tuple(RunCoverageEffect(point_index, operation) for operation in operations)
