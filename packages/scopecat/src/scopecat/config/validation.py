"""Validation for durable parameter values against unified value types."""

from __future__ import annotations

from typing import NoReturn, cast

from scopecat.kernel.quantity import Quantity as QuantityValue
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
from scopecat.kernel.value_validation import (
    ValuePath,
    ValueValidationError,
    coerce_literal,
    coerce_table_rows,
)
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterDefinition,
    ScalarParameterValue,
    StoredParameterValue,
    TableParameterValue,
)


class ParameterValueValidationError(ValueError):
    """A stored parameter value does not satisfy its catalog definition."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: ValuePath | None = None,
    ) -> None:
        self.code = code
        self.path = path
        super().__init__(message)


def coerce_parameter_atom(
    *,
    parameter_id: str,
    value_type: Scalar,
    value: object,
    path: ValuePath,
) -> ParameterAtomValue:
    """Validate and normalize one durable scalar atom."""

    try:
        return cast(
            "ParameterAtomValue",
            coerce_literal(value_type, value, path=path),
        )
    except ValueValidationError as error:
        atom = value_type.atom
        match atom:
            case Bool():
                label = "bool"
                code = "invalid_parameter_bool"
            case Int():
                label = "int"
                code = "invalid_parameter_int"
            case Float():
                label = "number"
                code = "invalid_parameter_number"
            case String():
                label = "string"
                code = "invalid_parameter_string"
            case Quantity():
                label = "quantity"
                code = (
                    "incompatible_parameter_quantity_unit"
                    if error.code == "incompatible_unit"
                    else "invalid_parameter_quantity"
                )
            case Entity():
                label = "entity"
                code = "invalid_parameter_entity"
            case _:  # PersistableValueType rejects non-durable atoms.
                label = type(atom).__name__.lower()
                code = "invalid_parameter_value"
        msg = f"parameter {parameter_id} requires {label}: {error.reason}"
        raise ParameterValueValidationError(code, msg, path=error.path) from error


def coerce_stored_parameter_value(
    definition: ParameterDefinition,
    stored: StoredParameterValue,
    *,
    path: ValuePath,
    allow_missing: bool = False,
) -> StoredParameterValue:
    """Validate and normalize one stored value using its catalog type."""

    value_type = definition.value_type
    if isinstance(value_type, Scalar):
        if not isinstance(stored, ScalarParameterValue):
            _raise_shape_mismatch(definition, stored, expected="scalar", path=path)
        return stored.model_copy(
            update={
                "value": coerce_parameter_atom(
                    parameter_id=definition.id,
                    value_type=value_type,
                    value=stored.value,
                    path=(*path, "value"),
                )
            }
        )
    if not isinstance(stored, TableParameterValue):
        _raise_shape_mismatch(definition, stored, expected="table", path=path)
    rows = _coerce_parameter_table(
        parameter_id=definition.id,
        value_type=value_type,
        value=stored.rows,
        path=(*path, "rows"),
        allow_missing=allow_missing,
    )
    return stored.model_copy(
        update={"rows": cast("tuple[dict[str, ParameterAtomValue], ...]", rows)}
    )


def coerce_parameter_table_cell(
    *,
    parameter_id: str,
    column: TableColumn,
    value: object,
    path: ValuePath,
) -> ParameterAtomValue:
    """Validate and normalize one typed parameter-table cell."""

    return coerce_parameter_atom(
        parameter_id=parameter_id,
        value_type=column.value_type,
        value=value,
        path=path,
    )


def _coerce_parameter_table(
    *,
    parameter_id: str,
    value_type: Table,
    value: object,
    path: ValuePath,
    allow_missing: bool = False,
) -> object:
    try:
        return coerce_table_rows(
            value_type, value, path=path, allow_missing=allow_missing
        )
    except ValueValidationError as error:
        msg = f"parameter {parameter_id}: {error.reason}"
        raise ParameterValueValidationError(
            "invalid_parameter_value",
            msg,
            path=error.path,
        ) from error


def _raise_shape_mismatch(
    definition: ParameterDefinition,
    stored: StoredParameterValue,
    *,
    expected: str,
    path: ValuePath,
) -> NoReturn:
    msg = f"parameter {definition.id} requires {expected} shape, got {stored.shape}"
    raise ParameterValueValidationError(
        "parameter_shape_mismatch",
        msg,
        path=path,
    )


def validate_parameter_representation(
    definition: ParameterDefinition, stored: StoredParameterValue
) -> StoredParameterValue:
    """Validate candidate values, retaining explicit compatible quantity units.

    Execution resolution still converts quantities to the catalog units. Candidate
    editing must not turn that transient conversion into unrelated stored edits.
    """
    normalized = coerce_stored_parameter_value(
        definition, stored, path=("parameter_snapshot", "values", definition.id)
    )
    if isinstance(stored, ScalarParameterValue):
        return stored if isinstance(stored.value, QuantityValue) else normalized
    assert isinstance(normalized, TableParameterValue)
    return normalized.model_copy(
        update={
            "rows": tuple(
                {
                    column: original[column]
                    if isinstance(original[column], QuantityValue)
                    else value
                    for column, value in row.items()
                }
                for original, row in zip(stored.rows, normalized.rows, strict=True)
            )
        }
    )
