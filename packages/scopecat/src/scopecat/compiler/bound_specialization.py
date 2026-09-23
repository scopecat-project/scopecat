"""Configuration specialization over bound program facts."""

from __future__ import annotations

from dataclasses import replace

from scopecat.compiler.bound_facts import (
    BoundProgramFacts,
    LogicalResourceRequirement,
)
from scopecat.compiler.frontend.logical_verification import VerifiedLogicalProgram
from scopecat.compiler.parameter_overlays import (
    parameter_cell_bindings,
)
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.relations.specialization import (
    ParameterCellBinding,
    specialize_scalar_expression,
)
from scopecat.kernel.graph_identity import ValueId
from scopecat.program.expressions import ScalarExpr
from scopecat.program.logical import LogicalInvocation
from scopecat.program.point_domain import (
    map_point_axis_centers,
)
from scopecat.program.value_graph import OperationId
from scopecat.records.parameter_read import BindingParameterRead


def specialize_bound_facts(
    logical: VerifiedLogicalProgram,
    program: BoundProgramFacts,
    *,
    parameters: ParameterRelationData,
) -> BoundProgramFacts:
    """Partially evaluate pure values across one bound fact set."""

    recorder = ParameterReadRecorder()
    for prior in program.parameter_reads:
        if prior.phase == "specialization":
            recorder.include(prior.evidence)
    base_known = EvalContext(params=parameters, parameter_reads=recorder)
    parameter_cells = parameter_cell_bindings(program.parameter_overlays)
    known = base_known
    specialized = replace(
        program,
        point_domain=_specialize_point_domain(
            program.point_domain,
            known=base_known,
        ),
        resource_requirements=tuple(
            _specialize_resource_requirement(
                requirement,
                known=known,
                parameter_cells=parameter_cells,
            )
            for requirement in program.resource_requirements
        ),
        live_compute_ids=_live_compute_ids(logical, program),
        value_overrides=_specialize_value_overrides(
            logical,
            program,
            known=known,
            parameter_cells=parameter_cells,
        ),
    )
    return replace(
        specialized,
        parameter_reads=(
            *(
                read
                for read in program.parameter_reads
                if read.phase != "specialization"
            ),
            BindingParameterRead(phase="specialization", evidence=recorder.snapshot()),
        ),
    )


def _specialize_point_domain(
    domain: PointDomain,
    *,
    known: EvalContext,
) -> PointDomain:
    """Materialize axis centers from base configuration before point overlays.

    Parameter overlays consume coordinates produced by the point domain. If an
    overlay's residual cell binding were fed back into an around-axis center,
    the centered scan would depend on its own point column instead of the
    accepted snapshot.
    """

    def specialize_center(
        center: ScalarExpr,
        _path: tuple[str | int, ...],
    ) -> ScalarExpr:
        return _specialize_scalar_value(
            center,
            known=known,
            parameter_cells=(),
        )

    return replace(
        domain,
        axes=map_point_axis_centers(domain.axes, specialize_center),
    )


def _live_compute_ids(
    logical: VerifiedLogicalProgram,
    program: BoundProgramFacts,
) -> frozenset[OperationId]:
    """Keep the dependency closure of compute results observed by effects."""

    demanded = {
        state.value_id
        for state in logical.program.bindings
        if state.value_id in logical.operation_results
    }
    demanded.update(
        argument.value_id
        for effect in logical.program.effects
        if isinstance(effect, LogicalInvocation)
        for argument in effect.arguments
        if argument.value_id in logical.operation_results
    )
    demanded.update(
        record.value_id
        for record in program.value_record_uses
        if record.requires_execution
    )

    owners = {node.result_id: node for node in logical.program.compute_nodes}
    live_ids: set[OperationId] = set()
    pending = list(demanded)
    while pending:
        result_id = pending.pop()
        node = owners.get(result_id)
        if node is None or node.id in live_ids:
            continue
        live_ids.add(node.id)
        pending.extend(
            value_id
            for _name, value_id in node.inputs
            if value_id in logical.operation_results
        )
    return frozenset(live_ids)


def _specialize_scalar_value(
    value: ScalarExpr,
    *,
    known: EvalContext,
    parameter_cells: tuple[ParameterCellBinding, ...],
) -> ScalarExpr:
    return specialize_scalar_expression(
        value,
        known=known,
        parameter_cells=parameter_cells,
    )


def _specialize_resource_requirement(
    requirement: LogicalResourceRequirement,
    *,
    known: EvalContext,
    parameter_cells: tuple[ParameterCellBinding, ...],
) -> LogicalResourceRequirement:
    return replace(
        requirement,
        entity_uses=tuple(
            _specialize_scalar_value(
                use,
                known=known,
                parameter_cells=parameter_cells,
            )
            for use in requirement.entity_uses
        ),
    )


def _specialize_value_overrides(
    logical: VerifiedLogicalProgram,
    program: BoundProgramFacts,
    *,
    known: EvalContext,
    parameter_cells: tuple[ParameterCellBinding, ...],
) -> dict[ValueId, ScalarExpr]:
    """Retain only scalar expressions changed by configuration binding."""

    overrides = {
        **logical.scalar_values,
        **program.value_overrides,
    }
    return {
        value_id: specialized
        for value_id, value in overrides.items()
        if (
            specialized := _specialize_scalar_value(
                value,
                known=known,
                parameter_cells=parameter_cells,
            )
        )
        != logical.scalar_values[value_id]
    }
