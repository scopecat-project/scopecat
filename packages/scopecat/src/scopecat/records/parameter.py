"""Parameter and quantity models."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, Protocol, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    TypeAdapter,
    field_serializer,
    field_validator,
)

from scopecat.kernel.entity import EntityRef, normalize_entity_metadata
from scopecat.kernel.frozen import FrozenMapping, freeze_json_mapping
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_type_wire import PersistableScalarWire
from scopecat.kernel.value_types import (
    Bool as BoolType,
)
from scopecat.kernel.value_types import (
    Entity as EntityType,
)
from scopecat.kernel.value_types import (
    Float as FloatType,
)
from scopecat.kernel.value_types import (
    Int as IntType,
)
from scopecat.kernel.value_types import (
    Quantity as QuantityType,
)
from scopecat.kernel.value_types import (
    Scalar,
    Table,
    TableColumn,
)
from scopecat.kernel.value_types import (
    String as StringType,
)


class _Identified(Protocol):
    @property
    def id(self) -> str: ...


def _ensure_unique_ids[T: _Identified](items: list[T], label: str) -> list[T]:
    seen: set[str] = set()
    for item in items:
        item_id = item.id
        if item_id in seen:
            msg = f"duplicate {label} id: {item_id}"
            raise ValueError(msg)
        seen.add(item_id)
    return items


def _require_persistable_scalar_type(value: Scalar, *, path: str) -> Scalar:
    if not isinstance(
        value.atom,
        BoolType | IntType | FloatType | StringType | QuantityType | EntityType,
    ):
        msg = (
            f"{path} supports only bool, int, float, string, quantity, and entity atoms"
        )
        raise ValueError(msg)
    if isinstance(value.atom, FloatType | QuantityType) and not value.atom.finite:
        msg = f"{path} numeric atoms must require finite values"
        raise ValueError(msg)
    return value


def _validate_persistable_value_type(value: Scalar | Table) -> Scalar | Table:
    if isinstance(value, Scalar):
        return _require_persistable_scalar_type(value, path="persisted parameter")
    for column in value.columns:
        _require_persistable_scalar_type(
            column.value_type,
            path=f"persisted parameter table column {column.id!r}",
        )
    return value


class _PersistableScalarTypeWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: Literal["scalar"]
    atom: PersistableScalarWire


class _PersistableTableColumnWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(strict=True, min_length=1)]
    value_type: PersistableScalarWire


class _PersistableTableTypeWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: Literal["table"]
    columns: tuple[_PersistableTableColumnWire, ...]
    primary_key: tuple[Annotated[str, Field(strict=True)], ...] = ()


type _PersistableValueTypeWire = Annotated[
    _PersistableScalarTypeWire | _PersistableTableTypeWire,
    Field(discriminator="shape"),
]

_PERSISTABLE_VALUE_TYPE_ADAPTER = TypeAdapter[_PersistableValueTypeWire](
    _PersistableValueTypeWire
)


def _persistable_value_type_from_wire(value: object) -> Scalar | Table:
    if isinstance(value, Scalar | Table):
        return value
    wire = _PERSISTABLE_VALUE_TYPE_ADAPTER.validate_python(value)
    if isinstance(wire, _PersistableScalarTypeWire):
        return wire.atom
    return Table(
        columns=tuple(
            TableColumn(id=column.id, value_type=column.value_type)
            for column in wire.columns
        ),
        primary_key=wire.primary_key,
    )


def _persistable_value_type_to_wire(
    value: Scalar | Table,
) -> _PersistableValueTypeWire:
    if isinstance(value, Scalar):
        return _PersistableScalarTypeWire(shape="scalar", atom=value)
    return _PersistableTableTypeWire(
        shape="table",
        columns=tuple(
            _PersistableTableColumnWire(
                id=column.id,
                value_type=column.value_type,
            )
            for column in value.columns
        ),
        primary_key=value.primary_key,
    )


type PersistableValueType = Annotated[
    Scalar | Table,
    BeforeValidator(
        _persistable_value_type_from_wire,
        json_schema_input_type=_PersistableValueTypeWire,
    ),
    PlainSerializer(
        _persistable_value_type_to_wire,
        return_type=_PersistableValueTypeWire,
    ),
]


def _require_closed_parameter_atom_input(value: object) -> object:
    """Reject coercions from values outside the durable scalar domain."""

    if isinstance(value, Quantity | EntityRef | str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        # Raw JSON objects are retained for Pydantic to validate as Quantity or
        # EntityRef. Freeze first so nested values cannot use Python-only
        # coercions (for example bytes -> string).
        return freeze_json_mapping(
            cast("Mapping[str, object]", value),
            path="persisted parameter scalar",
        )
    msg = (
        "persisted parameter scalar values must be quantity, entity, bool, int, "
        "float, or string"
    )
    raise ValueError(msg)


type ParameterAtomValue = Annotated[
    Quantity | EntityRef | bool | int | float | str,
    BeforeValidator(_require_closed_parameter_atom_input),
]


def _validate_finite_parameter_scalar(
    value: ParameterAtomValue,
) -> ParameterAtomValue:
    if isinstance(value, EntityRef):
        return value.model_copy(
            update={"metadata": normalize_entity_metadata(value.metadata)},
            deep=True,
        )
    number = value.value if isinstance(value, Quantity) else value
    if isinstance(number, float) and not math.isfinite(number):
        msg = "persisted parameter scalar values must be finite"
        raise ValueError(msg)
    return value


class ParameterDefinition(BaseModel):
    """Stable type definition for one accepted parameter value."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    id: str
    value_type: PersistableValueType
    description: str | None = None

    @field_validator("value_type")
    @classmethod
    def validate_value_type(cls, value: Scalar | Table) -> Scalar | Table:
        return _validate_persistable_value_type(value)


