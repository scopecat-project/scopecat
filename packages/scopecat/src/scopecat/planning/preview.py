"""Projection of a RunProgram into stable user-visible facts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from scopecat.authoring.experiments import ExperimentInvocation
from scopecat.execution.local.program import ComputeOperation, OutputInput
from scopecat.execution.program import RunPointInspection, RunProgram
from scopecat.inspection import CompiledProgramInspectionQuery
from scopecat.kernel.points import PointProposalAttempt
from scopecat.measurements.records import EntityRecordPlan, RecordPlan, ValueRecordPlan
from scopecat.planning.point_selection import (
    point_coordinate_contract,
    resolve_point_selection,
)
from scopecat.planning.preview_models import (
    ExperimentPreview,
    ExperimentPreviewBinding,
    ExperimentPreviewBindingEdge,
    ExperimentPreviewBindingRef,
    ExperimentPreviewCompute,
    ExperimentPreviewDomainInspection,
    ExperimentPreviewPoint,
    ExperimentPreviewPointGroup,
    ExperimentPreviewPointGrouping,
    ExperimentPreviewPointSchedule,
    ExperimentPreviewRecord,
    ExperimentPreviewTransientProduct,
)
from scopecat.program.parameters import ParameterContract, ParameterValueContract
from scopecat.program.scans import AroundScanSource, RangeScanSource, ValuesScanSource
from scopecat.program.value_refs import ValueRef, internal_value_ref_parameter_contracts
from scopecat.sdk.compute import compute_capture_names_internal

type _BindingKind = Literal["input", "coordinate", "parameter"]
type _BindingKey = tuple[_BindingKind, str]
type PreviewCoordinateMode = Literal["exact", "snap", "free"]
_BUNDLE_FIELD_IMPLEMENTATION = "internal:scopecat.bundle-field@1"
_PREVIEW_POINT_LIMIT = 64


def build_run_program_preview(
    program: RunProgram,
    *,
    invocation: ExperimentInvocation | None = None,
    point: int | Literal["first", "middle", "last"] = "first",
    coordinates: Mapping[str, object] | None = None,
    coordinate_mode: PreviewCoordinateMode = "exact",
    inspection_query: CompiledProgramInspectionQuery | None = None,
) -> ExperimentPreview:
    """Project stable user-visible facts from a closed RunProgram."""

    selected = program.measurements
    catalog = program.points
    point_count = catalog.contract.point_count
    initial_point_count = len(catalog.points)
    selected_point_index, free_candidate = _selected_point(
        program,
        point=point,
        coordinates=coordinates,
        coordinate_mode=coordinate_mode,
    )
    planned_point = (
        None if selected_point_index is None else catalog.points[selected_point_index]
    )
    point_inspection = (
        program.coverage.inspect(free_candidate, query=inspection_query)
        if free_candidate is not None
        else (
            None
            if selected_point_index is None
            else program.coverage.inspect(
                selected_point_index,
                query=inspection_query,
            )
        )
    )
    selected_candidate = (
        point_inspection.candidate if point_inspection is not None else free_candidate
    )
    domain_inspections = _preview_domain_inspections(point_inspection)
    preview_ordinals = _preview_point_ordinals(initial_point_count)
    bindings, binding_edges = (
        ((), ()) if invocation is None else _preview_binding_graph(invocation)
    )
    return ExperimentPreview(
        instrument_ids=tuple(item.id for item in program.resource_requirements),
        experiment_id=catalog.experiment_id,
        experiment_kind=catalog.experiment_kind,
        schema=selected.schema,
        coordinate_ids=tuple(selected.coordinate_ids),
        total_point_count=point_count,
        initial_point_count=initial_point_count,
        point_limit=catalog.contract.point_limit,
        points=tuple(
            ExperimentPreviewPoint(
                point_index=resolved.ordinal,
                coordinates=dict(resolved.coordinates),
            )
            for resolved in (catalog.points[ordinal] for ordinal in preview_ordinals)
        ),
        points_truncated=initial_point_count > len(preview_ordinals),
        records=tuple(
            ExperimentPreviewRecord(
                id=record.id,
                role=record.role,
                recording_group_id=record.recording_group_id,
                unit=record.unit,
                dtype=record.dtype,
                dims=("point", *(axis.id for axis in record.axes)),
                shape=(
                    point_count,
                    *(axis.size for axis in record.axes),
                ),
            )
            for record in selected.records
        ),
        sampled_point_limit=_PREVIEW_POINT_LIMIT,
        selected_point_limit=1,
        transient_products=_preview_transient_products(program),
        point_schedule=ExperimentPreviewPointSchedule(
            traversal=program.point_schedule.traversal,
            grouping=_preview_point_grouping(program),
        ),
        selected_point=(
            None
            if planned_point is None and selected_candidate is None
            else _preview_selected_point(
                coordinate_ids=catalog.coordinate_ids,
                point_index=selected_point_index,
                planned_coordinates=(
                    None if planned_point is None else planned_point.coordinates
                ),
                candidate=selected_candidate,
            )
        ),
        domain_inspections=domain_inspections,
        planned_settings=()
        if point_inspection is None
        else point_inspection.planned_settings,
        planned_settings_truncated=(
            point_inspection is not None and point_inspection.planned_settings_truncated
        ),
        computes=_preview_computes(program),
        bindings=bindings,
        binding_edges=binding_edges,
    )


def _selected_point(
    program: RunProgram,
    *,
    point: int | Literal["first", "middle", "last"],
    coordinates: Mapping[str, object] | None,
    coordinate_mode: PreviewCoordinateMode,
) -> tuple[int | None, PointProposalAttempt | None]:
    catalog = program.points
    point_count = len(catalog.points)
    if coordinate_mode == "free":
        if coordinates is None:
            raise ValueError("free preview requires coordinates")
        if point != "first":
            raise ValueError("select a free preview point by coordinates only")
        specs, _, _ = point_coordinate_contract(
            catalog,
            sampled_point_limit=max(point_count, 1),
        )
        resolved = resolve_point_selection(specs, coordinates, mode="free")
        return None, PointProposalAttempt(
            coordinates=resolved.coordinates,
            source="operator",
        )
    if point_count == 0:
        if coordinates is not None:
            raise ValueError("an empty experiment has no selectable coordinates")
        return None, None
    if coordinates is not None:
        if point != "first":
            raise ValueError("select a preview point by index or coordinates, not both")
        specs, _, _ = point_coordinate_contract(
            catalog,
            sampled_point_limit=max(point_count, 1),
        )
        resolved = resolve_point_selection(
            specs,
            coordinates,
            mode=coordinate_mode,
            sampled_points=tuple(point.coordinates for point in catalog.points),
        )
        assert resolved.sampled_point_index is not None
        return resolved.sampled_point_index, None
    if isinstance(point, int):
        if not 0 <= point < point_count:
            raise IndexError(point)
        return point, None
    if point == "first":
        return 0, None
    if point == "middle":
        return (point_count - 1) // 2, None
    if point == "last":
        return point_count - 1, None
    raise ValueError(f"unsupported preview point selector {point!r}")


def _preview_selected_point(
    *,
    coordinate_ids: tuple[str, ...],
    point_index: int | None,
    planned_coordinates: Mapping[str, object] | None,
    candidate: PointProposalAttempt | None,
) -> ExperimentPreviewPoint:
    coordinates = planned_coordinates if candidate is None else candidate.coordinates
    assert coordinates is not None
    return ExperimentPreviewPoint(
        point_index=point_index,
        coordinates={
            coordinate_id: coordinates[coordinate_id]
            for coordinate_id in coordinate_ids
        },
        proposal_fingerprint=(
            None if candidate is None else candidate.proposal_fingerprint
        ),
        source="author" if candidate is None else candidate.source,
    )


def _preview_domain_inspections(
    inspection: RunPointInspection | None,
) -> tuple[ExperimentPreviewDomainInspection, ...]:
    if inspection is None:
        return ()
    inspections: list[ExperimentPreviewDomainInspection] = []
    for job in inspection.jobs:
        content = job.execution.inspection
        if content is None:
            continue
        intent = job.execution.invocation.intent
        inspections.append(
            ExperimentPreviewDomainInspection(
                operation_id=job.id,
                point_index=inspection.point_index,
                target_id=intent.target_id,
                artifact_id=intent.artifact_id,
                artifact_fingerprint=intent.artifact_fingerprint,
                content=content,
            )
        )
    return tuple(inspections)


def _preview_point_ordinals(point_count: int) -> tuple[int, ...]:
    if point_count <= _PREVIEW_POINT_LIMIT:
        return tuple(range(point_count))
    edge_count = _PREVIEW_POINT_LIMIT // 2
    return (*range(edge_count), *range(point_count - edge_count, point_count))


def _preview_point_grouping(
    program: RunProgram,
) -> ExperimentPreviewPointGrouping | None:
    grouping = program.point_schedule.grouping
    if grouping is None:
        return None
    groups = program.point_groups
    sampled = (
        groups
        if len(groups) <= _PREVIEW_POINT_LIMIT
        else (
            *groups[: _PREVIEW_POINT_LIMIT // 2],
            *groups[-(_PREVIEW_POINT_LIMIT // 2) :],
        )
    )
    return ExperimentPreviewPointGrouping(
        id=grouping.id,
        varying_coordinate_ids=grouping.varying_coordinate_ids,
        scheduling=grouping.scheduling,
        on_interruption=grouping.on_interruption,
        group_count=len(groups),
        groups=tuple(
            ExperimentPreviewPointGroup(
                id=group.id,
                key=dict(group.key),
                point_indices=group.ordinals,
            )
            for group in sampled
        ),
        groups_truncated=len(sampled) < len(groups),
    )


def _preview_binding_graph(
    invocation: ExperimentInvocation,
) -> tuple[
    tuple[ExperimentPreviewBinding, ...],
    tuple[ExperimentPreviewBindingEdge, ...],
]:
    bindings: dict[_BindingKey, ExperimentPreviewBinding] = {
        ("input", input_definition.id): ExperimentPreviewBinding(
            id=input_definition.id,
            kind="input",
            owner="invocation",
            origin=(
                "override"
                if input_definition.id in invocation.input_overrides
                else "default"
            ),
        )
        for input_definition in invocation.definition.inputs
    }
    edges: list[ExperimentPreviewBindingEdge] = []
    for axis in invocation.point_plan.domain.axes:
        source = axis.source
        origin = (
            "values"
            if isinstance(source, ValuesScanSource)
            else "range"
            if isinstance(source, RangeScanSource)
            else "around"
        )
        coordinate_ref = ExperimentPreviewBindingRef(id=axis.id, kind="coordinate")
        bindings[("coordinate", axis.id)] = ExperimentPreviewBinding(
            id=axis.id,
            kind="coordinate",
            owner="point-plan",
            origin=origin,
        )
        if isinstance(source, AroundScanSource) and isinstance(source.center, ValueRef):
            _add_parameter_edges(
                bindings,
                edges,
                source.center,
                target=coordinate_ref,
                relation="centers",
            )
        if axis.overlay is not None:
            _add_parameter_edges(
                bindings,
                edges,
                axis.overlay,
                target=coordinate_ref,
                relation="overlays",
            )
    return tuple(bindings.values()), tuple(edges)


def _add_parameter_edges(
    bindings: dict[_BindingKey, ExperimentPreviewBinding],
    edges: list[ExperimentPreviewBindingEdge],
    value: ValueRef,
    *,
    target: ExperimentPreviewBindingRef,
    relation: Literal["centers", "overlays"],
) -> None:
    for contract in internal_value_ref_parameter_contracts(value):
        parameter_id = _parameter_contract_id(contract)
        parameter_ref = ExperimentPreviewBindingRef(
            id=parameter_id,
            kind="parameter",
        )
        bindings[("parameter", parameter_id)] = ExperimentPreviewBinding(
            id=parameter_id,
            kind="parameter",
            owner="configuration",
            origin=None,
        )
        edges.append(
            ExperimentPreviewBindingEdge(
                source=parameter_ref,
                target=target,
                relation=relation,
            )
        )


def _parameter_contract_id(contract: ParameterContract) -> str:
    if isinstance(contract, ParameterValueContract):
        return contract.parameter_id
    return f"{contract.table_id}.{contract.column_id}"


def _preview_computes(program: RunProgram) -> tuple[ExperimentPreviewCompute, ...]:
    host_operations: dict[str, ComputeOperation] = {}
    for operation in program.preview_compute_operations:
        host_operations.setdefault(operation.logical_compute_node_id, operation)

    value_record_demands = {
        record.value_id: f"record:{record.id}"
        for record in program.measurements.records
        if isinstance(record, ValueRecordPlan)
    }
    product_record_demands: dict[object, list[str]] = {}
    for record in program.measurements.records:
        if isinstance(record, RecordPlan):
            product_record_demands.setdefault(record.product_id, []).append(
                f"record:{record.id}"
            )
    observation_value_demands: dict[object, list[str]] = {}
    observation_product_demands: dict[object, list[str]] = {}
    for compute in program.measurement_computes:
        demand = f"compute:{compute.id.qualified_name}"
        for value_input in compute.value_inputs:
            observation_value_demands.setdefault(value_input.value_id, []).append(
                demand
            )
        for product_input in compute.inputs:
            observation_product_demands.setdefault(product_input.product_id, []).append(
                demand
            )

    host_result_demands: dict[object, list[str]] = {}
    for operation in host_operations.values():
        demand = f"compute:{operation.logical_compute_node_id}"
        for input_value in operation.inputs.values():
            if isinstance(input_value, OutputInput):
                host_result_demands.setdefault(input_value.value_id, []).append(demand)

    def host_demands(operation: ComputeOperation) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *(host_result_demands.get(operation.result.id, ())),
                    *(observation_value_demands.get(operation.result.id, ())),
                    *(
                        (value_record_demands[operation.result.id],)
                        if operation.result.id in value_record_demands
                        else ()
                    ),
                    *(
                        ("effect-payload",)
                        if operation.payload_slot is not None
                        else ()
                    ),
                )
            )
        )

    bundle_fields_by_source: dict[object, list[ComputeOperation]] = {}
    bundle_field_ids: set[str] = set()
    for operation in host_operations.values():
        if operation.implementation_id != _BUNDLE_FIELD_IMPLEMENTATION:
            continue
        source = operation.inputs.get("bundle")
        if not isinstance(source, OutputInput):
            continue
        bundle_fields_by_source.setdefault(source.value_id, []).append(operation)
        bundle_field_ids.add(operation.logical_compute_node_id)

    computes: list[ExperimentPreviewCompute] = []
    for operation in host_operations.values():
        if operation.logical_compute_node_id in bundle_field_ids:
            continue
        bundle_fields = tuple(bundle_fields_by_source.get(operation.result.id, ()))
        internal_demands = {
            f"compute:{field.logical_compute_node_id}" for field in bundle_fields
        }
        demands = tuple(
            dict.fromkeys(
                demand
                for demand in (
                    *host_demands(operation),
                    *(
                        demand
                        for field in bundle_fields
                        for demand in host_demands(field)
                    ),
                )
                if demand not in internal_demands
            )
        )
        computes.append(
            ExperimentPreviewCompute(
                id=operation.logical_compute_node_id,
                placement="host",
                implementation=operation.implementation_id,
                deterministic=operation.deterministic,
                inputs=tuple(operation.inputs),
                outputs=(
                    tuple(
                        field.logical_compute_node_id.rsplit(".outputs.", 1)[-1]
                        for field in bundle_fields
                    )
                    or (operation.logical_compute_node_id,)
                ),
                demanded_by=demands or ("experiment-effects",),
                captures=compute_capture_names_internal(operation.kernel),
            )
        )

    for compute in program.measurement_computes:
        demands = tuple(
            dict.fromkeys(
                demand
                for output in compute.outputs
                for demand in (
                    *product_record_demands.get(output.product_id, ()),
                    *observation_product_demands.get(output.product_id, ()),
                )
            )
        )
        computes.append(
            ExperimentPreviewCompute(
                id=compute.id.qualified_name,
                placement="observation",
                implementation=(
                    compute.implementation or f"python:{compute.id.qualified_name}"
                ),
                deterministic=compute.deterministic,
                inputs=(
                    *(input.id for input in compute.inputs),
                    *(input.id for input in compute.value_inputs),
                ),
                outputs=tuple(
                    output.product_id.qualified_name for output in compute.outputs
                ),
                demanded_by=demands,
                captures=compute.captures,
            )
        )
    return tuple(computes)


def _preview_transient_products(
    program: RunProgram,
) -> tuple[ExperimentPreviewTransientProduct, ...]:
    retained = {
        record.product_id
        for record in program.measurements.records
        if isinstance(record, RecordPlan)
    }
    retained.update(
        member.product_id
        for record in program.measurements.records
        if isinstance(record, EntityRecordPlan)
        for member in record.members
    )
    demanded = {
        item.product_id
        for compute in program.measurement_computes
        for item in compute.inputs
    }
    return tuple(
        ExperimentPreviewTransientProduct(
            id=product.id.qualified_name,
            unit=product.unit,
            dtype=product.dtype,
            dims=tuple(axis.dimension_id for axis in product.axes),
            shape=tuple(axis.size for axis in product.axes),
        )
        for product in program.measurements.catalog.product_defs
        if product.id in demanded and product.id not in retained
    )
