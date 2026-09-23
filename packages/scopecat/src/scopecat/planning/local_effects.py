"""Planning-only materialization of concrete host effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from scopecat.compiler.bind import BoundPlan
from scopecat.compiler.relations.context import EvalContext
from scopecat.compiler.relations.evaluation import evaluate_scalar
from scopecat.execution.local.program import (
    ApplyStateOperation,
    CollectOperation,
    InvokeOperation,
    LocalOperation,
)
from scopecat.execution.program import RunCoverageEffect
from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.product_identity import ProductUse
from scopecat.kernel.resource_identity import LogicalResourcePortId, ResourceRequirement
from scopecat.kernel.value_data import CellValue
from scopecat.kernel.value_types import Scalar
from scopecat.measurements.records import EntityAcquisitionCohortPlan
from scopecat.planning.routing import ResourcePortManifest
from scopecat.program.expressions import ComputeResultScalarExpr, ScalarExpr
from scopecat.program.logical import LogicalStateAssignment
from scopecat.records.parameter_read import BindingParameterRead, HostPointParameterRead
from scopecat.sdk.payloads import PayloadCodecRegistry

type EvaluatedEffectValue = CellValue | ComputeResultScalarExpr


@dataclass(frozen=True, slots=True)
class StateRecord:
    point_index: int
    resource_target: LogicalResourcePortId
    interface_id: InterfaceId
    property_id: str
    value: EvaluatedEffectValue
    component_path: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        component = "/".join(self.component_path)
        prefix = f"{self.interface_id}/{component}" if component else self.interface_id
        return f"{prefix}.{self.property_id}"


def evaluate_effect_value(
    value: ScalarExpr,
    value_type: Scalar,
    *,
    ctx: EvalContext,
) -> EvaluatedEffectValue:
    return (
        value
        if isinstance(value, ComputeResultScalarExpr)
        else evaluate_scalar(value, ctx, expected_type=value_type)
    )


def evaluate_state_assignment(
    assignment: LogicalStateAssignment,
    value: ScalarExpr,
    value_type: Scalar,
    *,
    point_index: int,
    ctx: EvalContext,
) -> StateRecord:
    """Materialize one data-only state record."""

    return StateRecord(
        point_index=point_index,
        resource_target=assignment.port_id,
        interface_id=assignment.interface_id,
        component_path=assignment.component_path,
        property_id=assignment.property_id,
        value=evaluate_effect_value(value, value_type, ctx=ctx),
    )


@dataclass(frozen=True, slots=True)
class LocalTargetPlan:
    """One closed local target selection reused by every coverage block.

    ``bound`` pairs the canonical logical program with its binding facts;
    ``product_uses`` is the
    local side of the local/domain demand cut.
    Physical manifests are selected once so bounded coverage evaluates only
    point-local values and entity selections, never the accepted configuration
    or provider inventory again.
    """

    bound: BoundPlan
    product_uses: tuple[ProductUse, ...]
    instrument_order: tuple[str, ...]
    resource_ports: Mapping[LogicalResourcePortId, ResourcePortManifest]
    acquisition_cohorts: tuple[EntityAcquisitionCohortPlan, ...] = ()
    payload_codecs: PayloadCodecRegistry = field(
        default_factory=PayloadCodecRegistry,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class MaterializedLocalEffects:
    """Final local effects aligned with the ordered bound effect sequence."""

    compute_operations: tuple[RunCoverageEffect, ...]
    effect_operations: tuple[tuple[RunCoverageEffect, ...], ...]
    parameter_reads: tuple[HostPointParameterRead, ...] = ()
    binding_parameter_reads: tuple[BindingParameterRead, ...] = ()


def local_operation_resource_requirements(
    operation: LocalOperation,
) -> tuple[ResourceRequirement, ...]:
    """Return the logical instrument requirement of one local operation.

    Planning maps it through the accepted configuration only after local and
    domain ownership are closed, so driver-facing IDs never become scheduler
    identities implicitly.
    """

    if isinstance(
        operation,
        ApplyStateOperation | InvokeOperation | CollectOperation,
    ):
        return (ResourceRequirement(operation.instrument_id),)
    return ()
