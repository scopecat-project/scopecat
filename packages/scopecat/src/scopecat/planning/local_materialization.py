"""Specialize bound host semantics into point-local operations."""

from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Protocol, cast

from scopecat.compiler.bind import BoundPlan
from scopecat.compiler.diagnostics import compiler_problem
from scopecat.compiler.relations.context import EvalContext
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.value_resolution import BoundValueResolver
from scopecat.execution.local.program import ApplyStateOperation, CollectOperation
from scopecat.execution.program import RunCoverageEffect
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.problems import Problem, model_location
from scopecat.kernel.product_identity import ProductUseId
from scopecat.kernel.resource_identity import LogicalResourcePortId
from scopecat.kernel.value_types import Scalar
from scopecat.measurements.records import EntityAcquisitionCohortPlan
from scopecat.planning.local_acquisition import bind_collect
from scopecat.planning.local_compute import (
    bind_compute_operations as _bind_compute_operations,
)
from scopecat.planning.local_effects import (
    LocalTargetPlan,
    MaterializedLocalEffects,
    StateRecord,
    evaluate_state_assignment,
)
from scopecat.planning.local_resources import (
    active_resource_port_ids,
    select_coverage_resources,
    select_resources,
)
from scopecat.planning.local_state_binding import (
    bind_desired_state,
    bind_invocation,
    bound_scalar_value,
    evaluate_state_records,
)
from scopecat.planning.point_materialization import MaterializedBoundPoints
from scopecat.planning.routing import (
    ResourcePortManifest,
    RoutingView,
)
from scopecat.program.expression_analysis import (
    expression_input_refs,
    expression_module_exports,
    expression_parameter_refs,
    expression_point_refs,
    expression_requires_execution,
)
from scopecat.program.expressions import ComputeResultScalarExpr, ScalarExpr
from scopecat.program.logical import (
    AcquireEffect,
    LogicalDomainExecution,
    LogicalEnsureState,
    LogicalInvocation,
    LogicalStateAssignment,
)
from scopecat.records.parameter_read import HostPointParameterRead
from scopecat.sdk.payloads import EMPTY_PAYLOAD_CODECS, PayloadCodecRegistry


class _InstrumentOperation(Protocol):
    @property
    def instrument_id(self) -> str: ...


