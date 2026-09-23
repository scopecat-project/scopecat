"""Select point-local resources and bind them to instrument channels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace

from scopecat.compiler.bind import BoundPlan
from scopecat.compiler.bound_facts import BoundProgramFacts
from scopecat.compiler.diagnostics import compiler_problem
from scopecat.compiler.point_domain import MaterializedPoint
from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.problems import Problem, model_location
from scopecat.kernel.product_identity import ProductUse
from scopecat.kernel.resource_identity import LogicalResourcePortId
from scopecat.planning.local_values import evaluate_scalar_value
from scopecat.planning.routing import (
    ResourceBinding,
    ResourceBindingError,
    ResourcePortManifest,
)
from scopecat.program.logical import (
    AcquireEffect,
    LogicalDomainExecution,
    LogicalEnsureState,
    LogicalInvocation,
    LogicalStateAssignment,
)
from scopecat.records.instrument import CommandChannelBinding


@dataclass(frozen=True, slots=True)
class ResourceEntitySelection:
    """Point-local logical entity ids paired with one static manifest."""

    manifest: ResourcePortManifest
    entity_ids: tuple[str, ...] = ()

    def select_one(self) -> ResourceBinding:
        return self.manifest.select_one(self.entity_ids)


def select_coverage_resources(
    program: BoundProgramFacts,
    resource_ports: Mapping[LogicalResourcePortId, ResourcePortManifest],
    points: Sequence[MaterializedPoint],
    params_by_ordinal: Mapping[int, ParameterRelationData],
    problems: list[Problem],
    *,
    parameter_reads: Mapping[int, ParameterReadRecorder],
) -> dict[int, Mapping[LogicalResourcePortId, ResourceEntitySelection]]:
    """Evaluate point-local entities over the target's static port manifests."""

    return {
        point.logical_ordinal: _select_point_resources(
            program,
            resource_ports,
            point,
            params_by_ordinal[point.logical_ordinal],
            problems,
            parameter_reads[point.logical_ordinal],
        )
        for point in points
    }


def select_resources(
    program: BoundProgramFacts,
    resource_ports: Mapping[LogicalResourcePortId, ResourcePortManifest],
    *,
    ctx: EvalContext,
    context: str,
    problems: list[Problem],
    selected_port_ids: AbstractSet[LogicalResourcePortId] | None = None,
) -> Mapping[LogicalResourcePortId, ResourceEntitySelection]:
    selected: dict[LogicalResourcePortId, ResourceEntitySelection] = {}
    for requirement in program.resource_requirements:
        if (
            selected_port_ids is not None
            and requirement.port_id not in selected_port_ids
        ):
            continue
        manifest = resource_ports.get(requirement.port_id)
        if manifest is None:
            continue
        entity_values: list[object] = []
        failed = False
        for use in requirement.entity_uses:
            try:
                entity_values.append(evaluate_scalar_value(use, ctx))
            except (ArithmeticError, KeyError, TypeError, ValueError) as error:
                failed = True
                problems.append(
                    compiler_problem(
                        "experiment_resource_entity_evaluation_failed",
                        f"resource {requirement.port_id.qualified_name} entity "
                        f"expression failed for {context}: {error}",
                        model_location(
                            "resources",
                            requirement.port_id.qualified_name,
                        ),
                    )
                )
        if failed:
            continue
        try:
            resource = ResourceEntitySelection(
                manifest=manifest,
                entity_ids=_normalize_entity_ids(entity_values),
            )
        except ResourceBindingError as error:
            problems.append(
                compiler_problem(
                    error.code,
                    str(error),
                    model_location(
                        "resources",
                        requirement.port_id.qualified_name,
                    ),
                )
            )
            continue
        selected[resource.manifest.port_id] = resource
    return selected


