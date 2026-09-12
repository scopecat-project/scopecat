"""Standard dataclass adapter for parameter declarations."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Protocol, cast, get_type_hints

from scopecat.authoring.parameter_fields import (
    ParameterSpec,
    ResolvedParameterField,
    resolve_parameter_field,
    table_from_parameter_fields,
)
from scopecat.kernel.value_types import Table


class _DataclassOptions(Protocol):
    frozen: bool


def dataclass_parameter_fields(row_type: type) -> tuple[ResolvedParameterField, ...]:
    """Resolve supported ordinary dataclass fields without constructing a row."""
    if not is_dataclass(row_type):
        raise TypeError("row_type must be a standard dataclass")
    options = cast("_DataclassOptions", getattr(row_type, "__dataclass_params__", None))
    if options.frozen:
        raise TypeError(
            "parameter rows require a mutable dataclass; remove frozen=True"
        )
    hints = cast("dict[str, object]", get_type_hints(row_type, include_extras=True))
    result: list[ResolvedParameterField] = []
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


__all__ = ["ParameterSpec", "dataclass_table_schema"]
