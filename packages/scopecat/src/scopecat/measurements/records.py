"""Logical record uses and their config-bound dataset projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, cast

from pydantic import JsonValue as WireJsonValue

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import FrozenMapping, freeze_json_mapping, thaw_json_value
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.json_types import JsonValue
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.point_identity import LogicalPointId, PointDomainLayout
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.kernel.product_identity import ProductId, ProductUse, ProductUseId
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_data import CellValue
from scopecat.kernel.value_types import (
    Array,
    DataType,
    Entity,
    Scalar,
    TableColumn,
)
from scopecat.measurements.products import ProductAxisDef, ProductDef
from scopecat.program.measurement_types import (
    EntityAcquisitionSemantics,
    MeasurementDType,
    MeasurementVariableRole,
    measurement_value_spec_from_scalar,
)
from scopecat.program.point_domain import (
    PointAxes,
    PointAxis,
    PointAxisRange,
    PointAxisValues,
    point_axis_size,
)
from scopecat.program.products import EntityAxisDef
from scopecat.program.recording import ExperimentResultField
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityAcquisition,
    MeasurementEntityIndex,
    MeasurementEntityProductMetadataOverride,
    MeasurementEntityProductSource,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainAxis,
    MeasurementPointDomainColumn,
    MeasurementPointDomainLinearSource,
    MeasurementPointDomainRangeSource,
    MeasurementPointDomainValuesSource,
    MeasurementProductGridPointDomain,
    MeasurementResultContract,
    MeasurementResultField,
    MeasurementScalar,
    MeasurementVariable,
    MeasurementVariableGroup,
    measurement_result_contract_version,
)


def _empty_metadata() -> FrozenMapping[str, JsonValue]:
    return FrozenMapping()


@dataclass(frozen=True, slots=True)
class RecordUse:
    """Template-owned durable destination for one logical product use."""

    id: str
    product_use_id: ProductUseId
    role: MeasurementVariableRole = "observable"
    recording_group_id: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.id:
            msg = "record use id must be non-empty"
            raise ValueError(msg)
        if self.recording_group_id is not None and not self.recording_group_id:
            msg = "recording group id must be non-empty when provided"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "metadata",
            freeze_json_mapping(self.metadata, path=f"record use {self.id!r} metadata"),
        )

    @property
    def product_use_ids(self) -> tuple[ProductUseId, ...]:
        return (self.product_use_id,)


@dataclass(frozen=True, slots=True)
class EntityRecordUseMember:
    """One bound product use aligned to a grouped record's entity axis."""

    entity: EntityRef
    product_use_id: ProductUseId


@dataclass(frozen=True, slots=True)
class EntityRecordUse:
    """One bound durable selection over homogeneous per-entity products."""

    id: str
    axis: EntityAxisDef
    members: tuple[EntityRecordUseMember, ...]
    role: MeasurementVariableRole = "observable"
    recording_group_id: str | None = None
    acquisition: EntityAcquisitionSemantics = field(
        default_factory=EntityAcquisitionSemantics
    )
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("entity record use id must be non-empty")
        if self.recording_group_id is not None and not self.recording_group_id:
            raise ValueError("recording group id must be non-empty when provided")
        members = tuple(self.members)
        if tuple(member.entity for member in members) != self.axis.values:
            raise ValueError("entity record uses must align exactly to their axis")
        object.__setattr__(self, "members", members)
        object.__setattr__(
            self,
            "metadata",
            freeze_json_mapping(self.metadata, path=f"record use {self.id!r} metadata"),
        )

    @property
    def product_use_ids(self) -> tuple[ProductUseId, ...]:
        return tuple(member.product_use_id for member in self.members)


@dataclass(frozen=True, slots=True)
class ValueRecordUse:
    """Durable destination for one data value in the logical graph."""

    id: str
    value_id: ValueId
    source_value_id: str
    value_type: DataType
    requires_execution: bool = False
    role: MeasurementVariableRole = "observable"
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("value record use id must be non-empty")
        if not self.source_value_id:
            raise ValueError("source value id must be non-empty")
        object.__setattr__(
            self,
            "metadata",
            freeze_json_mapping(
                self.metadata,
                path=f"value record use {self.id!r} metadata",
            ),
        )