def materialize_local_execution(
    bound_points: MaterializedBoundPoints,
    *,
    target: LocalTargetPlan,
    point_ordinals: Sequence[int] | None = None,
) -> MaterializedLocalEffects:
    """Lower one bounded point coverage into final ordered local effects."""

    bound = target.bound
    program = bound.bindings
    logical = bound.program.program
    problems: list[Problem] = []
    selected_instrument_order = target.instrument_order
    materialized_domain = bound_points.point_domain
    planner_points = materialized_domain.points
    point_count = len(planner_points)
    ordinals = (
        tuple(point.logical_ordinal for point in planner_points)
        if point_ordinals is None
        else tuple(point_ordinals)
    )
    selected_points = tuple(planner_points[ordinal] for ordinal in ordinals)
    point_by_ordinal = {point.logical_ordinal: point for point in selected_points}
    params_by_ordinal = {
        ordinal: bound_points.point_parameters[ordinal] for ordinal in ordinals
    }
    read_recorders = {ordinal: ParameterReadRecorder() for ordinal in ordinals}
    for recorder in read_recorders.values():
        recorder.incomplete("resource_topology_not_captured")
        recorder.incomplete("runtime_kernel_reads_not_captured")
    resources_by_ordinal = select_coverage_resources(
        program,
        target.resource_ports,
        selected_points,
        params_by_ordinal,
        problems,
        parameter_reads=read_recorders,
    )
    compute_nodes = tuple(
        node for node in logical.compute_nodes if node.id in program.live_compute_ids
    )
    known_compute_results = {node.result_id for node in compute_nodes}
    demanded_payload_results = {
        value.value_id
        for effect in logical.effects
        if isinstance(effect, LogicalInvocation)
        for argument in effect.arguments
        if isinstance(
            value := bound_scalar_value(bound, argument.value_id),
            ComputeResultScalarExpr,
        )
    }
    demanded_payload_results.update(
        value.value_id
        for effect in logical.effects
        for state in (
            effect.assignments
            if isinstance(effect, LogicalEnsureState)
            else (effect,)
            if isinstance(effect, LogicalStateAssignment)
            else ()
        )
        if isinstance(
            value := bound_scalar_value(bound, state.value_id),
            ComputeResultScalarExpr,
        )
    )
    payload_ids_by_ordinal: dict[int, dict[ValueId, str]] = {}
    compute_effects: list[RunCoverageEffect] = []
    for ordinal in ordinals:
        point = point_by_ordinal[ordinal]
        point_params = params_by_ordinal[ordinal]
        compute_operations, payload_ids = _bind_compute_operations(
            compute_nodes,
            logical.implementations,
            BoundValueResolver(bound.program, program),
            operation_prefix=point.logical_id.value,
            ctx=EvalContext(
                params=point_params,
                point_row=point.row,
                parameter_reads=read_recorders[ordinal],
            ),
            demanded_payload_results=demanded_payload_results,
            problems=problems,
        )
        payload_ids_by_ordinal[ordinal] = payload_ids
        compute_effects.extend(
            RunCoverageEffect(ordinal, operation) for operation in compute_operations
        )

    effect_operations: list[list[RunCoverageEffect]] = [
        [] for _effect in logical.effects
    ]
    for effect_index, effect in enumerate(logical.effects):
        if isinstance(effect, LogicalDomainExecution):
            continue
        if isinstance(effect, AcquireEffect):
            for ordinal in ordinals:
                point = point_by_ordinal[ordinal]
                resources = resources_by_ordinal[ordinal]
                collect = bind_collect(
                    program.product_defs,
                    target.product_uses,
                    effect,
                    resources,
                    point_uid=point.logical_id.value,
                    point_index=ordinal,
                    point_count=point_count,
                    problems=problems,
                )
                if collect is not None:
                    effect_operations[effect_index].append(
                        RunCoverageEffect(ordinal, collect)
                    )
            continue
        if isinstance(effect, LogicalInvocation):
            for ordinal in ordinals:
                point = point_by_ordinal[ordinal]
                invocation = bind_invocation(
                    effect,
                    bound,
                    resources_by_ordinal[ordinal],
                    point_uid=point.logical_id.value,
                    point_index=ordinal,
                    ctx=EvalContext(
                        params=params_by_ordinal[ordinal],
                        point_row=point.row,
                        parameter_reads=read_recorders[ordinal],
                    ),
                    payload_ids=payload_ids_by_ordinal[ordinal],
                    known_compute_results=known_compute_results,
                    payload_codecs=target.payload_codecs,
                    problems=problems,
                )
                if invocation is not None:
                    effect_operations[effect_index].append(
                        RunCoverageEffect(ordinal, invocation)
                    )
            continue
        if isinstance(effect, LogicalEnsureState):
            for ordinal in ordinals:
                point = point_by_ordinal[ordinal]
                point_params = params_by_ordinal[ordinal]
                resources = resources_by_ordinal[ordinal]
                desired = bind_desired_state(
                    tuple(
                        record
                        for state in effect.assignments
                        for record in evaluate_state_records(
                            state,
                            bound,
                            effect_index,
                            point,
                            point_params,
                            problems=problems,
                            parameter_reads=read_recorders[ordinal],
                        )
                    ),
                    point_uid=point.logical_id.value,
                    state_group_index=effect_index,
                    resources=resources,
                    point_index=ordinal,
                    payload_ids=payload_ids_by_ordinal[ordinal],
                    known_compute_results=known_compute_results,
                    problems=problems,
                )
                ordered = _order_instrument_operations(
                    desired,
                    instrument_order=selected_instrument_order,
                )
                effect_operations[effect_index].extend(
                    RunCoverageEffect(ordinal, operation) for operation in ordered
                )
            continue
        if effect_index and isinstance(
            logical.effects[effect_index - 1],
            LogicalStateAssignment,
        ):
            continue
        state_end = effect_index + 1
        while state_end < len(logical.effects) and isinstance(
            logical.effects[state_end],
            LogicalStateAssignment,
        ):
            state_end += 1
        state_group: list[tuple[int, LogicalStateAssignment]] = []
        for index in range(effect_index, state_end):
            state = logical.effects[index]
            if not isinstance(state, LogicalStateAssignment):
                raise AssertionError("state group contains a non-state effect")
            state_group.append((index, state))
        for ordinal in ordinals:
            point = point_by_ordinal[ordinal]
            point_params = params_by_ordinal[ordinal]
            resources = resources_by_ordinal[ordinal]
            desired = bind_desired_state(
                tuple(
                    record
                    for index, state in state_group
                    for record in evaluate_state_records(
                        state,
                        bound,
                        index,
                        point,
                        point_params,
                        problems=problems,
                        parameter_reads=read_recorders[ordinal],
                    )
                ),
                point_uid=point.logical_id.value,
                state_group_index=effect_index,
                resources=resources,
                point_index=ordinal,
                payload_ids=payload_ids_by_ordinal[ordinal],
                known_compute_results=known_compute_results,
                problems=problems,
            )
            ordered = _order_instrument_operations(
                desired,
                instrument_order=selected_instrument_order,
            )
            effect_operations[state_end - 1].extend(
                RunCoverageEffect(ordinal, operation) for operation in ordered
            )
    _validate_entity_acquisition_cohorts(
        target.acquisition_cohorts,
        effect_operations,
        point_ordinals=ordinals,
        problems=problems,
    )
    if bool(problems):
        raise CheckFailed(problems)
    return MaterializedLocalEffects(
        compute_operations=tuple(compute_effects),
        effect_operations=tuple(tuple(items) for items in effect_operations),
        parameter_reads=tuple(
            HostPointParameterRead(
                point_ordinal=ordinal, evidence=read_recorders[ordinal].snapshot()
            )
            for ordinal in ordinals
        ),
        binding_parameter_reads=program.parameter_reads,
    )