class ParameterCatalog(BaseModel):
    """Authored parameter schema in one shape-independent namespace."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    id: str
    definitions: Sequence[ParameterDefinition] = Field(default_factory=tuple)

    @field_validator("definitions")
    @classmethod
    def validate_definitions(
        cls, value: Sequence[ParameterDefinition]
    ) -> Sequence[ParameterDefinition]:
        return tuple(_ensure_unique_ids(list(value), "parameter definition"))

    def get(self, definition_id: str) -> ParameterDefinition | None:
        for definition in self.definitions:
            if definition.id == definition_id:
                return definition
        return None


class _StoredParameterValue(BaseModel):
    """Shared recursively immutable state for one stored parameter value."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        frozen=True,
    )

    id: str


class ScalarParameterValue(_StoredParameterValue):
    """One stored scalar parameter value."""

    shape: Literal["scalar"] = "scalar"
    value: ParameterAtomValue

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: ParameterAtomValue) -> ParameterAtomValue:
        return _validate_finite_parameter_scalar(value)


class TableParameterValue(_StoredParameterValue):
    """One stored typed table parameter."""

    shape: Literal["table"] = "table"
    rows: Sequence[Mapping[str, ParameterAtomValue]] = Field(default_factory=tuple)

    @field_validator("rows")
    @classmethod
    def validate_rows(
        cls,
        value: Sequence[Mapping[str, ParameterAtomValue]],
    ) -> Sequence[Mapping[str, ParameterAtomValue]]:
        return tuple(
            FrozenMapping(
                (column_id, _validate_finite_parameter_scalar(cell))
                for column_id, cell in row.items()
            )
            for row in value
        )

    @field_serializer("rows")
    def serialize_rows(
        self, value: Sequence[Mapping[str, ParameterAtomValue]]
    ) -> list[dict[str, ParameterAtomValue]]:
        return [dict(row) for row in value]


type StoredParameterValue = Annotated[
    ScalarParameterValue | TableParameterValue,
    Field(discriminator="shape"),
]


class ParameterSnapshot(BaseModel):
    """Recursively immutable accepted parameters for future runs."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    id: str
    values: Sequence[StoredParameterValue] = Field(default_factory=tuple)

    @field_validator("values")
    @classmethod
    def validate_values(
        cls,
        value: Sequence[StoredParameterValue],
    ) -> Sequence[StoredParameterValue]:
        return tuple(_ensure_unique_ids(list(value), "stored parameter value"))

    def get(self, value_id: str) -> StoredParameterValue | None:
        for value in self.values:
            if value.id == value_id:
                return value
        return None
