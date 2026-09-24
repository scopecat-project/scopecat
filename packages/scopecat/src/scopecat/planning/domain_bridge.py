"""Project bound plans into the public domain compiler SDK."""

from __future__ import annotations

from typing import cast

from scopecat.compiler.bind import BoundPlan
from scopecat.domain.program import DomainProgramDef
from scopecat.inspection import CompiledProgramInspectionQuery
from scopecat.kernel.product_identity import ProductId, ProductUseId
from scopecat.measurements.products import ProductDef
from scopecat.planning.measurement_projection import (
    project_measurement_catalog,
)
from scopecat.planning.point_materialization import MaterializedBoundPoints
from scopecat.program.logical import LogicalDomainExecution
from scopecat.records.parameter_read import DomainInputParameterRead
from scopecat.sdk.domain._identities import product_use_id
from scopecat.sdk.domain.batch import (
    DomainBatchInputs,
    DomainBatchRequest,
)
from scopecat.sdk.domain.view import (
    DomainCallView,
    DomainInputPortView,
    DomainPointRef,
    DomainProductAxisView,
    DomainProductContractView,
    DomainProductUseRef,
    DomainProgramView,
    DomainResultBindingView,
    DomainResultPortView,
)


def make_domain_call_view(
    bound: BoundPlan,
    execution_id: str,
    product_use_ids: tuple[ProductUseId, ...],
) -> DomainCallView:
    """Project static domain semantics once before bounded compilation."""

    execution = next(
        item
        for item in bound.program.program.domain_executions
        if item.id == execution_id
    )
    (
        product_contracts,
        product_use_refs,
        product_use_refs_by_id,
    ) = _project_domain_assets(bound)
    owned_use_ids = set(product_use_ids)
    return DomainCallView(
        id=execution.id,
        program=_domain_program_view(execution.program),
        results=_domain_result_views(
            bound,
            execution,
            product_contracts,
            product_use_refs_by_id,
        ),
        product_uses=tuple(
            product_use
            for product_use in product_use_refs
            if product_use_id(product_use) in owned_use_ids
        ),
    )


def make_domain_batch_request(
    call: DomainCallView,
    bound_points: MaterializedBoundPoints,
    point_ordinals: tuple[int, ...],
    *,
    legal_cut_offsets: tuple[int, ...],
    batch_ordinal: int,
    inspection_requested: bool = False,
    inspection_query: CompiledProgramInspectionQuery | None = None,
) -> DomainBatchRequest:
    """Resolve every input and project one complete bounded batch."""

    program_input_ids = tuple(port.id for port in call.program.inputs)
    compiler_input_ids = tuple(port.id for port in call.program.compiler_inputs)
    parameter_reads: list[DomainInputParameterRead] = []
    inputs = DomainBatchInputs(
        program=bound_points.bind_domain_inputs(
            call.id,
            "program",
            program_input_ids,
            point_ordinals,
            parameter_reads=parameter_reads,
        ),
        compiler=bound_points.bind_domain_inputs(
            call.id,
            "compiler",
            compiler_input_ids,
            point_ordinals,
            parameter_reads=parameter_reads,
        ),
        parameter_reads=tuple(parameter_reads),
        binding_parameter_reads=bound_points.bound_plan.bindings.parameter_reads,
    )
    selected_points = tuple(
        bound_points.point_domain.points[ordinal] for ordinal in point_ordinals
    )
    point_refs = tuple(
        DomainPointRef(
            id=point.logical_id.value,
            ordinal=point.logical_ordinal,
            native=point.logical_id,
        )
        for point in selected_points
    )
    return DomainBatchRequest(
        batch_ordinal=batch_ordinal,
        call=call,
        inputs=inputs,
        points=point_refs,
        legal_cut_offsets=legal_cut_offsets,
        measurement_catalog=project_measurement_catalog(bound_points),
        base_parameters=bound_points.bound_plan.environment.config.parameter_snapshot,
        parameters=tuple(
            bound_points.parameter_snapshot(ordinal) for ordinal in point_ordinals
        ),
        inspection_requested=inspection_requested,
        inspection_query=inspection_query,
    )


def _product_contract_view(product: ProductDef) -> DomainProductContractView:
    return DomainProductContractView(
        id=product.id.qualified_name,
        unit=product.unit,
        dtype=product.dtype,
        axes=tuple(
            DomainProductAxisView(
                id=axis.id,
                dimension_id=axis.dimension_id,
                dimension_label=axis.dimension_label,
                kind=axis.kind,
                size=axis.size,
                unit=axis.unit,
                metadata=axis.metadata,
            )
            for axis in product.axes
        ),
        metadata=product.metadata,
    )


def _domain_program_view(program: DomainProgramDef) -> DomainProgramView:
    return DomainProgramView(
        id=program.symbol_id.qualified_name,
        dialect_id=program.dialect_id,
        dialect_version=program.dialect_version,
        body=program.body,
        inputs=tuple(
            DomainInputPortView(port.id, port.value_type)
            for port in program.input_ports
        ),
        compiler_inputs=tuple(
            DomainInputPortView(port.id, port.value_type)
            for port in program.compiler_input_ports
        ),
        results=tuple(
            DomainResultPortView(port.id, port.contract)
            for port in program.result_ports
        ),
    )


def _domain_result_views(
    bound: BoundPlan,
    execution: LogicalDomainExecution,
    product_contracts: dict[ProductId, DomainProductContractView],
    product_use_refs: dict[ProductUseId, DomainProductUseRef],
) -> tuple[DomainResultBindingView, ...]:
    return tuple(
        DomainResultBindingView(
            id=result_id,
            product=product_contracts[product_id],
            product_uses=tuple(
                product_use_refs[use_id]
                for use_id in bound.bindings.domain_result_use_ids[
                    (execution.id, result_id)
                ]
            ),
            contract=next(
                port.contract
                for port in execution.program.result_ports
                if port.id == result_id
            ),
        )
        for result_id, product_id in execution.results
    )


def _project_domain_assets(
    bound: BoundPlan,
) -> tuple[
    dict[ProductId, DomainProductContractView],
    tuple[DomainProductUseRef, ...],
    dict[ProductUseId, DomainProductUseRef],
]:
    product_contracts = {
        product.id: _product_contract_view(product)
        for product in bound.bindings.product_defs
    }
    product_use_refs = tuple(
        DomainProductUseRef(
            id=use.id.value,
            product=product_contracts[use.product_id],
            native=use.id,
        )
        for use in bound.bindings.product_uses
    )
    product_use_refs_by_id = {
        cast("ProductUseId", ref.native): ref for ref in product_use_refs
    }
    return (
        product_contracts,
        product_use_refs,
        product_use_refs_by_id,
    )