def active_resource_port_ids(
    bound: BoundPlan,
    *,
    product_uses: Sequence[ProductUse],
) -> frozenset[LogicalResourcePortId]:
    """Return ports consumed by effects that survive product demand closure."""

    demanded_products = {use.product_id for use in product_uses}
    selected: set[LogicalResourcePortId] = set()
    for effect in bound.program.program.effects:
        if isinstance(effect, AcquireEffect):
            if any(
                product_id in demanded_products for product_id in effect.product_ids
            ):
                selected.add(effect.resource_port_id)
        elif isinstance(effect, LogicalInvocation):
            selected.add(effect.port_id)
        elif not isinstance(effect, LogicalDomainExecution):
            selected.update(_state_resource_port_ids(effect))
    if bound.program.program.success_state is not None:
        selected.update(_state_resource_port_ids(bound.program.program.success_state))
    return frozenset(selected)


def bind_single_resource(
    target: LogicalResourcePortId,
    *,
    resources: Mapping[LogicalResourcePortId, ResourceEntitySelection],
    missing_code: str,
) -> ResourceBinding:
    resource = resources.get(target)
    if resource is None:
        raise ResourceBindingError(
            missing_code,
            f"logical resource port {target.qualified_name!r} is not bound",
        )
    return resource.select_one()


def bind_interface_resource(
    target: LogicalResourcePortId,
    *,
    interface_id: InterfaceId,
    resources: Mapping[LogicalResourcePortId, ResourceEntitySelection],
    missing_code: str,
) -> ResourceBinding:
    resource = resources.get(target)
    if resource is None:
        raise ResourceBindingError(
            missing_code,
            f"logical resource port {target.qualified_name!r} is not bound",
        )
    binding = resource.select_one()
    endpoints = tuple(
        endpoint
        for endpoint in binding.endpoints
        if endpoint.interface_id == interface_id
    )
    component_paths = tuple(
        dict.fromkeys(endpoint.component_path for endpoint in endpoints)
    )
    if len(component_paths) != 1:
        rendered = ", ".join("/".join(path) or "<root>" for path in component_paths)
        raise ResourceBindingError(
            "resource_interface_component_ambiguous",
            f"logical resource port {target.qualified_name!r} maps interface "
            f"{interface_id!r} to multiple physical components: {rendered}",
        )
    channel_bindings = tuple(
        channel_binding
        for channel_binding in binding.channel_bindings
        if (
            channel_binding.interface_id is None
            or channel_binding.interface_id == interface_id
        )
    )
    entity_ids = binding.entity_ids or tuple(
        dict.fromkeys(channel_binding.entity_id for channel_binding in channel_bindings)
    )
    return replace(
        binding,
        component_path=component_paths[0],
        entity_ids=entity_ids,
        channel_bindings=channel_bindings,
    )


def collection_channel_bindings(
    bindings: Sequence[CommandChannelBinding],
    *,
    interface_id: InterfaceId,
) -> tuple[CommandChannelBinding, ...]:
    return tuple(
        binding for binding in bindings if binding.interface_id == interface_id
    )


def _select_point_resources(
    program: BoundProgramFacts,
    resource_ports: Mapping[LogicalResourcePortId, ResourcePortManifest],
    point: MaterializedPoint,
    params: ParameterRelationData,
    problems: list[Problem],
    parameter_reads: ParameterReadRecorder,
) -> Mapping[LogicalResourcePortId, ResourceEntitySelection]:
    return select_resources(
        program,
        resource_ports,
        ctx=EvalContext(
            params=params, point_row=point.row, parameter_reads=parameter_reads
        ),
        context=f"point {point.logical_ordinal}",
        problems=problems,
    )


def _normalize_entity_ids(values: Sequence[object]) -> tuple[str, ...]:
    entity_ids: list[str] = []
    for value in values:
        if isinstance(value, EntityRef):
            if not value.id:
                raise ResourceBindingError(
                    "module_resource_entity_invalid",
                    "resource entity id must be non-empty",
                )
            entity_ids.append(value.id)
        elif isinstance(value, str) and value:
            entity_ids.append(value)
        else:
            raise ResourceBindingError(
                "module_resource_entity_invalid",
                f"resource entity must resolve to an entity reference, got {value!r}",
            )
    return tuple(dict.fromkeys(entity_ids))


def _state_resource_port_ids(
    state: LogicalStateAssignment | LogicalEnsureState,
) -> tuple[LogicalResourcePortId, ...]:
    if isinstance(state, LogicalStateAssignment):
        return (state.port_id,)
    return tuple(assignment.port_id for assignment in state.assignments)