@dataclass(frozen=True, slots=True)
class RecordAxisPlan:
    """Config-independent projection of one product-local measurement axis."""

    id: str
    label: str | None
    kind: str
    size: int | None
    unit: str | None = None
    index: MeasurementEntityIndex | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            freeze_json_mapping(
                self.metadata, path=f"record axis {self.id!r} metadata"
            ),
        )


@dataclass(frozen=True, slots=True)
class RecordPlan:
    """Dataset variable plan derived from one recorded product use."""

    id: str
    product_use_id: ProductUseId
    product_id: ProductId
    dtype: MeasurementDType
    role: MeasurementVariableRole = "observable"
    recording_group_id: str | None = None
    unit: str | None = None
    axes: tuple[RecordAxisPlan, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if self.recording_group_id is not None and not self.recording_group_id:
            msg = "recording group id must be non-empty when provided"
            raise ValueError(msg)
        object.__setattr__(self, "axes", tuple(self.axes))
        object.__setattr__(
            self,
            "metadata",
            freeze_json_mapping(
                self.metadata, path=f"record plan {self.id!r} metadata"
            ),
        )


@dataclass(frozen=True, slots=True)
class EntityRecordMember:
    """One ordered entity/source member of an entity-indexed record."""

    entity: EntityRef
    product_use_id: ProductUseId
    product_id: ProductId
    product_metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_metadata",
            freeze_json_mapping(
                self.product_metadata,
                path=f"entity record source {self.product_id!s} metadata",
            ),
        )


