"""Orthogonal value types shared by authoring and persisted schemas.

Shape and scalar content are deliberately independent:

* :class:`Scalar` and :class:`Table` describe shape.
* :class:`Bool`, :class:`Int`, :class:`Float`, :class:`Complex`, :class:`String`,
  :class:`Quantity`, :class:`Entity`, and :class:`Payload`
  describe scalar content.

This module contains definitions only. Runtime literal coercion lives in
``scopecat.kernel.value_validation`` so schema models can depend on these types
without depending on authoring or creating model import cycles.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from scopecat.kernel.units import is_supported_unit, unit_kind


@dataclass(frozen=True, slots=True)
class Bool:
    """Boolean scalar content."""


@dataclass(frozen=True, slots=True)
class Int:
    """Integral scalar content with optional inclusive bounds."""

    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        _validate_bounds(self.minimum, self.maximum, label="Int")


@dataclass(frozen=True, slots=True)
class Float:
    """Numeric scalar content with optional inclusive bounds."""

    minimum: float | None = None
    maximum: float | None = None
    finite: bool = True

    def __post_init__(self) -> None:
        _validate_bounds(self.minimum, self.maximum, label="Float")


@dataclass(frozen=True, slots=True)
class Complex:
    """Finite complex scalar content with an optional measurement unit.

    Real and imaginary components share the declared unit. Complex values have
    no ordering bounds; explicit unit conversion supports linear scales only.
    """

    unit: str | None = None

    def __post_init__(self) -> None:
        if self.unit is not None and not is_supported_unit(self.unit):
            raise ValueError(f"unsupported complex unit: {self.unit}")


@dataclass(frozen=True, slots=True)
class String:
    """String scalar content with optional closed choices."""

    choices: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.choices is not None:
            if not self.choices:
                msg = "String choices must not be empty"
                raise ValueError(msg)
            if len(set(self.choices)) != len(self.choices):
                msg = "String choices must be unique"
                raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Quantity:
    """Quantity content constrained by dimension, unit, and numeric bounds.

    Bounds are expressed in ``unit``. Consequently bounded quantities require
    an explicit unit, while an unbounded type may constrain only the dimension.
    """

    dimension: str | None = None
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    finite: bool = True

    def __post_init__(self) -> None:
        if self.unit is not None and not is_supported_unit(self.unit):
            msg = f"unsupported quantity unit: {self.unit}"
            raise ValueError(msg)
        if (
            self.unit is not None
            and self.dimension is not None
            and unit_kind(self.unit) != self.dimension
        ):
            msg = (
                f"quantity unit {self.unit!r} does not belong to dimension "
                f"{self.dimension!r}"
            )
            raise ValueError(msg)
        if (self.minimum is not None or self.maximum is not None) and self.unit is None:
            msg = "bounded Quantity requires an explicit unit"
            raise ValueError(msg)
        _validate_bounds(self.minimum, self.maximum, label="Quantity")


@dataclass(frozen=True, slots=True)
class Entity:
    """Entity reference content, optionally constrained by domain kind."""

    entity_kind: str | None = None

    def __post_init__(self) -> None:
        if self.entity_kind is not None and not self.entity_kind:
            msg = "Entity entity_kind must be non-empty when provided"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Payload:
    """Opaque scalar content identified by a domain-owned schema id."""

    schema_id: str

    def __post_init__(self) -> None:
        _validate_id(self.schema_id, label="payload schema")


type AtomType = Bool | Int | Float | Complex | String | Quantity | Entity | Payload
type ValueDType = Literal["float64", "int64", "complex128", "bool", "string"]


@dataclass(frozen=True, slots=True)
class Scalar:
    """A single atom."""

    atom: AtomType


@dataclass(frozen=True, slots=True)
class ArrayDimension:
    """One local dimension of an array value.

    Local dimensions describe the shape available at one experiment point.
    They are intentionally distinct from scan axes, which create points.
    """

    id: str
    size: int | None
    kind: str | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.id, label="array dimension")
        if self.size is not None and self.size < 0:
            msg = "array dimension size must not be negative"
            raise ValueError(msg)
        if self.kind is not None and not self.kind:
            msg = "array dimension kind must be non-empty when provided"
            raise ValueError(msg)
        if self.unit is not None and not is_supported_unit(self.unit):
            msg = f"unsupported array dimension unit: {self.unit}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Array:
    """A typed dense array available at one experiment point."""

    dtype: ValueDType
    dimensions: tuple[ArrayDimension, ...]
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.dimensions:
            msg = "Array requires at least one local dimension"
            raise ValueError(msg)
        _validate_unique_ids(
            (dimension.id for dimension in self.dimensions),
            label="array dimensions",
        )
        if self.unit is not None and not is_supported_unit(self.unit):
            msg = f"unsupported array unit: {self.unit}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TableColumn:
    """One scalar column in a table type."""

    id: str
    value_type: Scalar

    def __post_init__(self) -> None:
        _validate_id(self.id, label="table column")


@dataclass(frozen=True, slots=True)
class Table:
    """A row collection with exact columns and an optional primary key."""

    columns: tuple[TableColumn, ...]
    primary_key: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_unique_ids(
            (column.id for column in self.columns),
            label="table columns",
        )
        if len(set(self.primary_key)) != len(self.primary_key):
            msg = "Table primary_key must not contain duplicates"
            raise ValueError(msg)
        columns = {column.id: column for column in self.columns}
        missing = [
            column_id for column_id in self.primary_key if column_id not in columns
        ]
        if missing:
            msg = "Table primary_key references unknown columns: " + ", ".join(missing)
            raise ValueError(msg)
        for column_id in self.primary_key:
            column = columns[column_id]
            if isinstance(column.value_type.atom, Payload | Complex):
                msg = (
                    f"Table primary key column {column_id!r} must use a primitive, "
                    "quantity, or entity atom"
                )
                raise ValueError(msg)
            if isinstance(column.value_type.atom, Float | Quantity) and not (
                column.value_type.atom.finite
            ):
                msg = (
                    f"Table primary key column {column_id!r} must guarantee "
                    "finite numeric values"
                )
                raise ValueError(msg)


type DataType = Scalar | Array
type ValueType = DataType | Table


def _validate_bounds(
    minimum: float | None,
    maximum: float | None,
    *,
    label: str,
) -> None:
    if any(
        isinstance(bound, float) and not math.isfinite(bound)
        for bound in (minimum, maximum)
    ):
        msg = f"{label} bounds must be finite"
        raise ValueError(msg)
    if minimum is not None and maximum is not None and minimum > maximum:
        msg = f"{label} minimum must not exceed maximum"
        raise ValueError(msg)


def _validate_id(value: str, *, label: str) -> None:
    if not value:
        msg = f"{label} id must be non-empty"
        raise ValueError(msg)


def _validate_unique_ids(values: Iterable[str], *, label: str) -> None:
    selected = tuple(values)
    duplicates = sorted({value for value in selected if selected.count(value) > 1})
    if duplicates:
        msg = f"{label} must be unique: {', '.join(duplicates)}"
        raise ValueError(msg)
