"""Canonical field resolution shared by parameter declaration adapters."""

from __future__ import annotations

import types
from dataclasses import dataclass, replace
from typing import Annotated, Literal, cast, get_args, get_origin

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity as QuantityValue
from scopecat.kernel.units import compatible_units, convert_linear_value
from scopecat.kernel.value_types import (
    Bool,
    Entity,
    Float,
    Int,
    Quantity,
    Scalar,
    String,
    Table,
    TableColumn,
)
from scopecat.records.parameter import ParameterAtomValue


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """Runtime units and inclusive bounds; Annotated does not add static unit algebra.

    Use ``Annotated[float, ParameterSpec(unit="GHz")]`` or the equivalent
    ``field(metadata={"parameter": ParameterSpec(unit="GHz")})``.
    """

    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    entity_kind: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterColumn:
    """A runtime column declaration, independent of any Python row class."""

    annotation: object
    spec: ParameterSpec


def column(
    annotation: object,
    *,
    unit: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    entity_kind: str | None = None,
) -> ParameterColumn:
    """Attach units/bounds/entity metadata to a dynamic Python type declaration."""
    return ParameterColumn(
        annotation, ParameterSpec(unit, minimum, maximum, entity_kind)
    )


def dynamic_parameter_field(name: str, declaration: object) -> ResolvedParameterField:
    if isinstance(declaration, ParameterColumn):
        return resolve_parameter_field(
            name, declaration.annotation, declaration.spec, label=name
        )
    return resolve_parameter_field(name, declaration, label=name)


@dataclass(frozen=True, slots=True)
class ResolvedParameterField:
    """Resolved field declaration shared by schema inference and live views."""

    name: str
    python_type: type
    optional: bool
    value_type: Scalar


def resolve_parameter_field(
    name: str, annotation: object, metadata: object = None, *, label: str
) -> ResolvedParameterField:
    """Resolve one declaration for dataclass and descriptor parameter frontends."""
    optional = False
    while True:
        if get_origin(annotation) is Annotated:
            annotation, *extras = cast("tuple[object, ...]", get_args(annotation))
            specs = [item for item in extras if isinstance(item, ParameterSpec)]
            if specs:
                if len(specs) != 1 or metadata is not None:
                    raise TypeError(f"{label}: specify ParameterSpec once")
                metadata = specs[0]
            continue
        if get_origin(annotation) is types.UnionType:
            args = cast("tuple[object, ...]", get_args(annotation))
            if len(args) == 2 and type(None) in args:
                optional = True
                annotation = next(item for item in args if item is not type(None))
                continue
        break
    if metadata is not None and not isinstance(metadata, ParameterSpec):
        raise TypeError(f"{label}: parameter metadata must be ParameterSpec")
    spec = metadata or ParameterSpec()
    if get_origin(annotation) is Literal:
        choices = cast("tuple[object, ...]", get_args(annotation))
        if not choices or not all(isinstance(value, str) for value in choices):
            raise TypeError(f"{label}: parameter Literal choices must be strings")
        atom = _atom(str, spec, label=label)
        assert isinstance(atom, String)
        atom = String(choices=cast("tuple[str, ...]", choices))
        python_type = str
    else:
        atom = _atom(annotation, spec, label=label)
        assert isinstance(annotation, type)
        python_type = annotation
    return ResolvedParameterField(name, python_type, optional, Scalar(atom))


def _atom(
    annotation: object, spec: ParameterSpec, *, label: str
) -> Bool | Int | Float | String | Quantity | Entity:
    if spec.unit is not None:
        if annotation is not float:
            raise TypeError(f"{label}: unit metadata requires float")
        if spec.entity_kind is not None:
            raise TypeError(f"{label}: unit and entity_kind cannot be combined")
        return Quantity(unit=spec.unit, minimum=spec.minimum, maximum=spec.maximum)
    if spec.entity_kind is not None and annotation is not EntityRef:
        raise TypeError(f"{label}: entity_kind requires EntityRef")
    if annotation is float:
        return Float(minimum=spec.minimum, maximum=spec.maximum)
    if annotation is int:
        if any(
            bound is not None and not isinstance(bound, int)
            for bound in (spec.minimum, spec.maximum)
        ):
            raise TypeError(f"{label}: int bounds must be integers")
        return Int(
            minimum=int(spec.minimum) if spec.minimum is not None else None,
            maximum=int(spec.maximum) if spec.maximum is not None else None,
        )
    if spec.minimum is not None or spec.maximum is not None:
        raise TypeError(f"{label}: bounds require int or float")
    if annotation is bool:
        return Bool()
    if annotation is str:
        return String()
    if annotation is EntityRef:
        return Entity(entity_kind=spec.entity_kind)
    raise TypeError(
        f"{label}: unsupported parameter type {annotation!r}; "
        "use bool, int, float, str or EntityRef"
    )


def table_from_parameter_fields(
    declarations: tuple[ResolvedParameterField, ...],
    *,
    primary_key: tuple[str, ...],
    label: str,
) -> Table:
    """Build the same wire schema for each supported declaration frontend."""
    for field in declarations:
        if field.name in primary_key and field.optional:
            raise TypeError(f"{label}.{field.name}: a primary key cannot be Optional")
    return Table(
        columns=tuple(
            TableColumn(field.name, field.value_type) for field in declarations
        ),
        primary_key=primary_key,
    )


def stored_parameter_value(
    value: object, field: ResolvedParameterField, *, label: str
) -> ParameterAtomValue:
    if value is None:
        raise ValueError(f"{label}: a required parameter cannot be None")
    atom = field.value_type.atom
    if isinstance(atom, Quantity):
        assert atom.unit is not None
        if isinstance(value, QuantityValue):
            return value.to(atom.unit)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return QuantityValue(float(value), atom.unit)
    # Ordinary dataclass assignments are not a runtime validation boundary.
    # The existing workspace validates values when previewing or saving.
    return cast("ParameterAtomValue", value)


def convert_parameter_bound(
    value: float | None, source: str, target: str
) -> float | None:
    if value is None or source == target:
        return value
    converted = convert_linear_value(value, source, target)
    if converted is None:
        raise ValueError(
            f"cannot convert parameter bound from {source!r} to {target!r}"
        )
    return converted


def require_parameter_field(
    declaration: Scalar, stored_type: Scalar, *, label: str
) -> None:
    """Bind editing and candidate fields under the same schema compatibility rule."""
    declared, stored = declaration.atom, stored_type.atom
    if (
        isinstance(stored, Quantity)
        and isinstance(declared, Quantity)
        and stored.unit is not None
        and declared.unit is not None
        and compatible_units(stored.unit, declared.unit)
    ):
        stored = replace(
            stored,
            unit=declared.unit,
            dimension=declared.dimension,
            minimum=convert_parameter_bound(stored.minimum, stored.unit, declared.unit),
            maximum=convert_parameter_bound(stored.maximum, stored.unit, declared.unit),
        )
    if stored != declared:
        raise TypeError(
            f"{label}"
            ": declared type/unit/bounds "
            f"{declared!r}"
            " differ from table "
            f"{stored_type.atom!r}"
            "; match the declaration or change the schema explici"
            "tly"
        )