@dataclass(frozen=True, slots=True)
class EntityRecordPlan:
    """One durable variable assembled from homogeneous per-entity products."""

    id: str
    members: tuple[EntityRecordMember, ...]
    dtype: MeasurementDType
    role: MeasurementVariableRole = "observable"
    recording_group_id: str | None = None
    acquisition: EntityAcquisitionSemantics = field(
        default_factory=EntityAcquisitionSemantics
    )
    unit: str | None = None
    axes: tuple[RecordAxisPlan, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("entity record plans require at least one member")
        object.__setattr__(self, "members", tuple(self.members))
        object.__setattr__(self, "axes", tuple(self.axes))
        object.__setattr__(
            self,
            "metadata",
            freeze_json_mapping(
                self.metadata, path=f"entity record plan {self.id!r} metadata"
            ),
        )


@dataclass(frozen=True, slots=True)
class EntityAcquisitionCohortMemberPlan:
    """One recorded field participating in an entity acquisition cohort."""

    record_id: str
    product_use_ids: tuple[ProductUseId, ...]


@dataclass(frozen=True, slots=True)
class EntityAcquisitionCohortPlan:
    """One execution cohort shared by entity-indexed recorded fields."""

    id: str
    policy: Literal["best_effort", "all_or_nothing"]
    dimension_id: str
    entities: tuple[EntityRef, ...]
    members: tuple[EntityAcquisitionCohortMemberPlan, ...]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("entity acquisition cohort id must be non-empty")
        if not self.entities or not self.members:
            raise ValueError("entity acquisition cohorts require entities and members")
        entity_count = len(self.entities)
        if any(len(member.product_use_ids) != entity_count for member in self.members):
            raise ValueError("entity acquisition cohort members must align to its axis")


@dataclass(frozen=True, slots=True)
class ValueRecordPlan:
    """Dataset projection for one symbolic program value."""

    id: str
    value_id: ValueId
    source_value_id: str
    dtype: MeasurementDType
    requires_execution: bool = False
    role: MeasurementVariableRole = "observable"
    unit: str | None = None
    recording_group_id: None = field(default=None, init=False)
    axes: tuple[RecordAxisPlan, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("value record plan id must be non-empty")
        if not self.source_value_id:
            raise ValueError("source value id must be non-empty")
        object.__setattr__(
            self,
            "metadata",
            freeze_json_mapping(
                self.metadata,
                path=f"value record plan {self.id!r} metadata",
            ),
        )


@dataclass(frozen=True, slots=True)
class ValueRecordCandidate:
    """One point-local data value available to dataset projection."""

    logical_point_id: LogicalPointId
    value_id: ValueId
    value: object


type DatasetRecordPlan = RecordPlan | EntityRecordPlan | ValueRecordPlan
type ProductRecordUse = RecordUse | EntityRecordUse
type BoundRecordUse = ProductRecordUse | ValueRecordUse


def plan_entity_acquisition_cohorts(
    records: Sequence[DatasetRecordPlan],
) -> tuple[EntityAcquisitionCohortPlan, ...]:
    """Factor non-independent entity acquisition semantics into execution plans."""

    planned: dict[str, EntityAcquisitionCohortPlan] = {}
    for record in records:
        if not isinstance(record, EntityRecordPlan):
            continue
        acquisition = record.acquisition
        if acquisition.policy == "independent":
            continue
        cohort_id = cast("str", acquisition.cohort_id)
        entity_axis = record.axes[0]
        entities = tuple(member.entity for member in record.members)
        member = EntityAcquisitionCohortMemberPlan(
            record_id=record.id,
            product_use_ids=tuple(item.product_use_id for item in record.members),
        )
        existing = planned.get(cohort_id)
        if existing is None:
            planned[cohort_id] = EntityAcquisitionCohortPlan(
                id=cohort_id,
                policy=acquisition.policy,
                dimension_id=entity_axis.id,
                entities=entities,
                members=(member,),
            )
            continue
        if (
            existing.policy != acquisition.policy
            or existing.dimension_id != entity_axis.id
            or existing.entities != entities
        ):
            raise ValueError(
                f"entity acquisition cohort {cohort_id!r} has conflicting plans"
            )
        planned[cohort_id] = replace(existing, members=(*existing.members, member))
    return tuple(planned.values())


def plan_records(
    products: Sequence[ProductDef],
    product_uses: Sequence[ProductUse],
    record_uses: Sequence[ProductRecordUse],
) -> list[RecordPlan | EntityRecordPlan]:
    """Project verified product record uses into dataset variable plans."""

    products_by_id = {product.id: product for product in products}
    uses_by_id = {use.id: use for use in product_uses}
    plans: list[RecordPlan | EntityRecordPlan] = []
    for record in record_uses:
        if isinstance(record, EntityRecordUse):
            plans.append(
                _plan_entity_record(
                    record,
                    products_by_id=products_by_id,
                    uses_by_id=uses_by_id,
                )
            )
            continue
        try:
            use = uses_by_id[record.product_use_id]
            product = products_by_id[use.product_id]
        except KeyError as error:
            msg = "record planning requires a closed verified product graph"
            raise ValueError(msg) from error
        plans.append(
            RecordPlan(
                id=record.id,
                product_use_id=use.id,
                product_id=product.id,
                role=record.role,
                recording_group_id=record.recording_group_id,
                unit=product.unit,
                dtype=product.dtype,
                axes=tuple(_plan_axis(axis) for axis in product.axes),
                metadata={**product.metadata, **record.metadata},
            )
        )
    return plans


def _plan_entity_record(
    record: EntityRecordUse,
    *,
    products_by_id: Mapping[ProductId, ProductDef],
    uses_by_id: Mapping[ProductUseId, ProductUse],
) -> EntityRecordPlan:
    resolved = tuple(
        (
            member,
            uses_by_id[member.product_use_id],
            products_by_id[uses_by_id[member.product_use_id].product_id],
        )
        for member in record.members
    )
    _first_member, _first_use, first_product = resolved[0]
    signatures = tuple(
        (
            product.dtype,
            product.unit,
            tuple(_grouped_axis_signature(axis) for axis in product.axes),
        )
        for _member, _use, product in resolved
    )
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise ValueError(
            f"entity record {record.id!r} products must have identical "
            "dtype, unit, and local axes"
        )
    entity_axis = RecordAxisPlan(
        id=record.axis.id,
        label=record.axis.id,
        kind="entity",
        size=len(record.axis.values),
        index=MeasurementEntityIndex(
            values=record.axis.values,
        ),
    )
    local_axes = tuple(
        _plan_grouped_axis(
            record.id,
            tuple(product.axes[axis_index] for _record, _use, product in resolved),
        )
        for axis_index in range(len(first_product.axes))
    )
    return EntityRecordPlan(
        id=record.id,
        members=tuple(
            EntityRecordMember(
                entity=member.entity,
                product_use_id=use.id,
                product_id=product.id,
                product_metadata=product.metadata,
            )
            for member, use, product in resolved
        ),
        dtype=first_product.dtype,
        role=record.role,
        recording_group_id=record.recording_group_id,
        acquisition=record.acquisition,
        unit=first_product.unit,
        axes=(entity_axis, *local_axes),
        metadata={**_common_product_metadata(resolved), **record.metadata},
    )


def _grouped_axis_signature(axis: ProductAxisDef) -> object:
    return (
        axis.id,
        axis.dimension_label,
        axis.kind,
        axis.size,
        axis.unit,
        axis.entities,
        axis.metadata,
    )


def _plan_grouped_axis(
    record_id: str,
    axes: Sequence[ProductAxisDef],
) -> RecordAxisPlan:
    first = axes[0]
    dimension_ids = {axis.dimension_id for axis in axes}
    planned = _plan_axis(first)
    return RecordAxisPlan(
        id=(
            first.dimension_id if len(dimension_ids) == 1 else f"{record_id}/{first.id}"
        ),
        label=planned.label,
        kind=planned.kind,
        size=planned.size,
        unit=planned.unit,
        index=planned.index,
        metadata=planned.metadata,
    )


def _common_product_metadata(
    resolved: Sequence[tuple[EntityRecordUseMember, ProductUse, ProductDef]],
) -> Mapping[str, JsonValue]:
    first = resolved[0][2].metadata
    return {
        key: value
        for key, value in first.items()
        if all(
            product.metadata.get(key) == value for _member, _use, product in resolved
        )
    }


def plan_value_records(
    record_uses: Sequence[ValueRecordUse],
) -> list[ValueRecordPlan]:
    """Project data value record uses into dataset variable plans."""

    plans: list[ValueRecordPlan] = []
    for record in record_uses:
        value_type = record.value_type
        if isinstance(value_type, Scalar):
            dtype, unit = measurement_value_spec_from_scalar(value_type)
            axes: tuple[RecordAxisPlan, ...] = ()
        else:
            dtype, unit = value_type.dtype, value_type.unit
            axes = tuple(
                RecordAxisPlan(
                    id=dimension.id,
                    label=None,
                    kind=dimension.kind or "sample",
                    size=dimension.size,
                    unit=dimension.unit,
                )
                for dimension in value_type.dimensions
            )
        plans.append(
            ValueRecordPlan(
                id=record.id,
                value_id=record.value_id,
                source_value_id=record.source_value_id,
                dtype=dtype,
                requires_execution=record.requires_execution,
                role=record.role,
                unit=unit,
                axes=axes,
                metadata=_value_record_metadata(record),
            )
        )
    return plans


def _plan_axis(axis: ProductAxisDef) -> RecordAxisPlan:
    return RecordAxisPlan(
        id=axis.dimension_id,
        label=axis.dimension_label,
        kind=axis.kind,
        size=axis.size,
        unit=axis.unit,
        index=(
            None
            if axis.entities is None
            else MeasurementEntityIndex(
                values=axis.entities,
            )
        ),
        metadata=axis.metadata,
    )


def validate_record_axes(
    records: Sequence[DatasetRecordPlan],
    *,
    phase: ProblemPhase = ProblemPhase.PLANNING,
) -> list[Problem]:
    """Validate compatibility that only becomes concrete after lowering."""

    problems: list[Problem] = []
    axes_by_id: dict[str, tuple[str, RecordAxisPlan]] = {}
    for record in records:
        seen_axis_ids: set[str] = set()
        for axis in record.axes:
            if axis.id in seen_axis_ids:
                continue
            seen_axis_ids.add(axis.id)
            existing = axes_by_id.get(axis.id)
            if existing is None:
                axes_by_id[axis.id] = (record.id, axis)
                continue
            existing_record_id, existing_axis = existing
            if _axes_are_compatible(existing_axis, axis):
                continue
            problems.append(
                problem(
                    "experiment_record_axis_conflict",
                    f"record {record.id!r} axis {axis.label or axis.id!r} conflicts "
                    f"with record {existing_record_id!r} on shared dimension "
                    f"{axis.id!r}; shared axes must have identical labels, kind, "
                    "size, unit, and metadata",
                    phase=phase,
                    location=model_location(
                        "records",
                        record.id,
                        "axes",
                        axis.label or axis.id,
                    ),
                    related_locations=(
                        model_location(
                            "records",
                            existing_record_id,
                            "axes",
                            existing_axis.label or existing_axis.id,
                        ),
                    ),
                )
            )
    return problems


def validate_record_plan(
    records: Sequence[DatasetRecordPlan],
    *,
    coordinate_ids: Sequence[str] = (),
    phase: ProblemPhase = ProblemPhase.PLANNING,
) -> list[Problem]:
    """Validate an independently supplied record plan at a projection boundary."""

    problems: list[Problem] = []
    record_ids = [record.id for record in records]
    duplicate_record_ids = _duplicates(record_ids)
    for record_id in sorted(duplicate_record_ids):
        problems.append(
            problem(
                "experiment_record_duplicate",
                f"experiment record {record_id!r} is duplicated",
                phase=phase,
                location=model_location("records"),
            )
        )
    coordinate_collisions = set(record_ids) & set(coordinate_ids)
    for record_id in sorted(coordinate_collisions):
        problems.append(
            problem(
                "experiment_record_coordinate_collision",
                f"record {record_id!r} conflicts with a point coordinate",
                phase=phase,
                location=model_location("records", record_id),
            )
        )
    dimension_ids = {
        "point",
        *(axis.id for record in records for axis in record.axes),
    }
    variable_ids = {*coordinate_ids, *record_ids}
    for variable_id in sorted(dimension_ids & variable_ids):
        problems.append(
            problem(
                "experiment_record_dimension_collision",
                f"measurement variable {variable_id!r} conflicts with a dataset "
                "dimension of the same id",
                phase=phase,
                location=model_location("records", variable_id),
            )
        )
    problems.extend(
        validate_record_axes(
            records,
            phase=phase,
        )
    )
    return problems


def expected_dataset_schema(
    *,
    experiment_id: str,
    point_count: int | None,
    records: Sequence[DatasetRecordPlan],
    dataset_id: str = "raw-measurements",
    point_coordinate_columns: Sequence[TableColumn] = (),
    point_domain_layout: PointDomainLayout = "product_grid",
    point_domain_axes: PointAxes[Quantity] = (),
    result_fields: Sequence[ExperimentResultField] = (),
) -> MeasurementDatasetSchema | None:
    """Build the complete planned dataset schema from points and record plans."""

    point_coordinate_ids = frozenset(column.id for column in point_coordinate_columns)
    if not records and not any(
        field.variable_id in point_coordinate_ids for field in result_fields
    ):
        return None
    dimensions = [
        MeasurementDimension(id="point", kind="point", size=point_count),
        *_record_axes(records),
    ]
    axis_values_by_id = {
        axis.id: _point_axis_unit_samples(axis) for axis in point_domain_axes
    }
    point_coordinates = _coordinate_variables(
        point_coordinate_columns,
        axis_values_by_id=axis_values_by_id,
    )
    record_variables = [_record_variable(record) for record in records]
    record_coordinates = [
        variable for variable in record_variables if variable.role == "coordinate"
    ]
    observables = [
        variable for variable in record_variables if variable.role == "observable"
    ]
    coordinates = [*point_coordinates, *record_coordinates]
    variables = [*coordinates, *observables]
    result = _measurement_result_contract(
        experiment_id,
        result_fields,
        variables=variables,
        dimensions=dimensions,
    )
    return MeasurementDatasetSchema(
        dataset_id=dataset_id,
        point_domain=(
            MeasurementProductGridPointDomain(
                axes=[
                    _measurement_point_domain_axis(axis) for axis in point_domain_axes
                ]
            )
            if point_domain_layout == "product_grid"
            else MeasurementPointCloudPointDomain(
                columns=[
                    MeasurementPointDomainColumn(id=column.id)
                    for column in point_coordinate_columns
                ]
            )
        ),
        dimensions=dimensions,
        variables=variables,
        variable_groups=_measurement_variable_groups(variables),
        primary_coordinates=[variable.id for variable in coordinates],
        primary_observables=[variable.id for variable in observables],
        result=result,
        metadata={"experiment_id": experiment_id},
    )


def _measurement_variable_groups(
    variables: Sequence[MeasurementVariable],
) -> tuple[MeasurementVariableGroup, ...]:
    group_ids: dict[str, None] = {}
    for variable in variables:
        if variable.recording_group_id is not None:
            group_ids.setdefault(variable.recording_group_id, None)
    return tuple(MeasurementVariableGroup(id=group_id) for group_id in group_ids)


def _measurement_point_domain_axis(
    axis: PointAxis[Quantity],
) -> MeasurementPointDomainAxis:
    source = axis.source
    if isinstance(source, PointAxisValues):
        selected_source = MeasurementPointDomainValuesSource(
            values=[measurement_axis_scalar(value) for value in source.values]
        )
    elif isinstance(source, PointAxisRange):
        start = measurement_axis_scalar(source.start)
        stop = measurement_axis_scalar(source.stop)
        assert start is not None and stop is not None
        selected_source = MeasurementPointDomainRangeSource(start=start, stop=stop)
    else:
        center = measurement_axis_scalar(source.center)
        span = measurement_axis_scalar(source.span)
        assert center is not None and span is not None
        selected_source = MeasurementPointDomainLinearSource(
            center=center,
            span=span,
        )
    return MeasurementPointDomainAxis(
        id=axis.id,
        size=point_axis_size(source),
        source=selected_source,
    )


def _point_axis_unit_samples(
    axis: PointAxis[Quantity],
) -> Sequence[CellValue]:
    source = axis.source
    if isinstance(source, PointAxisValues):
        return source.values
    if isinstance(source, PointAxisRange):
        return (source.start, source.stop)
    return (source.center, source.span)


def _measurement_result_contract(
    experiment_id: str,
    result_fields: Sequence[ExperimentResultField],
    *,
    variables: Sequence[MeasurementVariable],
    dimensions: Sequence[MeasurementDimension],
) -> MeasurementResultContract | None:
    if not result_fields:
        return None
    selected = tuple(
        MeasurementResultField(path=field.path, variable_id=field.variable_id)
        for field in result_fields
    )
    return MeasurementResultContract(
        id=experiment_id,
        version=measurement_result_contract_version(
            experiment_id,
            selected,
            variables=variables,
            dimensions=dimensions,
        ),
        fields=selected,
    )


def measurement_scalar(value: CellValue) -> MeasurementScalar:
    """Encode one typed scalar in the durable measurement representation."""

    if isinstance(value, Quantity):
        return MeasurementScalar.create(
            dtype="float64",
            unit=value.unit,
            value=value.value,
        )
    if isinstance(value, EntityRef):
        entity: dict[str, WireJsonValue] = {}
        if value.kind is not None:
            entity["kind"] = value.kind
        if value.metadata:
            entity["metadata"] = cast(
                "WireJsonValue",
                thaw_json_value(value.metadata),
            )
        return MeasurementScalar.create(
            dtype="string",
            value=value.id,
            metadata={"entity": entity},
        )
    if isinstance(value, bool):
        return MeasurementScalar.create(dtype="bool", value=value)
    if isinstance(value, int):
        return MeasurementScalar.create(dtype="int64", value=value)
    if isinstance(value, float):
        return MeasurementScalar.create(dtype="float64", value=value)
    if isinstance(value, complex):
        return MeasurementScalar.create(dtype="complex128", value=value)
    if isinstance(value, str):
        return MeasurementScalar.create(dtype="string", value=value)
    raise TypeError(f"unsupported persisted scalar: {type(value).__name__}")


def measurement_axis_scalar(value: CellValue) -> MeasurementScalar | None:
    """Encode a displayable point-axis value, leaving opaque values unlabeled."""

    if value is None or isinstance(value, PayloadValue | dict):
        return None
    return measurement_scalar(value)


def _record_axes(records: Sequence[DatasetRecordPlan]) -> list[MeasurementDimension]:
    dimensions: list[MeasurementDimension] = []
    seen: set[str] = set()
    for record in records:
        for axis in record.axes:
            if axis.id in seen:
                continue
            seen.add(axis.id)
            dimensions.append(
                MeasurementDimension(
                    id=axis.id,
                    kind=axis.kind,
                    label=axis.label,
                    size=axis.size,
                    index=axis.index,
                    metadata=_wire_metadata(axis.metadata),
                )
            )
    return dimensions


def _record_variable(record: DatasetRecordPlan) -> MeasurementVariable:
    if isinstance(record, ValueRecordPlan):
        return MeasurementVariable(
            id=record.id,
            role=record.role,
            dtype=record.dtype,
            unit=record.unit,
            dims=["point", *(axis.id for axis in record.axes)],
            source_value_id=record.source_value_id,
            metadata=_wire_metadata(record.metadata),
        )
    if isinstance(record, EntityRecordPlan):
        product_metadata = tuple(
            _wire_metadata(member.product_metadata) for member in record.members
        )
        common_metadata = {
            key: value
            for key, value in product_metadata[0].items()
            if all(metadata.get(key) == value for metadata in product_metadata[1:])
        }
        return MeasurementVariable(
            id=record.id,
            role=record.role,
            dtype=record.dtype,
            unit=record.unit,
            dims=["point", *(axis.id for axis in record.axes)],
            source_entity_products=MeasurementEntityProductSource(
                dimension_id=record.axes[0].id,
                product_ids=tuple(
                    member.product_id.qualified_name for member in record.members
                ),
                common_metadata=common_metadata,
                metadata_overrides=tuple(
                    MeasurementEntityProductMetadataOverride(
                        entity_index=entity_index,
                        metadata={
                            key: value
                            for key, value in metadata.items()
                            if key not in common_metadata
                        },
                    )
                    for entity_index, metadata in enumerate(product_metadata)
                    if any(key not in common_metadata for key in metadata)
                ),
            ),
            entity_acquisition=MeasurementEntityAcquisition(
                policy=record.acquisition.policy,
                cohort_id=record.acquisition.cohort_id,
            ),
            recording_group_id=record.recording_group_id,
            metadata=_wire_metadata(record.metadata),
        )
    return MeasurementVariable(
        id=record.id,
        role=record.role,
        dtype=record.dtype,
        unit=record.unit,
        dims=["point", *(axis.id for axis in record.axes)],
        source_product_id=record.product_id.qualified_name,
        recording_group_id=record.recording_group_id,
        metadata=_wire_metadata(record.metadata),
    )


def _value_record_metadata(record: ValueRecordUse) -> Mapping[str, JsonValue]:
    if isinstance(record.value_type, Array):
        return record.metadata
    atom = record.value_type.atom
    if not isinstance(atom, Entity) or atom.entity_kind is None:
        return record.metadata
    return {"entity_kind": atom.entity_kind, **record.metadata}


def _axes_are_compatible(left: RecordAxisPlan, right: RecordAxisPlan) -> bool:
    return (
        left.label == right.label
        and left.kind == right.kind
        and left.size == right.size
        and left.unit == right.unit
        and left.index == right.index
        and left.metadata == right.metadata
    )


def _coordinate_variables(
    columns: Sequence[TableColumn],
    *,
    axis_values_by_id: Mapping[str, Sequence[CellValue]],
) -> list[MeasurementVariable]:
    return [
        _coordinate_variable(
            column,
            axis_values=axis_values_by_id.get(column.id, ()),
        )
        for column in columns
    ]


def _coordinate_variable(
    column: TableColumn,
    *,
    axis_values: Sequence[CellValue],
) -> MeasurementVariable:
    atom = column.value_type.atom
    metadata: dict[str, WireJsonValue] = {}
    if isinstance(atom, Entity) and atom.entity_kind is not None:
        metadata["entity_kind"] = atom.entity_kind
    return MeasurementVariable(
        id=column.id,
        role="coordinate",
        dtype=measurement_value_spec_from_scalar(column.value_type)[0],
        unit=_coordinate_unit(column, axis_values=axis_values),
        dims=["point"],
        metadata=metadata,
    )


def _coordinate_unit(
    column: TableColumn,
    *,
    axis_values: Sequence[CellValue],
) -> str | None:
    declared = measurement_value_spec_from_scalar(column.value_type)[1]
    observed = {value.unit for value in axis_values if isinstance(value, Quantity)}
    if len(observed) > 1:
        raise ValueError(
            f"point-domain axis {column.id} uses inconsistent quantity units"
        )
    if not observed:
        return declared
    selected = next(iter(observed))
    if declared is not None and selected != declared:
        raise ValueError(
            f"point-domain axis {column.id} unit {selected} does not match "
            f"its declared unit {declared}"
        )
    return selected


def _wire_metadata(metadata: Mapping[str, JsonValue]) -> dict[str, WireJsonValue]:
    return cast("dict[str, WireJsonValue]", thaw_json_value(metadata))


def _duplicates(values: Sequence[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates
