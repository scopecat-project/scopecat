"""Shared compatibility and literal inference for first-class value types."""

from __future__ import annotations

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity as QuantityValue
from scopecat.kernel.units import compatible_units, unit_kind
from scopecat.kernel.value_types import (
    Array,
    AtomType,
    Bool,
    Complex,
    Entity,
    Float,
    Int,
    Payload,
    Quantity,
    Scalar,
    String,
    Table,
    ValueType,
)
from scopecat.kernel.value_validation import ValuePath, ValueValidationError


def require_assignable(
    source: ValueType,
    target: ValueType,
    *,
    path: ValuePath,
) -> None:
    """Require every value admitted by ``source`` to be accepted by ``target``."""

    if is_assignable(source, target):
        return
    raise ValueValidationError(
        path,
        f"expected {describe_value_type(target)}, got {describe_value_type(source)}",
        code="incompatible_value_type",
    )


def is_assignable(source: ValueType, target: ValueType) -> bool:
    """Return whether a typed producer can safely feed a typed consumer."""

    if isinstance(source, Scalar) and isinstance(target, Scalar):
        return _scalar_assignable(source, target)
    if isinstance(source, Array) and isinstance(target, Array):
        return _array_assignable(source, target)
    if isinstance(source, Table) and isinstance(target, Table):
        return _table_assignable(source, target)
    return False


def describe_value_type(value_type: ValueType) -> str:
    if isinstance(value_type, Scalar):
        return f"Scalar[{_describe_atom(value_type.atom)}]"
    if isinstance(value_type, Array):
        dimensions = ", ".join(
            f"{dimension.id}={dimension.size if dimension.size is not None else '*'}"
            for dimension in value_type.dimensions
        )
        unit = f", unit={value_type.unit!r}" if value_type.unit is not None else ""
        return f"Array[{value_type.dtype}; {dimensions}{unit}]"
    columns = ", ".join(
        f"{column.id}: {describe_value_type(column.value_type)}"
        for column in value_type.columns
    )
    return f"Table{{{columns}}}"


def literal_scalar_type(value: object) -> Scalar:
    """Infer the narrow scalar type of one closed literal."""

    return _literal_scalar_type(value)


def _scalar_assignable(source: Scalar, target: Scalar) -> bool:
    return _atom_assignable(source.atom, target.atom)


def _array_assignable(source: Array, target: Array) -> bool:
    if source.dtype != target.dtype or source.unit != target.unit:
        return False
    if len(source.dimensions) != len(target.dimensions):
        return False
    return all(
        source_dimension.id == target_dimension.id
        and source_dimension.kind == target_dimension.kind
        and source_dimension.unit == target_dimension.unit
        and (
            target_dimension.size is None
            or source_dimension.size == target_dimension.size
        )
        for source_dimension, target_dimension in zip(
            source.dimensions,
            target.dimensions,
            strict=True,
        )
    )


def _atom_assignable(source: AtomType, target: AtomType) -> bool:
    if type(source) is not type(target):
        return False
    if isinstance(source, Bool) and isinstance(target, Bool):
        return True
    if isinstance(source, Int) and isinstance(target, Int):
        return _numeric_constraints_are_subset(source, target)
    if isinstance(source, Float) and isinstance(target, Float):
        return (not target.finite or source.finite) and _numeric_constraints_are_subset(
            source, target
        )
    if isinstance(source, Complex) and isinstance(target, Complex):
        return source.unit == target.unit
    if isinstance(source, String) and isinstance(target, String):
        if target.choices is not None:
            return source.choices is not None and set(source.choices) <= set(
                target.choices
            )
        return True
    if isinstance(source, Quantity) and isinstance(target, Quantity):
        source_dimension = source.dimension or (
            unit_kind(source.unit) if source.unit is not None else None
        )
        target_dimension = target.dimension or (
            unit_kind(target.unit) if target.unit is not None else None
        )
        if target_dimension is not None and source_dimension != target_dimension:
            return False
        if (
            source.unit is not None
            and target.unit is not None
            and not compatible_units(source.unit, target.unit)
        ):
            return False
        if target.finite and not source.finite:
            return False
        source_minimum, source_maximum = _quantity_bounds_in_target_unit(
            source,
            target,
        )
        return _bounds_are_subset(
            source_minimum,
            source_maximum,
            target.minimum,
            target.maximum,
        )
    if isinstance(source, Entity) and isinstance(target, Entity):
        return target.entity_kind is None or source.entity_kind == target.entity_kind
    if isinstance(source, Payload) and isinstance(target, Payload):
        return source.schema_id == target.schema_id
    return False


def _table_assignable(source: Table, target: Table) -> bool:
    source_columns = {column.id: column for column in source.columns}
    target_columns = {column.id: column for column in target.columns}
    if set(source_columns) != set(target_columns):
        return False
    for target_column in target.columns:
        source_column = source_columns[target_column.id]
        if not _scalar_assignable(source_column.value_type, target_column.value_type):
            return False
    if target.primary_key:
        if not source.primary_key:
            return False
        if not set(source.primary_key) <= set(target.primary_key):
            return False
    return True


def _numeric_constraints_are_subset(source: Int | Float, target: Int | Float) -> bool:
    return _bounds_are_subset(
        source.minimum,
        source.maximum,
        target.minimum,
        target.maximum,
    )


def _bounds_are_subset(
    source_minimum: float | None,
    source_maximum: float | None,
    target_minimum: float | None,
    target_maximum: float | None,
) -> bool:
    if target_minimum is not None and (
        source_minimum is None or source_minimum < target_minimum
    ):
        return False
    return not (
        target_maximum is not None
        and (source_maximum is None or source_maximum > target_maximum)
    )


def _quantity_bounds_in_target_unit(
    source: Quantity,
    target: Quantity,
) -> tuple[float | None, float | None]:
    if source.unit is None or target.unit is None or source.unit == target.unit:
        return source.minimum, source.maximum
    source_unit = source.unit
    target_unit = target.unit

    def convert(value: float | None) -> float | None:
        if value is None:
            return None
        return QuantityValue(value=value, unit=source_unit).to(target_unit).value

    return convert(source.minimum), convert(source.maximum)


def _literal_scalar_type(value: object) -> Scalar:
    if value is None:
        msg = "null literals are not supported"
        raise TypeError(msg)
    if isinstance(value, bool):
        return Scalar(Bool())
    if isinstance(value, int):
        return Scalar(Int(minimum=value, maximum=value))
    if isinstance(value, float):
        return Scalar(Float(minimum=value, maximum=value))
    if isinstance(value, complex):
        return Scalar(Complex())
    if isinstance(value, str):
        return Scalar(String())
    if isinstance(value, QuantityValue):
        return Scalar(
            Quantity(unit=value.unit, minimum=value.value, maximum=value.value)
        )
    if isinstance(value, EntityRef):
        return Scalar(Entity(entity_kind=value.kind))
    if isinstance(value, PayloadValue):
        return Scalar(Payload(value.schema_id))
    return Scalar(Payload(type(value).__qualname__))


def _describe_atom(atom: AtomType) -> str:
    if isinstance(atom, Quantity):
        constraint = atom.unit or atom.dimension
        return f"Quantity[{constraint}]" if constraint is not None else "Quantity"
    if isinstance(atom, Entity):
        return (
            f"Entity[{atom.entity_kind}]" if atom.entity_kind is not None else "Entity"
        )
    if isinstance(atom, Payload):
        return f"Payload[{atom.schema_id}]"
    return type(atom).__name__
