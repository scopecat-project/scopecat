"""Bind desired state and invocation effects for local execution."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import cast

from scopecat.compiler.bind import BoundPlan
from scopecat.compiler.diagnostics import compiler_problem
from scopecat.compiler.point_domain import MaterializedPoint
from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.value_resolution import resolve_bound_value
from scopecat.execution.local.program import (
    ApplyStateOperation,
    InvokeOperation,
    ResourceProvenance,
    StateDemandOrigin,
    StateTarget,
)
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.problems import ModelLocation, Problem, model_location
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import LogicalResourcePortId
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.kernel.value_identity import scalar_values_equal
from scopecat.kernel.value_types import Scalar
from scopecat.planning.local_effects import (
    StateRecord,
    evaluate_effect_value,
    evaluate_state_assignment,
)
from scopecat.planning.local_resources import (
    ResourceEntitySelection,
    bind_interface_resource,
)
from scopecat.planning.routing import ResourceBindingError
from scopecat.program.expressions import ComputeResultScalarExpr, ScalarExpr
from scopecat.program.logical import LogicalInvocation, LogicalStateAssignment
from scopecat.records.content import CommandPayload
from scopecat.sdk.instruments.commands import InstrumentOperationArgument
from scopecat.sdk.payloads import PayloadCodecRegistry

type _PhysicalPropertyKey = tuple[InterfaceId, tuple[str, ...], str]


@dataclass(frozen=True, slots=True)
class _StateDemand:
    interface_id: InterfaceId
    component_path: tuple[str, ...]
    property_id: str
    value: StateValue
    origin: StateDemandOrigin


def bound_scalar_value(bound: BoundPlan, value_id: ValueId) -> ScalarExpr:
    value = resolve_bound_value(bound.program, bound.bindings, value_id)
    if not isinstance(value, ScalarExpr):
        raise AssertionError("verified effect values must be scalar")
    return value


def evaluate_state_records(
    state: LogicalStateAssignment,
    bound: BoundPlan,
    effect_index: int,
    point: MaterializedPoint,
    params: ParameterRelationData,
    *,
    problems: list[Problem],
    parameter_reads: ParameterReadRecorder | None = None,
) -> tuple[StateRecord, ...]:
    ctx = EvalContext(
        params=params, point_row=point.row, parameter_reads=parameter_reads
    )
    try:
        return (
            evaluate_state_assignment(
                state,
                bound_scalar_value(bound, state.value_id),
                cast("Scalar", bound.program.value_types[state.value_id]),
                point_index=point.logical_ordinal,
                ctx=ctx,
            ),
        )
    except (ArithmeticError, KeyError, TypeError, ValueError) as error:
        problems.append(
            compiler_problem(
                "experiment_state_evaluation_failed",
                f"state binding failed for point {point.logical_ordinal}: {error}",
                model_location("effects", effect_index),
            )
        )
        return ()


def bind_desired_state(
    records: Sequence[StateRecord],
    *,
    point_uid: str,
    state_group_index: int,
    resources: Mapping[LogicalResourcePortId, ResourceEntitySelection],
    point_index: int,
    payload_ids: Mapping[ValueId, str],
    known_compute_results: AbstractSet[ValueId],
    problems: list[Problem],
    state_context: str | None = None,
    state_location: ModelLocation | None = None,
) -> tuple[ApplyStateOperation, ...]:
    selected_context = state_context or f"point {point_index}"
    selected_location = state_location or model_location(
        "points",
        point_index,
        "desired_state",
    )
    grouped: dict[str, dict[_PhysicalPropertyKey, list[_StateDemand]]] = {}
    for record in records:
        interface_id = record.interface_id
        component_path = record.component_path
        property_id = record.property_id
        if isinstance(record.value, ComputeResultScalarExpr):
            result_id = record.value.value_id
            if result_id not in known_compute_results:
                problems.append(
                    compiler_problem(
                        "compute_payload_unknown_output",
                        "state references unknown compute result "
                        f"{result_id.qualified_name!r}",
                        selected_location,
                    )
                )
                continue
            payload_id = payload_ids.get(result_id)
            if payload_id is None:
                problems.append(
                    compiler_problem(
                        "compute_payload_unavailable",
                        "state compute output is not an available payload: "
                        f"{result_id.qualified_name!r}",
                        selected_location,
                    )
                )
                continue
            selected_value = StateValue(PayloadRef(payload_id=payload_id))
        else:
            selected_value = _state_value(record.value)
        if selected_value is None:
            problems.append(
                compiler_problem(
                    "state_value_unsupported",
                    "state values must be finite numbers, quantities, "
                    "strings, booleans, or available compute payloads",
                    model_location("desired_state", "value"),
                )
            )
            continue
        try:
            binding = bind_interface_resource(
                record.resource_target,
                interface_id=interface_id,
                resources=resources,
                missing_code="state_resource_port_unbound",
            )
        except ResourceBindingError as error:
            problems.append(
                compiler_problem(
                    error.code,
                    str(error),
                    model_location("desired_state", "resource_port_id"),
                )
            )
            continue
        component_path = (*binding.component_path, *component_path)
        group = grouped.setdefault(binding.instrument_id, {})
        key = (interface_id, component_path, property_id)
        group.setdefault(key, []).append(
            _StateDemand(
                interface_id=interface_id,
                component_path=component_path,
                property_id=property_id,
                value=selected_value,
                origin=StateDemandOrigin(
                    resource=ResourceProvenance(
                        logical_port_id=binding.port_id,
                        requested_role=binding.requested_role,
                        route_id=binding.route_id,
                        route_role_id=binding.route_role_id,
                    ),
                    entity_ids=binding.entity_ids,
                    channel_bindings=binding.channel_bindings,
                ),
            ),
        )
    operations: list[ApplyStateOperation] = []
    for instrument_id, targets in grouped.items():
        merged: list[StateTarget] = []
        for demands in targets.values():
            first = demands[0]
            if any(
                not _state_values_equal(first.value, demand.value)
                for demand in demands[1:]
            ):
                rendered_path = "/".join(first.component_path) or "<root>"
                rendered_demands = "; ".join(
                    _format_state_demand(demand) for demand in demands
                )
                problems.append(
                    compiler_problem(
                        "experiment_conflicting_desired_state",
                        f"{instrument_id}.{first.interface_id}/{rendered_path}."
                        f"{first.property_id} has conflicting demands at "
                        f"{selected_context}: {rendered_demands}",
                        selected_location,
                    )
                )
                continue
            merged.append(
                StateTarget(
                    interface_id=first.interface_id,
                    component_path=first.component_path,
                    property_id=first.property_id,
                    value=first.value,
                    origins=tuple(demand.origin for demand in demands),
                )
            )
        if merged:
            operations.append(
                ApplyStateOperation(
                    operation_id=(
                        f"{point_uid}.state.{state_group_index}.{instrument_id}"
                    ),
                    instrument_id=instrument_id,
                    targets=tuple(merged),
                )
            )
    return tuple(operations)


def _state_values_equal(left: StateValue, right: StateValue) -> bool:
    left_value = left.root
    right_value = right.root
    if isinstance(left_value, PayloadRef) or isinstance(right_value, PayloadRef):
        return left_value == right_value
    return scalar_values_equal(left_value, right_value)


def _format_state_demand(demand: _StateDemand) -> str:
    origin = demand.origin
    entities = ",".join(origin.entity_ids) or "<unscoped>"
    resource = origin.resource
    return (
        f"{entities} via {resource.logical_port_id.qualified_name} "
        f"(route {resource.route_id}) = {demand.value.root!r}"
    )


def bind_invocation(
    effect: LogicalInvocation,
    bound: BoundPlan,
    resources: Mapping[LogicalResourcePortId, ResourceEntitySelection],
    *,
    point_uid: str,
    point_index: int,
    ctx: EvalContext,
    payload_ids: Mapping[ValueId, str],
    known_compute_results: AbstractSet[ValueId],
    payload_codecs: PayloadCodecRegistry,
    problems: list[Problem],
) -> InvokeOperation | None:
    try:
        binding = bind_interface_resource(
            effect.port_id,
            interface_id=effect.interface_id,
            resources=resources,
            missing_code="invocation_resource_port_unbound",
        )
    except ResourceBindingError as error:
        problems.append(
            compiler_problem(
                error.code,
                str(error),
                model_location("invocations", effect.qualified_name, "resource"),
            )
        )
        return None

    arguments: list[InstrumentOperationArgument] = []
    payloads: list[CommandPayload] = []
    for argument in effect.arguments:
        try:
            value = evaluate_effect_value(
                bound_scalar_value(bound, argument.value_id),
                cast("Scalar", bound.program.value_types[argument.value_id]),
                ctx=ctx,
            )
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            problems.append(
                compiler_problem(
                    "instrument_invocation_argument_evaluation_failed",
                    f"invocation {effect.qualified_name!r} argument "
                    f"{argument.id!r} failed for point {point_index}: {error}",
                    model_location(
                        "invocations",
                        effect.qualified_name,
                        "arguments",
                        argument.id,
                    ),
                )
            )
            continue
        if isinstance(value, ComputeResultScalarExpr):
            if value.value_id not in known_compute_results:
                problems.append(
                    compiler_problem(
                        "compute_payload_unknown_output",
                        "invocation references unknown compute result "
                        f"{value.value_id.qualified_name!r}",
                        model_location(
                            "invocations",
                            effect.qualified_name,
                            "arguments",
                            argument.id,
                        ),
                    )
                )
                continue
            payload_id = payload_ids.get(value.value_id)
            if payload_id is None:
                problems.append(
                    compiler_problem(
                        "compute_payload_unavailable",
                        "invocation compute output is not an available payload: "
                        f"{value.value_id.qualified_name!r}",
                        model_location(
                            "invocations",
                            effect.qualified_name,
                            "arguments",
                            argument.id,
                        ),
                    )
                )
                continue
            selected_value = StateValue(PayloadRef(payload_id=payload_id))
        elif isinstance(value, PayloadValue):
            payload_id = (
                f"{point_uid}.invoke.{effect.qualified_name}.arguments."
                f"{argument.id}.payload"
            )
            try:
                payload = payload_codecs.command_payload(
                    payload_id,
                    value.schema_id,
                    value.payload,
                )
            except Exception as error:
                problems.append(
                    compiler_problem(
                        "instrument_invocation_payload_encoding_failed",
                        f"invocation {effect.qualified_name!r} argument "
                        f"{argument.id!r} payload encoding failed: {error}",
                        model_location(
                            "invocations",
                            effect.qualified_name,
                            "arguments",
                            argument.id,
                        ),
                    )
                )
                continue
            payloads.append(payload)
            selected_value = StateValue(PayloadRef(payload_id=payload_id))
        else:
            selected_value = _state_value(value)
            if selected_value is None:
                problems.append(
                    compiler_problem(
                        "instrument_invocation_argument_unsupported",
                        f"invocation {effect.qualified_name!r} argument "
                        f"{argument.id!r} must be a finite scalar value",
                        model_location(
                            "invocations",
                            effect.qualified_name,
                            "arguments",
                            argument.id,
                        ),
                    )
                )
                continue
        arguments.append(
            InstrumentOperationArgument(id=argument.id, value=selected_value)
        )
    if len(arguments) != len(effect.arguments):
        return None
    return InvokeOperation(
        effect_id=f"{point_uid}.invoke.{effect.qualified_name}",
        instrument_id=binding.instrument_id,
        resource_id=binding.instrument_id,
        interface_id=effect.interface_id,
        component_path=(*binding.component_path, *effect.component_path),
        operation_id=effect.operation_id,
        arguments=tuple(arguments),
        resource=ResourceProvenance(
            logical_port_id=binding.port_id,
            requested_role=binding.requested_role,
            route_id=binding.route_id,
            route_role_id=binding.route_role_id,
        ),
        payloads=tuple(payloads),
        entity_ids=binding.entity_ids,
        channel_bindings=binding.channel_bindings,
    )


def _state_value(value: object) -> StateValue | None:
    if isinstance(value, Quantity):
        return StateValue(value) if math.isfinite(value.value) else None
    if isinstance(value, bool | int | str):
        return StateValue(value)
    if isinstance(value, float):
        return StateValue(value) if math.isfinite(value) else None
    return None
