"""Standard dataclass declarations for existing parameter table schemas."""

from __future__ import annotations

import types
from dataclasses import dataclass, fields, is_dataclass
from typing import Annotated, Protocol, cast, get_args, get_origin, get_type_hints

from scopecat.kernel.entity import EntityRef
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
class DataclassParameterField:
    """Resolved field declaration shared by schema inference and live views."""

    name: str
    python_type: type
    optional: bool
    value_type: Scalar


class _DataclassOptions(Protocol):
    frozen: bool


def dataclass_parameter_fields(row_type: type) -> tuple[DataclassParameterField, ...]:
    """Resolve supported ordinary dataclass fields without constructing a row."""
    if not is_dataclass(row_type):
        raise TypeError("row_type must be a standard dataclass")
    options = cast("_DataclassOptions", getattr(row_type, "__dataclass_params__", None))
    if options.frozen:
        raise TypeError(
            "parameter rows require a mutable dataclass; remove frozen=True"
        )
    hints = cast("dict[str, object]", get_type_hints(row_type, include_extras=True))
    result: list[DataclassParameterField] = []
    for field in fields(row_type):
        result.append(
            resolve_parameter_field(
                field.name,
                hints[field.name],
                field.metadata.get("parameter"),
                label=f"{row_type.__name__}.{field.name}",
            )
        )
    return tuple(result)


def resolve_parameter_field(
    name: str, annotation: object, metadata: object = None, *, label: str
) -> DataclassParameterField:
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
    atom = _atom(annotation, spec, label=f"{label}")
    assert isinstance(annotation, type)
    return DataclassParameterField(name, annotation, optional, Scalar(atom))


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


def dataclass_table_schema(row_type: type, *, primary_key: tuple[str, ...]) -> Table:
    """Infer a table declaration; this does not install or migrate a project schema.

    Optional fields describe unknown cells, not a nullable wire scalar. Defaults
    remain ordinary constructor behavior and are never applied to existing rows.
    """
    return table_from_parameter_fields(
        dataclass_parameter_fields(row_type),
        primary_key=primary_key,
        label=row_type.__name__,
    )


def table_from_parameter_fields(
    declarations: tuple[DataclassParameterField, ...],
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


__all__ = ["ParameterSpec", "dataclass_table_schema"]
