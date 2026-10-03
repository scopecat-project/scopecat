"""Measurement record models shared by execution and analysis."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from typing import (
    Annotated,
    Literal,
    Self,
    SupportsComplex,
    SupportsFloat,
    SupportsIndex,
    cast,
    override,
)

import numpy as np
from numpy.typing import NDArray
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    ValidationInfo,
    WithJsonSchema,
    field_serializer,
    field_validator,
    model_validator,
)

from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.entity import EntityRef, entity_axis_fingerprint, entity_identity
from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.numpy_storage import freeze_ndarray
from scopecat.kernel.quantity import Quantity
from scopecat.program.measurement_types import (
    EntityAcquisitionPolicy,
    MeasurementArrayData,
    MeasurementArrayElement,
    MeasurementDType,
    MeasurementVariableRole,
)
from scopecat.program.point_domain import (
    point_axis_linear_value,
    point_axis_range_value,
)
from scopecat.records._schema_utils import (
    ensure_unique_ids,
    missing_references,
    validate_supported_unit,
)
from scopecat.records.measurement_array_schema import (
    MeasurementArrayPayload,
    MeasurementBooleanArrayPayload,
)
from scopecat.records.metadata import MeasurementMetadata

MEASUREMENT_RECORD_SCHEMA_VERSION = "scopecat.measurement_record.v10"
MEASUREMENT_DATASET_FORMAT_VERSION = "scopecat.measurement_dataset_schema.v17"

MeasurementUnavailableReason = Literal["missing", "invalid", "overload"]
_MEASUREMENT_ARRAY_CREATE_CONTEXT = object()
type _NonEmptyText = Annotated[str, Field(min_length=1)]


def _empty_metadata() -> Mapping[str, object]:
    return FrozenMapping()


class _FrozenMeasurementModel(BaseModel):
    """Validation-preserving copy semantics for immutable measurement models."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @override
    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        if not update:
            return self
        values: dict[str, object] = {
            name: getattr(self, name) for name in type(self).model_fields
        }
        values.update(update)
        return type(self).model_validate(values)

    @override
    def __copy__(self) -> Self:
        return self

    @override
    def __deepcopy__(self, memo: dict[int, object] | None = None) -> Self:
        if memo is not None:
            memo[id(self)] = self
        return self


class MeasurementEntityIndex(_FrozenMeasurementModel):
    """Ordered durable entity labels for one fixed measurement dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["entity"] = "entity"
    values: Sequence[EntityRef] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def freeze_values(cls, value: Sequence[EntityRef]) -> Sequence[EntityRef]:
        selected = tuple(value)
        identities = tuple(entity_identity(entity) for entity in selected)
        if len(identities) != len(set(identities)):
            raise ValueError("measurement entity index values must be unique")
        return selected

    @property
    def fingerprint(self) -> str:
        return entity_axis_fingerprint(self.values)

    @property
    def entity_kind(self) -> str | None:
        kinds = {entity.kind for entity in self.values}
        if len(kinds) != 1:
            return None
        return next(iter(kinds))


class MeasurementDimension(_FrozenMeasurementModel):
    """One logical extent; ``None`` denotes a point-local ragged extent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    label: str | None = None
    size: Annotated[int, Field(ge=0)] | None
    index: MeasurementEntityIndex | None = None
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @model_validator(mode="after")
    def validate_index(self) -> MeasurementDimension:
        if self.index is None:
            return self
        if self.kind != "entity":
            raise ValueError("measurement entity indexes require an entity dimension")
        if self.size != len(self.index.values):
            raise ValueError(
                "measurement entity index cardinality must match its dimension size"
            )
        return self