def materialize_local_success_state(
    bound: BoundPlan,
    *,
    target: LocalTargetPlan,
    parameter_reads: ParameterReadRecorder | None = None,
) -> tuple[ApplyStateOperation, ...]:
    """Materialize the fixed desired state applied after successful completion."""

    authored_success_state = target.bound.program.program.success_state
    if authored_success_state is None:
        return ()
    problems: list[Problem] = []
    ctx = EvalContext(
        params=bound.environment.parameters, parameter_reads=parameter_reads
    )
    if parameter_reads is not None:
        parameter_reads.incomplete("resource_topology_not_captured")
    resources = select_resources(
        target.bound.bindings,
        target.resource_ports,
        ctx=ctx,
        context="normal completion",
        problems=problems,
        selected_port_ids=frozenset(
            assignment.port_id for assignment in authored_success_state.assignments
        ),
    )
    records: list[StateRecord] = []
    for assignment in authored_success_state.assignments:
        try:
            records.append(
                evaluate_state_assignment(
                    assignment,
                    bound_scalar_value(target.bound, assignment.value_id),
                    cast(
                        "Scalar", target.bound.program.value_types[assignment.value_id]
                    ),
                    point_index=0,
                    ctx=ctx,
                )
            )
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            problems.append(
                compiler_problem(
                    "experiment_success_state_evaluation_failed",
                    f"experiment success_state evaluation failed: {error}",
                    model_location("success_state"),
                )
            )
    desired = bind_desired_state(
        records,
        point_uid=f"{bound.program.experiment_id}.success_state",
        state_group_index=len(target.bound.program.program.effects),
        resources=resources,
        point_index=0,
        payload_ids={},
        known_compute_results={
            node.result_id
            for node in target.bound.program.program.compute_nodes
            if node.id in target.bound.bindings.live_compute_ids
        },
        problems=problems,
        state_context="normal completion",
        state_location=model_location("success_state"),
    )
    if problems:
        raise CheckFailed(problems)
    return _order_instrument_operations(
        desired,
        instrument_order=target.instrument_order,
    )


def local_execution_is_point_invariant(target: LocalTargetPlan) -> bool:
    """Return whether the initial state probe can represent every point."""

    bound = target.bound
    logical = bound.program.program
    if bound.bindings.live_compute_ids:
        return False
    overlay_tables = {overlay.table_id for overlay in bound.bindings.parameter_overlays}

    def expression_is_invariant(value: ScalarExpr) -> bool:
        return not (
            expression_point_refs(value)
            or expression_input_refs(value)
            or expression_module_exports(value)
            or expression_requires_execution(value)
            or overlay_tables.intersection(expression_parameter_refs(value))
        )

    for requirement in bound.bindings.resource_requirements:
        if requirement.port_id not in target.resource_ports:
            continue
        if any(not expression_is_invariant(use) for use in requirement.entity_uses):
            return False
    for effect in logical.effects:
        if isinstance(effect, LogicalDomainExecution):
            continue
        if isinstance(effect, AcquireEffect | LogicalInvocation):
            return False
        assignments = (
            effect.assignments if isinstance(effect, LogicalEnsureState) else (effect,)
        )
        if any(
            not expression_is_invariant(bound_scalar_value(bound, assignment.value_id))
            for assignment in assignments
        ):
            return False
    return True


def prepare_local_target(
    bound: BoundPlan,
    *,
    product_use_ids: AbstractSet[ProductUseId],
    instrument_order: Sequence[str] = (),
    acquisition_cohorts: Sequence[EntityAcquisitionCohortPlan] = (),
    payload_codecs: PayloadCodecRegistry = EMPTY_PAYLOAD_CODECS,
) -> LocalTargetPlan:
    """Select the complete local target once for all bounded coverage.

    Product demand is closed before physical binding so an acquisition with no
    live product cannot create a spurious missing or ambiguous hardware error.
    Only surviving local effects receive static manifests.
    """

    requested = frozenset(product_use_ids)
    available = {use.id for use in bound.bindings.product_uses}
    unknown = sorted(use_id.value for use_id in requested - available)
    if unknown:
        msg = "local product selection contains unknown uses: " + ", ".join(unknown)
        raise ValueError(msg)
    product_uses = tuple(
        use for use in bound.bindings.product_uses if use.id in requested
    )
    active_resource_ports = active_resource_port_ids(
        bound,
        product_uses=product_uses,
    )
    resource_ports: dict[LogicalResourcePortId, ResourcePortManifest] = {}
    if active_resource_ports:
        physical_resources = RoutingView.from_config(bound.environment.config)
        resource_ports = {
            requirement.port_id: physical_resources.bind_port(
                port_id=requirement.port_id,
                interfaces=requirement.interfaces,
                role=requirement.role,
            )
            for requirement in bound.bindings.resource_requirements
            if requirement.port_id in active_resource_ports
        }
    return LocalTargetPlan(
        bound=bound,
        product_uses=product_uses,
        instrument_order=_validate_instrument_order(instrument_order),
        resource_ports=resource_ports,
        acquisition_cohorts=tuple(acquisition_cohorts),
        payload_codecs=payload_codecs,
    )


def _validate_entity_acquisition_cohorts(
    cohorts: Sequence[EntityAcquisitionCohortPlan],
    effect_operations: Sequence[Sequence[RunCoverageEffect]],
    *,
    point_ordinals: Sequence[int],
    problems: list[Problem],
) -> None:
    """Require every declared cohort to be collected by one hardware command."""

    command_by_use: dict[tuple[int, ProductUseId], str] = {}
    for group in effect_operations:
        for effect in group:
            operation = effect.operation
            if not isinstance(operation, CollectOperation):
                continue
            for binding in operation.result_bindings:
                for use_id in binding.product_use_ids:
                    command_by_use[(effect.point_index, use_id)] = (
                        operation.operation_id
                    )
    for cohort in cohorts:
        use_ids = tuple(
            use_id for member in cohort.members for use_id in member.product_use_ids
        )
        for point_index in point_ordinals:
            command_ids = {
                command_by_use.get((point_index, use_id)) for use_id in use_ids
            }
            if len(command_ids) == 1 and None not in command_ids:
                continue
            problems.append(
                compiler_problem(
                    "entity_acquisition_cohort_not_atomic",
                    f"entity acquisition cohort {cohort.id!r} must be collected "
                    "by one hardware command",
                    model_location(
                        "points",
                        point_index,
                        "entity_acquisition_cohorts",
                        cohort.id,
                    ),
                    details={
                        "cohort_id": cohort.id,
                        "product_use_ids": [use_id.value for use_id in use_ids],
                        "command_ids": sorted(
                            command_id
                            for command_id in command_ids
                            if command_id is not None
                        ),
                    },
                )
            )


def _validate_instrument_order(
    instrument_order: Sequence[str],
) -> tuple[str, ...]:
    selected = tuple(instrument_order)
    if len(selected) != len(set(selected)) or any(not item for item in selected):
        msg = "instrument_order must contain unique non-empty ids"
        raise ValueError(msg)
    return selected


def _order_instrument_operations[T: _InstrumentOperation](
    operations: Sequence[T],
    *,
    instrument_order: Sequence[str],
) -> tuple[T, ...]:
    by_instrument = {operation.instrument_id: operation for operation in operations}
    selected = tuple(
        by_instrument[instrument_id]
        for instrument_id in instrument_order
        if instrument_id in by_instrument
    )
    selected_ids = {operation.instrument_id for operation in selected}
    return (
        *selected,
        *sorted(
            (
                operation
                for operation in operations
                if operation.instrument_id not in selected_ids
            ),
            key=lambda operation: operation.instrument_id,
        ),
    )
