"""Configuration-derived facts attached to a verified logical program.

The logical program remains the sole owner of operations and effect order.
These records contain only values and selections introduced by configuration
binding; planning combines them with ``VerifiedLogicalProgram`` instead of
consuming a second copied program.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from scopecat.compiler.parameter_overlays import PointParameterOverlay
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.topology_selection import TopologyTableResolution
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.instrument_members import InstrumentCapabilityRef
from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.json_types import JsonValue
from scopecat.kernel.product_identity import (
    ProductId,
    ProductUse,
    ProductUseId,
    product_use,
)
from scopecat.kernel.resource_identity import (
    DEFAULT_RESOURCE_ROLE,
    LogicalResourcePortId,
    ResourceRoleSelector,
)
from scopecat.measurements.products import (
    ProductAxisDef,
    ProductDef,
)
from scopecat.measurements.records import (
    BoundRecordUse,
    EntityRecordUse,
    ProductRecordUse,
    RecordUse,
    ValueRecordUse,
)
from scopecat.program.expressions import ScalarExpr
from scopecat.program.logical import (
    MeasurementComputeId,
)
from scopecat.program.measurement_contracts import (
    MeasurementComputeKernel,
)
from scopecat.program.measurement_types import MeasurementVariableRole
from scopecat.program.value_graph import OperationId
from scopecat.records.parameter_read import BindingParameterRead


def _empty_value_overrides() -> dict[ValueId, ScalarExpr]:
    return {}


def _empty_topology_entity_sets() -> dict[ValueId, TopologyTableResolution]:
    return {}


def _empty_domain_result_use_ids() -> dict[tuple[str, str], tuple[ProductUseId, ...]]:
    return {}


@dataclass(frozen=True, slots=True)
class BoundMeasurementComputeOutput:
    """One calculated product and all of its downstream use slots."""

    id: str
    product_id: ProductId
    product_use_ids: tuple[ProductUseId, ...] = ()


@dataclass(frozen=True, slots=True)
class BoundMeasurementComputeInput:
    """One named measured product consumed by a point-local computation."""

    id: str
    product_id: ProductId
    product_use_id: ProductUseId


@dataclass(frozen=True, slots=True)
class BoundMeasurementComputeValueInput:
    """One named early value consumed after measurements become available."""

    id: str
    value_id: ValueId


@dataclass(frozen=True, slots=True)
class BoundMeasurementCompute:
    """One live point-local compute retained by record demand."""

    id: MeasurementComputeId
    inputs: tuple[BoundMeasurementComputeInput, ...]
    outputs: tuple[BoundMeasurementComputeOutput, ...]
    kernel: MeasurementComputeKernel = field(repr=False, compare=False)
    value_inputs: tuple[BoundMeasurementComputeValueInput, ...] = ()
    implementation: str | None = None
    deterministic: bool = False
    captures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LogicalResourceRequirement:
    """Stable logical capabilities plus point-local object selection.

    ``capabilities`` is the compile-time contract for the logical port,
    ``entity_uses`` selects its objects at each point, and ``role``
    distinguishes equivalent endpoints by lab purpose. Physical instrument and
    channel identity enter only during target materialization.
    """

    port_id: LogicalResourcePortId
    capabilities: tuple[InstrumentCapabilityRef, ...] = ()
    entity_uses: tuple[ScalarExpr, ...] = ()
    role: ResourceRoleSelector = DEFAULT_RESOURCE_ROLE

    @property
    def interfaces(self) -> tuple[InterfaceId, ...]:
        return tuple(
            dict.fromkeys(capability.interface_id for capability in self.capabilities)
        )


@dataclass(frozen=True, slots=True)
class BoundProgramFacts:
    """Transient facts introduced by binding one canonical logical program.

    These facts belong to one accepted configuration and compiler pass. They
    are neither part of the reusable program model nor a versioned wire or
    persistence format.
    """

    point_domain: PointDomain
    value_overrides: Mapping[ValueId, ScalarExpr] = field(
        default_factory=_empty_value_overrides
    )
    topology_entity_sets: Mapping[ValueId, TopologyTableResolution] = field(
        default_factory=_empty_topology_entity_sets
    )
    resource_requirements: tuple[LogicalResourceRequirement, ...] = ()
    parameter_overlays: tuple[PointParameterOverlay, ...] = ()
    live_compute_ids: frozenset[OperationId] = frozenset()
    domain_result_use_ids: Mapping[tuple[str, str], tuple[ProductUseId, ...]] = field(
        default_factory=_empty_domain_result_use_ids
    )
    measurement_computes: tuple[BoundMeasurementCompute, ...] = ()
    product_defs: tuple[ProductDef, ...] = ()
    product_uses: tuple[ProductUse, ...] = ()
    record_uses: tuple[BoundRecordUse, ...] = ()
    parameter_reads: tuple[BindingParameterRead, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_overrides", dict(self.value_overrides))
        object.__setattr__(
            self,
            "topology_entity_sets",
            dict(self.topology_entity_sets),
        )
        object.__setattr__(
            self,
            "domain_result_use_ids",
            dict(self.domain_result_use_ids),
        )

    @property
    def product_record_uses(self) -> tuple[ProductRecordUse, ...]:
        return tuple(
            record
            for record in self.record_uses
            if isinstance(record, RecordUse | EntityRecordUse)
        )

    @property
    def value_record_uses(self) -> tuple[ValueRecordUse, ...]:
        return tuple(
            record for record in self.record_uses if isinstance(record, ValueRecordUse)
        )


def product_axis(
    id: str,
    *,
    dimension_id: str,
    dimension_label: str | None = None,
    size: int | None,
    kind: str | None = None,
    unit: str | None = None,
    entities: tuple[EntityRef, ...] | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
    coordinate_product_id: ProductId | None = None,
) -> ProductAxisDef:
    return ProductAxisDef(
        id=id,
        dimension_id=dimension_id,
        dimension_label=dimension_label,
        kind=kind or id,
        size=size,
        unit=unit,
        entities=entities,
        metadata=metadata or {},
        coordinate_product_id=coordinate_product_id,
    )


def shot_axis(size: int, *, dimension_id: str) -> ProductAxisDef:
    return product_axis(
        "shot",
        dimension_id=dimension_id,
        size=size,
        kind="shot",
        unit="count",
    )


def record_product(
    product: ProductDef | ProductId,
    *,
    record_id: str | None = None,
    role: MeasurementVariableRole = "observable",
    recording_group_id: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> tuple[ProductUse, RecordUse]:
    """Create one product-use occurrence and one durable record consumer."""

    selected_id = product.id if isinstance(product, ProductDef) else product
    use = product_use(selected_id)
    return use, RecordUse(
        id=record_id or selected_id.qualified_name,
        product_use_id=use.id,
        role=role,
        recording_group_id=recording_group_id,
        metadata=metadata or {},
    )