class MeasurementEntityProductMetadataOverride(_FrozenMeasurementModel):
    """Entity-local product metadata beyond one source's common metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_index: Annotated[int, Field(ge=0)]
    metadata: MeasurementMetadata


class MeasurementEntityProductSource(_FrozenMeasurementModel):
    """Ordered product provenance aligned to one entity dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension_id: _NonEmptyText
    product_ids: Sequence[_NonEmptyText | None] = Field(min_length=1)
    common_metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)
    metadata_overrides: Sequence[MeasurementEntityProductMetadataOverride] = Field(
        default_factory=tuple
    )

    @field_validator("product_ids", "metadata_overrides")
    @classmethod
    def freeze_product_ids[T](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_metadata_overrides(self) -> MeasurementEntityProductSource:
        indices = [override.entity_index for override in self.metadata_overrides]
        if len(indices) != len(set(indices)):
            raise ValueError(
                "measurement entity product metadata override indices must be unique"
            )
        if any(index >= len(self.product_ids) for index in indices):
            raise ValueError(
                "measurement entity product metadata override is out of range"
            )
        return self

    def metadata_for(self, entity_index: int) -> Mapping[str, object]:
        if self.product_ids[entity_index] is None:
            return FrozenMapping()
        override: Mapping[str, object] = next(
            (
                item.metadata
                for item in self.metadata_overrides
                if item.entity_index == entity_index
            ),
            FrozenMapping[str, object](),
        )
        return FrozenMapping((*self.common_metadata.items(), *override.items()))


class MeasurementEntityAcquisition(_FrozenMeasurementModel):
    """Declared execution semantics for one entity-indexed variable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: EntityAcquisitionPolicy = "independent"
    cohort_id: _NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_cohort(self) -> MeasurementEntityAcquisition:
        if self.policy == "independent":
            if self.cohort_id is not None:
                raise ValueError("independent entity acquisition has no cohort id")
        elif self.cohort_id is None:
            raise ValueError(f"{self.policy} entity acquisition requires a cohort id")
        return self


class MeasurementVariable(_FrozenMeasurementModel):
    """A point-local variable whose shape is derived from its dimensions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    role: MeasurementVariableRole
    dtype: MeasurementDType
    unit: str | None = None
    dims: Sequence[str] = Field(min_length=1)
    label: str | None = None
    source_product_id: _NonEmptyText | None = None
    source_entity_products: MeasurementEntityProductSource | None = None
    entity_acquisition: MeasurementEntityAcquisition | None = None
    source_value_id: _NonEmptyText | None = None
    recording_group_id: _NonEmptyText | None = None
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        return validate_supported_unit(value)

    @field_validator("dims")
    @classmethod
    def validate_dims(cls, value: Sequence[str]) -> Sequence[str]:
        ensure_unique_ids(
            value,
            "measurement variable dimensions must be unique",
        )
        if value[0] != "point":
            raise ValueError("measurement variables must use point as first dimension")
        return tuple(value)

    @model_validator(mode="after")
    def validate_unitless_dtype(self) -> MeasurementVariable:
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(f"{self.dtype} measurement variables cannot have a unit")
        return self


class MeasurementVariableGroup(_FrozenMeasurementModel):
    """One named recording-group definition referenced by variables."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: _NonEmptyText
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)


class MeasurementResultField(_FrozenMeasurementModel):
    """One experiment return path resolved to a durable dataset variable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Sequence[str] = Field(min_length=1)
    variable_id: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def freeze_path(cls, value: Sequence[str]) -> Sequence[str]:
        if any(not segment for segment in value):
            raise ValueError("measurement result path segments must be non-empty")
        return tuple(value)


class MeasurementResultContract(_FrozenMeasurementModel):
    """Self-describing experiment return contract persisted with a dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    version: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fields: Sequence[MeasurementResultField] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def freeze_fields[T](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_paths(self) -> MeasurementResultContract:
        ensure_unique_ids(
            ["/".join(field.path) for field in self.fields],
            "measurement result paths must be unique",
        )
        return self


class MeasurementPointDomainValuesSource(_FrozenMeasurementModel):
    """One explicit durable product-grid coordinate source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["values"] = "values"
    values: Sequence[MeasurementScalar | None]

    @field_validator("values")
    @classmethod
    def freeze_values(
        cls,
        value: Sequence[MeasurementScalar | None],
    ) -> Sequence[MeasurementScalar | None]:
        return tuple(value)


class MeasurementPointDomainRangeSource(_FrozenMeasurementModel):
    """One compact inclusive durable product-grid coordinate range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["range"] = "range"
    start: MeasurementScalar
    stop: MeasurementScalar

    @model_validator(mode="after")
    def validate_endpoints(self) -> MeasurementPointDomainRangeSource:
        if self.start.dtype not in {"int64", "float64"}:
            raise ValueError("measurement point-domain ranges must be real numeric")
        if (self.start.dtype, self.start.unit) != (self.stop.dtype, self.stop.unit):
            raise ValueError("measurement point-domain range endpoints must match")
        return self


class MeasurementPointDomainLinearSource(_FrozenMeasurementModel):
    """One compact centered durable product-grid coordinate range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["linear"] = "linear"
    center: MeasurementScalar
    span: MeasurementScalar

    @model_validator(mode="after")
    def validate_quantities(self) -> MeasurementPointDomainLinearSource:
        if self.center.dtype != "float64" or self.center.unit is None:
            raise ValueError("measurement centered axes require a quantity center")
        if (self.center.dtype, self.center.unit) != (
            self.span.dtype,
            self.span.unit,
        ):
            raise ValueError("measurement centered axis center and span must match")
        return self


type MeasurementPointDomainAxisSource = Annotated[
    MeasurementPointDomainValuesSource
    | MeasurementPointDomainRangeSource
    | MeasurementPointDomainLinearSource,
    Field(discriminator="kind"),
]


class MeasurementPointDomainAxis(_FrozenMeasurementModel):
    """One ordered independent axis in a product-grid point domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    size: Annotated[int, Field(ge=0)]
    source: MeasurementPointDomainAxisSource

    @model_validator(mode="after")
    def validate_source_size(self) -> MeasurementPointDomainAxis:
        if (
            isinstance(self.source, MeasurementPointDomainValuesSource)
            and len(self.source.values) != self.size
        ):
            raise ValueError("measurement point-domain axis values must match its size")
        if not isinstance(self.source, MeasurementPointDomainValuesSource) and (
            self.size < 2
        ):
            raise ValueError(
                "measurement generated point-domain axes require two points"
            )
        return self


class MeasurementProductGridPointDomain(_FrozenMeasurementModel):
    """A point domain formed from the ordered product of independent axes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["product_grid"] = "product_grid"
    axes: Sequence[MeasurementPointDomainAxis]

    @field_validator("axes")
    @classmethod
    def validate_axes(
        cls,
        value: Sequence[MeasurementPointDomainAxis],
    ) -> Sequence[MeasurementPointDomainAxis]:
        ensure_unique_ids(
            [axis.id for axis in value],
            "measurement point-domain axis ids must be unique",
        )
        return tuple(value)


class MeasurementPointDomainColumn(_FrozenMeasurementModel):
    """One ordered coordinate column in a point-cloud domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)


class MeasurementPointCloudPointDomain(_FrozenMeasurementModel):
    """A point domain whose coordinate columns form explicit ordered rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["point_cloud"] = "point_cloud"
    columns: Sequence[MeasurementPointDomainColumn]

    @field_validator("columns")
    @classmethod
    def validate_columns(
        cls,
        value: Sequence[MeasurementPointDomainColumn],
    ) -> Sequence[MeasurementPointDomainColumn]:
        ensure_unique_ids(
            [column.id for column in value],
            "measurement point-domain column ids must be unique",
        )
        return tuple(value)


type MeasurementPointDomain = Annotated[
    MeasurementProductGridPointDomain | MeasurementPointCloudPointDomain,
    Field(discriminator="kind"),
]


class MeasurementDatasetSchema(_FrozenMeasurementModel):
    """Complete planned shape and variable contract for one measurement dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["scopecat.measurement_dataset_schema.v17"] = (
        MEASUREMENT_DATASET_FORMAT_VERSION
    )
    dataset_id: str = Field(min_length=1)
    record_schema: Literal["scopecat.measurement_record.v10"] = (
        MEASUREMENT_RECORD_SCHEMA_VERSION
    )
    point_domain: MeasurementPointDomain
    dimensions: Sequence[MeasurementDimension]
    variables: Sequence[MeasurementVariable] = Field(default_factory=tuple)
    variable_groups: Sequence[MeasurementVariableGroup] = Field(default_factory=tuple)
    primary_coordinates: Sequence[str] = Field(default_factory=tuple)
    primary_observables: Sequence[str] = Field(default_factory=tuple)
    result: MeasurementResultContract | None = None
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @field_validator(
        "dimensions",
        "variables",
        "variable_groups",
        "primary_coordinates",
        "primary_observables",
    )
    @classmethod
    def freeze_sequences[T](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_references(self) -> MeasurementDatasetSchema:
        dimension_ids = [dimension.id for dimension in self.dimensions]
        ensure_unique_ids(
            dimension_ids,
            "measurement dataset schema dimension ids must be unique",
        )

        variable_ids = [variable.id for variable in self.variables]
        ensure_unique_ids(
            variable_ids,
            "measurement dataset schema variable ids must be unique",
        )

        dimension_id_set = set(dimension_ids)
        dimension_by_id = {dimension.id: dimension for dimension in self.dimensions}
        variable_by_id = {variable.id: variable for variable in self.variables}
        _validate_measurement_variable_groups(self.variables, self.variable_groups)
        namespace_collisions = dimension_id_set & set(variable_by_id)
        if namespace_collisions:
            raise ValueError(
                "measurement dimensions and variables must have distinct ids: "
                + ", ".join(sorted(namespace_collisions))
            )
        point_dimensions = [
            dimension for dimension in self.dimensions if dimension.kind == "point"
        ]
        if len(point_dimensions) != 1 or point_dimensions[0].id != "point":
            raise ValueError(
                "measurement dataset schema must define exactly one point "
                "dimension with id point"
            )
        if isinstance(self.point_domain, MeasurementProductGridPointDomain):
            if point_dimensions[0].size is None:
                raise ValueError(
                    "measurement product-grid point dimension must have a fixed size"
                )
            grid_size = math.prod(axis.size for axis in self.point_domain.axes)
            if grid_size != point_dimensions[0].size:
                raise ValueError(
                    "measurement product-grid cardinality must match the point "
                    "dimension size"
                )

        for variable in self.variables:
            missing_dims = missing_references(variable.dims, dimension_id_set)
            if missing_dims:
                msg = (
                    f"measurement variable {variable.id} references unknown "
                    f"dimensions: {', '.join(missing_dims)}"
                )
                raise ValueError(msg)
            _validate_measurement_variable_source(variable, dimension_by_id)
        ensure_unique_ids(
            self.primary_coordinates,
            "measurement dataset primary coordinate ids must be unique",
        )
        ensure_unique_ids(
            self.primary_observables,
            "measurement dataset primary observable ids must be unique",
        )

        for variable_id in self.primary_coordinates:
            variable = variable_by_id.get(variable_id)
            if variable is None:
                msg = f"primary coordinate {variable_id} is not a variable"
                raise ValueError(msg)
            if variable.role != "coordinate":
                msg = f"primary coordinate {variable_id} must have coordinate role"
                raise ValueError(msg)

        for variable_id in self.primary_observables:
            variable = variable_by_id.get(variable_id)
            if variable is None:
                msg = f"primary observable {variable_id} is not a variable"
                raise ValueError(msg)
            if variable.role != "observable":
                msg = f"primary observable {variable_id} must have observable role"
                raise ValueError(msg)

        if self.result is not None:
            missing_result_variables = sorted(
                {
                    field.variable_id
                    for field in self.result.fields
                    if field.variable_id not in variable_by_id
                }
            )
            if missing_result_variables:
                raise ValueError(
                    "measurement result fields reference missing variables: "
                    + ", ".join(missing_result_variables)
                )

        return self


def measurement_result_contract_version(
    contract_id: str,
    fields: Sequence[MeasurementResultField],
    *,
    variables: Sequence[MeasurementVariable],
    dimensions: Sequence[MeasurementDimension],
) -> str:
    """Identify a result contract including its local dimension definitions."""

    variable_by_id = {variable.id: variable for variable in variables}
    dimension_by_id = {dimension.id: dimension for dimension in dimensions}
    identity = {
        "id": contract_id,
        "fields": [
            {
                "path": list(field.path),
                "variable": _measurement_result_variable_identity(
                    variable_by_id[field.variable_id]
                ),
                "dimensions": [
                    _measurement_result_dimension_identity(
                        dimension_by_id[dimension_id]
                    )
                    for dimension_id in variable_by_id[field.variable_id].dims[1:]
                ],
            }
            for field in fields
        ],
    }
    return f"sha256:{stable_content_hash(identity)}"


def _measurement_result_variable_identity(
    variable: MeasurementVariable,
) -> Mapping[str, object]:
    return {
        "id": variable.id,
        "role": variable.role,
        "dtype": variable.dtype,
        "unit": variable.unit,
        "dims": list(variable.dims),
    }


def _measurement_result_dimension_identity(
    dimension: MeasurementDimension,
) -> Mapping[str, object]:
    return {
        "id": dimension.id,
        "kind": dimension.kind,
        "size": dimension.size,
        "entity_axis_fingerprint": (
            None if dimension.index is None else dimension.index.fingerprint
        ),
    }


def _validate_measurement_variable_groups(
    variables: Sequence[MeasurementVariable],
    groups: Sequence[MeasurementVariableGroup],
) -> None:
    ensure_unique_ids(
        [group.id for group in groups],
        "measurement dataset variable group ids must be unique",
    )
    group_ids = {group.id for group in groups}
    referenced_group_ids = {
        variable.recording_group_id
        for variable in variables
        if variable.recording_group_id is not None
    }
    missing_groups = referenced_group_ids - group_ids
    if missing_groups:
        raise ValueError(
            "measurement variables reference unknown groups: "
            + ", ".join(sorted(missing_groups))
        )
    unused_groups = group_ids - referenced_group_ids
    if unused_groups:
        raise ValueError(
            "measurement variable groups must be referenced: "
            + ", ".join(sorted(unused_groups))
        )


def _validate_measurement_variable_source(
    variable: MeasurementVariable,
    dimension_by_id: Mapping[str, MeasurementDimension],
) -> None:
    sources = (
        variable.source_product_id,
        variable.source_entity_products,
        variable.source_value_id,
    )
    if sum(source is not None for source in sources) > 1:
        raise ValueError(
            f"measurement variable {variable.id} declares multiple sources"
        )
    entity_source = variable.source_entity_products
    if entity_source is None:
        if variable.entity_acquisition is not None:
            raise ValueError(
                f"measurement variable {variable.id} entity acquisition requires "
                "entity product sources"
            )
        return
    if variable.entity_acquisition is None:
        raise ValueError(
            f"measurement variable {variable.id} entity sources require "
            "acquisition semantics"
        )
    dimension = dimension_by_id.get(entity_source.dimension_id)
    if (
        dimension is None
        or dimension.index is None
        or entity_source.dimension_id not in variable.dims
    ):
        raise ValueError(
            f"measurement variable {variable.id} entity source must reference "
            "one indexed entity dimension"
        )
    entity_count = len(dimension.index.values)
    if len(entity_source.product_ids) != entity_count:
        raise ValueError(
            f"measurement variable {variable.id} entity source cardinality "
            "must match its entity dimension"
        )


MeasurementScalarData = Annotated[
    bool | int | float | complex | str,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "boolean"},
                {"type": "integer"},
                {"type": "number"},
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "real": {"type": "number"},
                        "imag": {"type": "number"},
                    },
                    "required": ["real", "imag"],
                    "additionalProperties": False,
                },
            ]
        }
    ),
]


class MeasurementScalar(_FrozenMeasurementModel):
    """One normalized, typed scalar measurement value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["scalar"]
    dtype: MeasurementDType = "float64"
    unit: str | None = None
    value: MeasurementScalarData
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @classmethod
    def create(
        cls,
        *,
        value: object,
        dtype: MeasurementDType = "float64",
        unit: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        """Construct a scalar while keeping the wire discriminator required."""

        return cls.model_validate(
            {
                "kind": "scalar",
                "dtype": dtype,
                "unit": unit,
                "value": value,
                "metadata": {} if metadata is None else metadata,
            }
        )

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        return validate_supported_unit(value)

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> MeasurementScalarData:
        raw_dtype = cast("object", info.data.get("dtype", "float64"))
        dtype = cast(
            "MeasurementDType",
            raw_dtype
            if raw_dtype in {"float64", "int64", "complex128", "bool", "string"}
            else "float64",
        )
        return _measurement_scalar_data(value, dtype=dtype)

    @field_serializer("value")
    def serialize_value(self, value: MeasurementScalarData) -> object:
        if self.dtype == "complex128":
            if not isinstance(value, complex):
                raise TypeError("complex scalar serialization requires a complex value")
            return {"real": value.real, "imag": value.imag}
        return value

    @model_validator(mode="after")
    def validate_unitless_dtype(self) -> MeasurementScalar:
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(f"{self.dtype} measurement scalars cannot have a unit")
        return self


def measurement_point_axis_values(
    axis: MeasurementPointDomainAxis,
) -> tuple[MeasurementScalar | None, ...]:
    """Materialize one durable axis only when an analysis view needs its values."""

    return tuple(iter_measurement_point_axis_values(axis))


def iter_measurement_point_axis_values(
    axis: MeasurementPointDomainAxis,
) -> Iterator[MeasurementScalar | None]:
    """Read compact generated coordinates without allocating the entire axis."""

    source = axis.source
    if isinstance(source, MeasurementPointDomainValuesSource):
        yield from source.values
        return
    if isinstance(source, MeasurementPointDomainRangeSource):
        start = _measurement_scalar_range_value(source.start)
        stop = _measurement_scalar_range_value(source.stop)
        yield from (
            _measurement_scalar_from_generated(
                source.start,
                point_axis_range_value(start, stop, axis.size, index),
            )
            for index in range(axis.size)
        )
        return
    center = Quantity(cast("float", source.center.value), source.center.unit)
    span = Quantity(cast("float", source.span.value), source.span.unit)
    yield from (
        _measurement_scalar_from_generated(
            source.center,
            point_axis_linear_value(center, span, axis.size, index),
        )
        for index in range(axis.size)
    )


def _measurement_scalar_range_value(
    value: MeasurementScalar,
) -> int | float | Quantity:
    selected = value.value
    if value.unit is not None:
        return Quantity(cast("float", selected), value.unit)
    if value.dtype == "int64":
        return cast("int", selected)
    return cast("float", selected)


def _measurement_scalar_from_generated(
    template: MeasurementScalar,
    value: float | Quantity,
) -> MeasurementScalar:
    native = value.value if isinstance(value, Quantity) else value
    return template.model_copy(update={"value": native})


class MeasurementArrayUnavailableGroup(_FrozenMeasurementModel):
    """One reason shared by a sparse set of unavailable array leaves."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: MeasurementUnavailableReason
    flat_indices: Sequence[Annotated[int, Field(ge=0)]] = Field(min_length=1)
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @field_validator("flat_indices")
    @classmethod
    def freeze_flat_indices(cls, value: Sequence[int]) -> Sequence[int]:
        selected = tuple(value)
        if len(selected) != len(set(selected)):
            raise ValueError("unavailable array indices must be unique")
        return selected


class MeasurementArrayAvailability(_FrozenMeasurementModel):
    """Array validity with sparse, reason-qualified unavailable leaves."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

    valid: MeasurementBooleanArrayPayload
    unavailable: Sequence[MeasurementArrayUnavailableGroup] = Field(min_length=1)

    @classmethod
    def create(
        cls,
        *,
        valid: object,
        reason: MeasurementUnavailableReason = "missing",
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        """Create one incomplete validity mask with one shared failure reason."""

        selected = _measurement_validity_array(valid)
        if bool(np.all(selected)):
            raise ValueError("fully available arrays must omit availability")
        invalid = tuple(int(index) for index in np.flatnonzero(~selected.reshape(-1)))
        return cls(
            valid=selected,
            unavailable=(
                MeasurementArrayUnavailableGroup(
                    reason=reason,
                    flat_indices=invalid,
                    metadata={} if metadata is None else metadata,
                ),
            ),
        )

    @field_validator("valid", mode="before")
    @classmethod
    def normalize_valid(cls, value: object) -> NDArray[np.bool_]:
        return _measurement_validity_array(value)

    @field_serializer("valid")
    def serialize_valid(self, value: NDArray[np.bool_]) -> object:
        return cast("object", value.tolist())

    @field_validator("unavailable")
    @classmethod
    def freeze_unavailable[T: MeasurementArrayUnavailableGroup](
        cls, value: Sequence[T]
    ) -> Sequence[T]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_incomplete(self) -> MeasurementArrayAvailability:
        flattened = self.valid.reshape(-1)
        if bool(np.all(flattened)):
            raise ValueError("fully available arrays must omit availability")
        actual = tuple(
            sorted(index for group in self.unavailable for index in group.flat_indices)
        )
        expected = tuple(int(index) for index in np.flatnonzero(~flattened))
        if len(actual) != len(set(actual)) or actual != expected:
            raise ValueError(
                "array unavailable groups must exactly partition invalid leaves"
            )
        return self

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MeasurementArrayAvailability):
            return NotImplemented
        return np.array_equal(self.valid, other.valid) and (
            self.unavailable == other.unavailable
        )


class MeasurementArray(_FrozenMeasurementModel):
    """One rectangular typed array backed by an immutable NumPy buffer."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

    kind: Literal["array"]
    dtype: MeasurementDType = "float64"
    unit: str | None = None
    shape: tuple[Annotated[int, Field(ge=0)], ...] = Field(min_length=1)
    values: MeasurementArrayPayload
    availability: MeasurementArrayAvailability | None = None
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @classmethod
    def create(
        cls,
        *,
        values: object,
        dtype: MeasurementDType = "float64",
        unit: str | None = None,
        availability: MeasurementArrayAvailability | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        """Construct an immutable array and infer its wire shape from ``values``."""

        data: dict[str, object] = {
            "kind": "array",
            "dtype": dtype,
            "unit": unit,
            "values": values,
            "availability": availability,
            "metadata": {} if metadata is None else metadata,
        }
        return cls.model_validate(
            data,
            context=_MEASUREMENT_ARRAY_CREATE_CONTEXT,
        )

    @model_validator(mode="before")
    @classmethod
    def prepare_create_values(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if info.context is not _MEASUREMENT_ARRAY_CREATE_CONTEXT:
            return value
        data = dict(cast("Mapping[str, object]", value))
        raw_dtype = data.get("dtype", "float64")
        dtype = cast(
            "MeasurementDType",
            raw_dtype
            if raw_dtype in {"float64", "int64", "complex128", "bool", "string"}
            else "float64",
        )
        selected = _measurement_ndarray(data["values"], dtype=dtype)
        data["shape"] = selected.shape
        data["values"] = selected
        return data

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        return validate_supported_unit(value)

    @field_validator("shape")
    @classmethod
    def freeze_shape(cls, value: Sequence[int]) -> tuple[int, ...]:
        return tuple(value)

    @field_validator("values", mode="before")
    @classmethod
    def normalize_values(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> MeasurementArrayData:
        """Own one contiguous, typed, read-only array at the model boundary."""

        raw_dtype = cast("object", info.data.get("dtype", "float64"))
        dtype = cast(
            "MeasurementDType",
            raw_dtype
            if raw_dtype in {"float64", "int64", "complex128", "bool", "string"}
            else "float64",
        )
        selected = (
            cast("MeasurementArrayData", value)
            if info.context is _MEASUREMENT_ARRAY_CREATE_CONTEXT
            else _measurement_ndarray(value, dtype=dtype)
        )
        expected_shape = cast("object", info.data.get("shape"))
        expected_extents = (
            cast("tuple[object, ...]", expected_shape)
            if isinstance(expected_shape, tuple)
            else ()
        )
        if (
            selected.size == 0
            and isinstance(expected_shape, tuple)
            and all(isinstance(extent, int) for extent in expected_extents)
            and math.prod(cast("tuple[int, ...]", expected_shape)) == 0
        ):
            selected = selected.reshape(cast("tuple[int, ...]", expected_shape))
        return selected

    @field_serializer("values")
    def serialize_values(self, value: MeasurementArrayData) -> object:
        if self.dtype == "complex128":
            return _complex_array_json(cast("object", value.tolist()))
        return cast("object", value.tolist())

    @model_validator(mode="after")
    def validate_values_shape(self) -> MeasurementArray:
        actual_shape = self.values.shape
        if actual_shape != self.shape:
            msg = f"measurement array shape {actual_shape} does not match {self.shape}"
            raise ValueError(msg)
        if (
            self.availability is not None
            and self.availability.valid.shape != self.shape
        ):
            raise ValueError(
                "measurement array availability shape does not match its values"
            )
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(f"{self.dtype} measurement arrays cannot have a unit")
        return self

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MeasurementArray):
            return NotImplemented
        return (
            self.kind == other.kind
            and self.dtype == other.dtype
            and self.unit == other.unit
            and self.shape == other.shape
            and self.metadata == other.metadata
            and self.availability == other.availability
            and np.array_equal(self.values, other.values)
        )


class MeasurementPartitionedArray(_FrozenMeasurementModel):
    """One logical rectangular array retained as contiguous axis partitions.

    Partitions are a physical transport and storage choice. ``shape``, dtype,
    unit, metadata, and availability remain the logical measurement contract;
    consumers that need a single NumPy buffer may call ``materialize()``.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

    kind: Literal["partitioned_array"]
    dtype: MeasurementDType = "float64"
    unit: str | None = None
    axis: Annotated[int, Field(ge=0)]
    partitions: Sequence[MeasurementArray] = Field(min_length=1)
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @classmethod
    def create(
        cls,
        *,
        partitions: Sequence[MeasurementArray],
        axis: int,
        dtype: MeasurementDType = "float64",
        unit: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        """Construct a logical array without joining its physical buffers."""

        return cls(
            kind="partitioned_array",
            dtype=dtype,
            unit=unit,
            axis=axis,
            partitions=partitions,
            metadata={} if metadata is None else metadata,
        )

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        return validate_supported_unit(value)

    @field_validator("partitions")
    @classmethod
    def freeze_partitions[T: MeasurementArray](
        cls,
        value: Sequence[T],
    ) -> Sequence[T]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_partitions(self) -> MeasurementPartitionedArray:
        first = self.partitions[0]
        rank = len(first.shape)
        if rank == 0 or self.axis >= rank:
            raise ValueError(
                "measurement array partition axis must address a non-scalar rank"
            )
        if any(partition.shape[self.axis] == 0 for partition in self.partitions):
            raise ValueError("measurement array partitions must be non-empty")
        expected_shape = first.shape[: self.axis] + first.shape[self.axis + 1 :]
        if any(
            partition.shape[: self.axis] + partition.shape[self.axis + 1 :]
            != expected_shape
            for partition in self.partitions
        ):
            raise ValueError(
                "measurement array partitions must match outside their split axis"
            )
        if any(
            (partition.dtype, partition.unit) != (self.dtype, self.unit)
            for partition in self.partitions
        ):
            raise ValueError(
                "measurement array partitions must match its dtype and unit"
            )
        if any(partition.metadata for partition in self.partitions):
            raise ValueError(
                "measurement array partition metadata belongs on the logical value"
            )
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(
                f"{self.dtype} measurement partitioned arrays cannot have a unit"
            )
        return self

    @property
    def shape(self) -> tuple[int, ...]:
        first = self.partitions[0].shape
        return (
            *first[: self.axis],
            sum(partition.shape[self.axis] for partition in self.partitions),
            *first[self.axis + 1 :],
        )

    @property
    def values(self) -> MeasurementArrayData:
        """Materialize one immutable NumPy buffer at an explicit consumer boundary."""

        return self.materialize().values

    @property
    def availability(self) -> MeasurementArrayAvailability | None:
        """Materialize logical availability while preserving diagnostics."""

        if all(partition.availability is None for partition in self.partitions):
            return None
        full_shape = self.shape
        valid = np.concatenate(
            [
                (
                    np.ones(partition.shape, dtype=np.bool_)
                    if partition.availability is None
                    else partition.availability.valid
                )
                for partition in self.partitions
            ],
            axis=self.axis,
        )
        unavailable: list[MeasurementArrayUnavailableGroup] = []
        axis_offset = 0
        for partition in self.partitions:
            availability = partition.availability
            if availability is not None:
                for group in availability.unavailable:
                    coordinates = list(
                        np.unravel_index(group.flat_indices, partition.shape)
                    )
                    coordinates[self.axis] = coordinates[self.axis] + axis_offset
                    global_indices: NDArray[np.intp] = np.asarray(
                        np.ravel_multi_index(
                            tuple(coordinates),
                            full_shape,
                        ),
                        dtype=np.intp,
                    )
                    unavailable.append(
                        MeasurementArrayUnavailableGroup(
                            reason=group.reason,
                            flat_indices=tuple(
                                cast("list[int]", global_indices.tolist())
                            ),
                            metadata=group.metadata,
                        )
                    )
            axis_offset += partition.shape[self.axis]
        return MeasurementArrayAvailability(
            valid=valid,
            unavailable=unavailable,
        )

    def materialize(self) -> MeasurementArray:
        """Join partitions into the ordinary dense measurement representation."""

        return MeasurementArray.create(
            values=np.concatenate(
                [partition.values for partition in self.partitions],
                axis=self.axis,
            ),
            dtype=self.dtype,
            unit=self.unit,
            availability=self.availability,
            metadata=self.metadata,
        )

    @property
    def value_nbytes(self) -> int:
        return sum(partition.values.nbytes for partition in self.partitions)

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MeasurementPartitionedArray):
            return NotImplemented
        return (
            self.kind == other.kind
            and self.dtype == other.dtype
            and self.unit == other.unit
            and self.axis == other.axis
            and self.metadata == other.metadata
            and self.partitions == other.partitions
        )


class MeasurementUnavailable(_FrozenMeasurementModel):
    """A complete scalar or array result with no usable value.

    ``None`` preserves an unknown extent for a ragged product axis when no
    available value exists from which to learn that point-local size.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unavailable"]
    reason: MeasurementUnavailableReason
    dtype: MeasurementDType
    unit: str | None
    shape: tuple[Annotated[int, Field(ge=0)] | None, ...]
    metadata: MeasurementMetadata

    @classmethod
    def create(
        cls,
        *,
        reason: MeasurementUnavailableReason,
        dtype: MeasurementDType,
        unit: str | None,
        shape: Sequence[int | None],
        metadata: Mapping[str, object],
    ) -> Self:
        """Construct an unavailable result with its complete value contract."""

        return cls(
            kind="unavailable",
            reason=reason,
            dtype=dtype,
            unit=unit,
            shape=tuple(shape),
            metadata=metadata,
        )

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        return validate_supported_unit(value)

    @model_validator(mode="after")
    def validate_unitless_dtype(self) -> MeasurementUnavailable:
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(
                f"{self.dtype} unavailable measurements cannot have a unit"
            )
        return self


type MeasurementSegment = Annotated[
    MeasurementArray | MeasurementUnavailable,
    Field(discriminator="kind"),
]


class MeasurementSegmentedArray(_FrozenMeasurementModel):
    """One entity-indexed value with independently shaped local array segments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["segmented_array"]
    dtype: MeasurementDType = "float64"
    unit: str | None = None
    segments: Sequence[MeasurementSegment] = Field(min_length=1)
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @classmethod
    def create(
        cls,
        *,
        segments: Sequence[MeasurementArray | MeasurementUnavailable],
        dtype: MeasurementDType = "float64",
        unit: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        return cls(
            kind="segmented_array",
            dtype=dtype,
            unit=unit,
            segments=segments,
            metadata={} if metadata is None else metadata,
        )

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        return validate_supported_unit(value)

    @field_validator("segments")
    @classmethod
    def freeze_segments[T: MeasurementSegment](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)

    @model_validator(mode="after")
    def validate_segments(self) -> MeasurementSegmentedArray:
        ranks = {len(segment.shape) for segment in self.segments}
        if ranks == {0} or len(ranks) != 1:
            raise ValueError(
                "measurement segmented arrays require one non-scalar local rank"
            )
        if any(
            (segment.dtype, segment.unit) != (self.dtype, self.unit)
            for segment in self.segments
        ):
            raise ValueError(
                "measurement segmented array members must match its dtype and unit"
            )
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(
                f"{self.dtype} measurement segmented arrays cannot have a unit"
            )
        return self

    @property
    def segment_shapes(self) -> tuple[tuple[int | None, ...], ...]:
        return tuple(tuple(segment.shape) for segment in self.segments)

    @property
    def shape(self) -> tuple[int | None, ...]:
        shapes = self.segment_shapes
        rank = len(shapes[0])
        extents: list[int | None] = []
        for axis in range(rank):
            axis_extents = {shape[axis] for shape in shapes}
            extent = next(iter(axis_extents)) if len(axis_extents) == 1 else None
            extents.append(extent)
        return (len(shapes), *extents)

    @property
    def values(self) -> MeasurementArrayData:
        chunks: list[MeasurementArrayData] = []
        for segment in self.segments:
            if isinstance(segment, MeasurementArray):
                chunks.append(segment.values.reshape(-1))
                continue
            size = (
                0
                if any(extent is None for extent in segment.shape)
                else math.prod(cast("tuple[int, ...]", segment.shape))
            )
            chunks.append(
                cast(
                    "MeasurementArrayData",
                    np.zeros(size, dtype=_numpy_dtype(self.dtype)),
                )
            )
        combined = np.concatenate(chunks)
        return cast("MeasurementArrayData", freeze_ndarray(combined))

    @property
    def availability(self) -> MeasurementArrayAvailability | None:
        valid_chunks: list[NDArray[np.bool_]] = []
        unavailable: list[MeasurementArrayUnavailableGroup] = []
        offset = 0
        for segment in self.segments:
            if isinstance(segment, MeasurementArray):
                size = segment.values.size
                if segment.availability is None:
                    valid_chunks.append(np.ones(size, dtype=np.bool_))
                else:
                    valid_chunks.append(segment.availability.valid.reshape(-1))
                    unavailable.extend(
                        MeasurementArrayUnavailableGroup(
                            reason=group.reason,
                            flat_indices=tuple(
                                offset + index for index in group.flat_indices
                            ),
                            metadata=group.metadata,
                        )
                        for group in segment.availability.unavailable
                    )
            else:
                size = (
                    0
                    if any(extent is None for extent in segment.shape)
                    else math.prod(cast("tuple[int, ...]", segment.shape))
                )
                valid_chunks.append(np.zeros(size, dtype=np.bool_))
                if size:
                    unavailable.append(
                        MeasurementArrayUnavailableGroup(
                            reason=segment.reason,
                            flat_indices=tuple(range(offset, offset + size)),
                            metadata=segment.metadata,
                        )
                    )
            offset += size
        valid = np.concatenate(valid_chunks)
        if bool(np.all(valid)):
            return None
        return MeasurementArrayAvailability(valid=valid, unavailable=unavailable)

    @property
    def has_unavailable_segments(self) -> bool:
        return any(
            isinstance(segment, MeasurementUnavailable) for segment in self.segments
        )


type MeasurementAcquisitionValue = Annotated[
    MeasurementScalar
    | MeasurementArray
    | MeasurementPartitionedArray
    | MeasurementUnavailable,
    Field(discriminator="kind"),
]


type MeasurementValue = Annotated[
    MeasurementScalar
    | MeasurementArray
    | MeasurementPartitionedArray
    | MeasurementSegmentedArray
    | MeasurementUnavailable,
    Field(discriminator="kind"),
]


class InstrumentAcquisitionEvidence(_FrozenMeasurementModel):
    """Daemon-observed interval and physical target for one collected result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: _NonEmptyText
    instrument_id: _NonEmptyText
    interface_id: InterfaceId
    component_path: tuple[_NonEmptyText, ...] = ()
    acquisition_id: _NonEmptyText
    result_id: _NonEmptyText
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> InstrumentAcquisitionEvidence:
        if self.completed_at < self.started_at:
            raise ValueError("acquisition completion must not precede its start")
        return self


class EntityAcquisitionEvidence(_FrozenMeasurementModel):
    """Acquisition evidence aligned to one entity-indexed variable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["entity"] = "entity"
    dimension_id: _NonEmptyText
    acquisition: MeasurementEntityAcquisition = Field(
        default_factory=MeasurementEntityAcquisition
    )
    values: Sequence[InstrumentAcquisitionEvidence | None] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def freeze_values[T](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)


type MeasurementAcquisitionEvidence = (
    InstrumentAcquisitionEvidence | EntityAcquisitionEvidence
)


class InstrumentAcquisitionEvent(_FrozenMeasurementModel):
    """Shared physical acquisition interval referenced by result evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: _NonEmptyText
    instrument_id: _NonEmptyText
    interface_id: InterfaceId
    component_path: tuple[_NonEmptyText, ...] = ()
    acquisition_id: _NonEmptyText
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @classmethod
    def from_result(cls, evidence: InstrumentAcquisitionEvidence) -> Self:
        return cls(
            command_id=evidence.command_id,
            instrument_id=evidence.instrument_id,
            interface_id=evidence.interface_id,
            component_path=evidence.component_path,
            acquisition_id=evidence.acquisition_id,
            started_at=evidence.started_at,
            completed_at=evidence.completed_at,
        )

    def result(self, result_id: str) -> InstrumentAcquisitionEvidence:
        return InstrumentAcquisitionEvidence(
            command_id=self.command_id,
            instrument_id=self.instrument_id,
            interface_id=self.interface_id,
            component_path=self.component_path,
            acquisition_id=self.acquisition_id,
            result_id=result_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )


class InstrumentAcquisitionEvidenceRef(_FrozenMeasurementModel):
    """One result identifier within a shared acquisition event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["instrument"] = "instrument"
    event_index: Annotated[int, Field(ge=0)]
    result_id: _NonEmptyText


class EntityAcquisitionEvidenceRef(_FrozenMeasurementModel):
    """Entity-aligned result references within shared acquisition events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["entity"] = "entity"
    dimension_id: _NonEmptyText
    acquisition: MeasurementEntityAcquisition = Field(
        default_factory=MeasurementEntityAcquisition
    )
    values: Sequence[InstrumentAcquisitionEvidenceRef | None] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def freeze_values[T](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)


type MeasurementAcquisitionEvidenceRef = Annotated[
    InstrumentAcquisitionEvidenceRef | EntityAcquisitionEvidenceRef,
    Field(discriminator="kind"),
]


def _empty_evidence_events() -> Sequence[InstrumentAcquisitionEvent]:
    return ()


def _empty_evidence_entries() -> Sequence[MeasurementAcquisitionEvidenceRef]:
    return ()


def _empty_evidence_refs() -> Mapping[str, int]:
    return FrozenMapping()


type MeasurementAcquisitionEvents = Sequence[InstrumentAcquisitionEvent]
type MeasurementAcquisitionEvidenceEntries = Sequence[MeasurementAcquisitionEvidenceRef]


def _freeze_measurement_values(
    value: Mapping[str, MeasurementValue],
) -> Mapping[str, MeasurementValue]:
    return FrozenMapping(value.items())


def _serialize_measurement_values(
    value: Mapping[str, MeasurementValue],
) -> dict[str, MeasurementValue]:
    return dict(value)


type MeasurementValueMap = Annotated[
    Mapping[str, MeasurementValue],
    AfterValidator(_freeze_measurement_values),
    PlainSerializer(
        _serialize_measurement_values,
        return_type=dict[str, MeasurementValue],
    ),
]


def _freeze_evidence_refs(value: Mapping[str, int]) -> Mapping[str, int]:
    return FrozenMapping(value.items())


def _serialize_evidence_refs(value: Mapping[str, int]) -> dict[str, int]:
    return dict(value)


type MeasurementAcquisitionEvidenceRefs = Annotated[
    Mapping[_NonEmptyText, Annotated[int, Field(ge=0)]],
    AfterValidator(_freeze_evidence_refs),
    PlainSerializer(_serialize_evidence_refs, return_type=dict[str, int]),
]


class MeasurementAcquisitionEvidenceCatalog(_FrozenMeasurementModel):
    """Result evidence factored over shared acquisition events by compact indexes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    events: MeasurementAcquisitionEvents = Field(default_factory=_empty_evidence_events)
    entries: MeasurementAcquisitionEvidenceEntries = Field(
        default_factory=_empty_evidence_entries
    )
    variable_refs: MeasurementAcquisitionEvidenceRefs = Field(
        default_factory=_empty_evidence_refs
    )

    @field_validator("events", "entries")
    @classmethod
    def freeze_catalog_sequences[T](cls, value: Sequence[T]) -> Sequence[T]:
        return tuple(value)

    @classmethod
    def create(
        cls,
        evidence_by_variable: Mapping[str, MeasurementAcquisitionEvidence],
    ) -> MeasurementAcquisitionEvidenceCatalog:
        events: list[InstrumentAcquisitionEvent] = []
        event_indexes: dict[InstrumentAcquisitionEvent, int] = {}
        entries: list[MeasurementAcquisitionEvidenceRef] = []
        entry_indexes: dict[MeasurementAcquisitionEvidenceRef, int] = {}
        variable_refs: dict[str, int] = {}
        for variable_id in sorted(evidence_by_variable):
            entry = _evidence_ref(
                evidence_by_variable[variable_id],
                events=events,
                event_indexes=event_indexes,
            )
            entry_index = entry_indexes.get(entry)
            if entry_index is None:
                entry_index = len(entries)
                entries.append(entry)
                entry_indexes[entry] = entry_index
            variable_refs[variable_id] = entry_index
        return cls(events=events, entries=entries, variable_refs=variable_refs)

    @model_validator(mode="after")
    def validate_refs(self) -> MeasurementAcquisitionEvidenceCatalog:
        if len(self.events) != len(set(self.events)):
            raise ValueError("measurement acquisition events must be unique")
        if len(self.entries) != len(set(self.entries)):
            raise ValueError("measurement acquisition evidence entries must be unique")
        referenced_entries = set(self.variable_refs.values())
        if referenced_entries != set(range(len(self.entries))):
            raise ValueError(
                "measurement acquisition evidence entry indexes must be referenced"
            )
        referenced_events = {
            event_index
            for entry in self.entries
            for event_index in _evidence_ref_event_indices(entry)
        }
        if referenced_events != set(range(len(self.events))):
            raise ValueError(
                "measurement acquisition event indexes must be referenced exactly"
            )
        return self

    def for_variable(self, variable_id: str) -> MeasurementAcquisitionEvidence | None:
        entry_index = self.variable_refs.get(variable_id)
        return (
            None
            if entry_index is None
            else _evidence_from_ref(self.entries[entry_index], events=self.events)
        )

    def select(
        self,
        variable_ids: Sequence[str],
    ) -> MeasurementAcquisitionEvidenceCatalog:
        selected = set(variable_ids)
        return type(self).create(
            {
                variable_id: evidence
                for variable_id in self.variable_refs
                if variable_id in selected
                and (evidence := self.for_variable(variable_id)) is not None
            }
        )


def _evidence_ref(
    evidence: MeasurementAcquisitionEvidence,
    *,
    events: list[InstrumentAcquisitionEvent],
    event_indexes: dict[InstrumentAcquisitionEvent, int],
) -> MeasurementAcquisitionEvidenceRef:
    if isinstance(evidence, InstrumentAcquisitionEvidence):
        return _instrument_evidence_ref(
            evidence,
            events=events,
            event_indexes=event_indexes,
        )
    return EntityAcquisitionEvidenceRef(
        dimension_id=evidence.dimension_id,
        acquisition=evidence.acquisition,
        values=tuple(
            None
            if value is None
            else _instrument_evidence_ref(
                value,
                events=events,
                event_indexes=event_indexes,
            )
            for value in evidence.values
        ),
    )


def _instrument_evidence_ref(
    evidence: InstrumentAcquisitionEvidence,
    *,
    events: list[InstrumentAcquisitionEvent],
    event_indexes: dict[InstrumentAcquisitionEvent, int],
) -> InstrumentAcquisitionEvidenceRef:
    event = InstrumentAcquisitionEvent.from_result(evidence)
    event_index = event_indexes.get(event)
    if event_index is None:
        event_index = len(events)
        events.append(event)
        event_indexes[event] = event_index
    return InstrumentAcquisitionEvidenceRef(
        event_index=event_index,
        result_id=evidence.result_id,
    )


def _evidence_ref_event_indices(
    evidence: MeasurementAcquisitionEvidenceRef,
) -> tuple[int, ...]:
    if isinstance(evidence, InstrumentAcquisitionEvidenceRef):
        return (evidence.event_index,)
    return tuple(value.event_index for value in evidence.values if value is not None)


def _evidence_from_ref(
    evidence: MeasurementAcquisitionEvidenceRef,
    *,
    events: Sequence[InstrumentAcquisitionEvent],
) -> MeasurementAcquisitionEvidence:
    if isinstance(evidence, InstrumentAcquisitionEvidenceRef):
        return events[evidence.event_index].result(evidence.result_id)
    return EntityAcquisitionEvidence(
        dimension_id=evidence.dimension_id,
        acquisition=evidence.acquisition,
        values=tuple(
            None if value is None else events[value.event_index].result(value.result_id)
            for value in evidence.values
        ),
    )


class MeasurementRecord(_FrozenMeasurementModel):
    """One durable point row with immutable values, evidence, and metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    logical_point_id: str | None = None
    point_index: int
    coordinates: MeasurementValueMap
    observables: MeasurementValueMap
    acquisition_evidence: MeasurementAcquisitionEvidenceCatalog = Field(
        default_factory=MeasurementAcquisitionEvidenceCatalog
    )
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @model_validator(mode="after")
    def validate_acquisition_evidence_variables(self) -> MeasurementRecord:
        unknown = set(self.acquisition_evidence.variable_refs) - (
            set(self.coordinates) | set(self.observables)
        )
        if unknown:
            raise ValueError(
                "measurement acquisition evidence references unknown variables: "
                + ", ".join(sorted(unknown))
            )
        return self


class MeasurementDataset(_FrozenMeasurementModel):
    """A complete planned schema paired with its current ordered record set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_schema: MeasurementDatasetSchema
    records: Sequence[MeasurementRecord]
    metadata: MeasurementMetadata = Field(default_factory=_empty_metadata)

    @field_validator("records")
    @classmethod
    def freeze_records(
        cls,
        value: Sequence[MeasurementRecord],
    ) -> Sequence[MeasurementRecord]:
        return tuple(value)


def _measurement_ndarray(
    value: object,
    *,
    dtype: MeasurementDType,
) -> MeasurementArrayData:
    try:
        candidate = cast("NDArray[MeasurementArrayElement]", np.asarray(value))
    except ValueError as error:
        raise ValueError("measurement array values must be rectangular") from error

    if candidate.dtype.kind == "O":
        normalized = _normalize_array_tree(
            cast("object", candidate.tolist()), dtype=dtype
        )
        candidate = cast(
            "NDArray[MeasurementArrayElement]",
            np.asarray(normalized),
        )

    _validate_array_kind(candidate, dtype=dtype)
    try:
        selected = np.asarray(
            candidate,
            dtype=_numpy_dtype(dtype),
        )
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"measurement array values do not fit {dtype}") from error

    if dtype in {"float64", "complex128"} and not np.isfinite(selected).all():
        raise ValueError("measurement values must be finite")
    return cast(
        "MeasurementArrayData",
        freeze_ndarray(selected),
    )


def _measurement_validity_array(value: object) -> NDArray[np.bool_]:
    selected = _measurement_ndarray(value, dtype="bool")
    return cast("NDArray[np.bool_]", selected)


def _measurement_scalar_data(
    value: object,
    *,
    dtype: MeasurementDType,
) -> MeasurementScalarData:
    is_boolean = isinstance(value, bool | np.bool_)
    if dtype == "float64":
        if is_boolean or not isinstance(value, int | float | np.integer | np.floating):
            raise ValueError("float64 measurement scalar value must be numeric")
        try:
            selected_float = float(cast("SupportsFloat", value))
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError("measurement scalar value does not fit float64") from error
        if not math.isfinite(selected_float):
            raise ValueError("measurement values must be finite")
        return selected_float
    if dtype == "int64":
        if is_boolean or not isinstance(value, int | np.integer):
            raise ValueError("int64 measurement scalar value must be an integer")
        selected_int = int(cast("SupportsIndex", value))
        if not np.iinfo(np.int64).min <= selected_int <= np.iinfo(np.int64).max:
            raise ValueError("measurement scalar value does not fit int64")
        return selected_int
    if dtype == "complex128":
        if isinstance(value, Mapping):
            selected_complex = _complex_from_mapping(
                cast("Mapping[object, object]", value)
            )
        else:
            if is_boolean or not isinstance(
                value,
                int | float | complex | np.number,
            ):
                raise ValueError("complex128 measurement scalar value must be numeric")
            try:
                selected_complex = complex(cast("SupportsComplex", value))
            except (OverflowError, TypeError, ValueError) as error:
                raise ValueError(
                    "measurement scalar value does not fit complex128"
                ) from error
        if not (
            math.isfinite(selected_complex.real)
            and math.isfinite(selected_complex.imag)
        ):
            raise ValueError("measurement values must be finite")
        return selected_complex
    if dtype == "bool":
        if not is_boolean:
            raise ValueError("bool measurement scalar value must be a boolean")
        return bool(value)
    if not isinstance(value, str):
        raise ValueError("string measurement scalar value must be a string")
    return value


def _complex_from_mapping(value: Mapping[object, object]) -> complex:
    if set(value) != {"real", "imag"}:
        raise ValueError(
            "complex measurement value must contain only real and imag components"
        )
    real = _complex_component(value["real"])
    imag = _complex_component(value["imag"])
    return complex(real, imag)


def _complex_component(value: object) -> float:
    if isinstance(value, bool | np.bool_) or not isinstance(
        value,
        int | float | np.integer | np.floating,
    ):
        raise ValueError("complex measurement components must be real numeric values")
    try:
        selected = float(cast("SupportsFloat", value))
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(
            "complex measurement component does not fit float64"
        ) from error
    if not math.isfinite(selected):
        raise ValueError("measurement values must be finite")
    return selected


def _normalize_array_tree(value: object, *, dtype: MeasurementDType) -> object:
    if isinstance(value, list | tuple):
        selected = cast("list[object] | tuple[object, ...]", value)
        return tuple(_normalize_array_tree(item, dtype=dtype) for item in selected)

    if dtype == "complex128" and isinstance(value, Mapping):
        return _complex_from_mapping(cast("Mapping[object, object]", value))

    is_boolean = isinstance(value, bool | np.bool_)
    if dtype == "float64":
        if is_boolean or not isinstance(value, int | float | np.integer | np.floating):
            raise ValueError("float64 measurement array values must be numeric")
        return float(cast("SupportsFloat", value))
    if dtype == "int64":
        if is_boolean or not isinstance(value, int | np.integer):
            raise ValueError("int64 measurement array values must be integers")
        return int(cast("SupportsIndex", value))
    if dtype == "complex128":
        if is_boolean or not isinstance(value, int | float | complex | np.number):
            raise ValueError("complex128 measurement array values must be numeric")
        return complex(cast("SupportsComplex", value))
    if dtype == "bool":
        if not is_boolean:
            raise ValueError("bool measurement array values must be booleans")
        return bool(value)
    if not isinstance(value, str):
        raise ValueError("string measurement array values must be strings")
    return value


def _validate_array_kind(
    value: NDArray[MeasurementArrayElement],
    *,
    dtype: MeasurementDType,
) -> None:
    if value.size == 0:
        return
    allowed_kinds = {
        "float64": "iuf",
        "int64": "iu",
        "complex128": "iufc",
        "bool": "b",
        "string": "U",
    }
    if value.dtype.kind not in allowed_kinds[dtype]:
        raise ValueError(f"measurement array values do not match {dtype}")
    if (
        dtype == "int64"
        and value.dtype.kind == "u"
        and np.max(cast("NDArray[np.uint64]", value)) > np.iinfo(np.int64).max
    ):
        raise ValueError("measurement array values do not fit int64")


def _numpy_dtype(dtype: MeasurementDType) -> np.dtype[np.generic]:
    if dtype == "float64":
        return np.dtype(np.float64)
    if dtype == "int64":
        return np.dtype(np.int64)
    if dtype == "complex128":
        return np.dtype(np.complex128)
    if dtype == "bool":
        return np.dtype(np.bool_)
    return np.dtype(np.str_)


def _complex_array_json(value: object) -> object:
    if isinstance(value, list):
        return [_complex_array_json(item) for item in cast("list[object]", value)]
    if not isinstance(value, complex):
        raise TypeError("complex array serialization requires complex values")
    selected = value
    return {"real": selected.real, "imag": selected.imag}
