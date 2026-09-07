"""Runtime coercion for the orthogonal value type system."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import cast

import numpy as np
from pydantic import ValidationError

from scopecat.kernel.entity import (
    EntityRef,
    normalize_entity_metadata,
)
from scopecat.kernel.numpy_storage import freeze_ndarray
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.problems import LocationPathItem
from scopecat.kernel.quantity import Quantity as QuantityValue
from scopecat.kernel.units import compatible_units, unit_kind
from scopecat.kernel.value_identity import scalar_identity, scalar_values_equal
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
    ValueDType,
    ValueType,
)

type ValuePath = tuple[LocationPathItem, ...]


def format_value_path(path: ValuePath) -> str:
    """Render a structured value path for human-readable exception text."""

    if not path:
        return ""
    selected = ""
    for index, item in enumerate(path):
        if isinstance(item, int):
            selected += f"[{item}]"
        elif index == 0 or item.isidentifier():
            selected += item if index == 0 else f".{item}"
        else:
            selected += f"[{item!r}]"
    return selected


class ValueValidationError(ValueError):
    """A literal does not satisfy a value type."""

    def __init__(
        self,
        path: ValuePath,
        reason: str,
        *,
        code: str = "invalid_value",
    ) -> None:
        self.path = path
        self.reason = reason
        self.code = code
        rendered_path = format_value_path(path)
        super().__init__(f"{rendered_path}: {reason}" if rendered_path else reason)


def coerce_literal(
    value_type: ValueType,
    value: object,
    *,
    path: ValuePath = ("value",),
) -> object:
    """Validate and normalize a Python literal for ``value_type``.

    Collections normalize to tuples, rows normalize to dictionaries, numeric
    float values normalize to ``float``, and entity strings normalize to
    :class:`~scopecat.kernel.entity.EntityRef` objects. Opaque payloads normalize
    to transient, schema-tagged runtime wrappers.
    """

    if value is None:
        raise ValueValidationError(path, "value must not be null")
    if isinstance(value_type, Scalar):
        return _coerce_atom(value_type.atom, value, path=path)
    if isinstance(value_type, Array):
        return _coerce_array(value_type, value, path=path)
    return _coerce_table(value_type, value, path=path)


def _coerce_array(
    value_type: Array,
    value: object,
    *,
    path: ValuePath,
) -> np.ndarray:
    expected_dtype = _numpy_dtype(value_type.dtype)
    try:
        source = np.asarray(value)
    except (TypeError, ValueError) as error:
        raise ValueValidationError(path, f"expected array, got {value!r}") from error
    if not np.can_cast(source.dtype, expected_dtype, casting="safe"):
        raise ValueValidationError(
            path,
            f"array dtype {source.dtype} cannot be safely converted to "
            f"{expected_dtype}",
        )
    try:
        selected = np.asarray(source, dtype=expected_dtype)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueValidationError(
            path,
            f"array values do not fit {value_type.dtype}",
        ) from error
    if selected.ndim != len(value_type.dimensions):
        raise ValueValidationError(
            path,
            f"expected a {len(value_type.dimensions)}D array, got {selected.ndim}D",
        )
    for index, dimension in enumerate(value_type.dimensions):
        if dimension.size is not None and selected.shape[index] != dimension.size:
            raise ValueValidationError(
                path,
                f"dimension {dimension.id!r} must have size {dimension.size}, "
                f"got {selected.shape[index]}",
            )
    return freeze_ndarray(selected)


def _numpy_dtype(dtype: ValueDType) -> np.dtype[np.generic]:
    return np.dtype(
        {
            "float64": np.float64,
            "int64": np.int64,
            "complex128": np.complex128,
            "bool": np.bool_,
            "string": np.str_,
        }[dtype]
    )


def validate_literal(
    value_type: ValueType,
    value: object,
    *,
    path: ValuePath = ("value",),
) -> None:
    """Raise :class:`ValueValidationError` if a literal is incompatible."""

    coerce_literal(value_type, value, path=path)


def _coerce_atom(atom: AtomType, value: object, *, path: ValuePath) -> object:
    if isinstance(atom, Bool):
        if not isinstance(value, bool):
            raise ValueValidationError(path, f"expected bool, got {value!r}")
        return value
    if isinstance(atom, Int):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueValidationError(path, f"expected int, got {value!r}")
        _validate_numeric_value(value, atom.minimum, atom.maximum, path=path)
        return value
    if isinstance(atom, Float):
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueValidationError(path, f"expected float, got {value!r}")
        try:
            numeric_value = float(value)
        except OverflowError as error:
            raise ValueValidationError(path, "expected a finite float") from error
        if atom.finite and not math.isfinite(numeric_value):
            raise ValueValidationError(path, "expected a finite float")
        _validate_numeric_value(
            numeric_value,
            atom.minimum,
            atom.maximum,
            path=path,
        )
        return numeric_value
    if isinstance(atom, Complex):
        if isinstance(value, bool) or not isinstance(value, int | float | complex):
            raise ValueValidationError(path, f"expected complex, got {value!r}")
        try:
            numeric = complex(value)
        except OverflowError as error:
            raise ValueValidationError(path, "expected a finite complex") from error
        if not math.isfinite(numeric.real) or not math.isfinite(numeric.imag):
            raise ValueValidationError(path, "expected a finite complex")
        return numeric
    if isinstance(atom, String):
        return _coerce_string(atom, value, path=path)
    if isinstance(atom, Quantity):
        return _coerce_quantity(atom, value, path=path)
    if isinstance(atom, Entity):
        return _coerce_entity(atom, value, path=path)
    return _coerce_payload(atom, value, path=path)


def _coerce_string(atom: String, value: object, *, path: ValuePath) -> str:
    if not isinstance(value, str):
        raise ValueValidationError(path, f"expected string, got {value!r}")
    if atom.choices is not None and value not in atom.choices:
        raise ValueValidationError(
            path,
            f"string must be one of {', '.join(repr(item) for item in atom.choices)}",
        )
    return value


def _coerce_quantity(
    atom: Quantity,
    value: object,
    *,
    path: ValuePath,
) -> QuantityValue:
    selected: QuantityValue
    if isinstance(value, QuantityValue):
        selected = value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if atom.unit is None:
            raise ValueValidationError(
                path,
                "numeric quantity literal requires a declared unit",
            )
        try:
            numeric_value = float(value)
        except OverflowError as error:
            raise ValueValidationError(
                path,
                "expected a finite quantity",
            ) from error
        selected = QuantityValue(value=numeric_value, unit=atom.unit)
    elif isinstance(value, Mapping):
        try:
            selected = QuantityValue.model_validate(value)
        except ValidationError as error:
            raise ValueValidationError(path, f"invalid quantity: {error}") from error
    else:
        raise ValueValidationError(path, f"expected quantity, got {value!r}")

    expected_dimension = atom.dimension or (
        unit_kind(atom.unit) if atom.unit is not None else None
    )
    if (
        expected_dimension is not None
        and unit_kind(selected.unit) != expected_dimension
    ):
        raise ValueValidationError(
            path,
            "quantity must use dimension "
            f"{expected_dimension!r}, got {selected.unit!r}",
            code="incompatible_unit",
        )
    if atom.unit is not None:
        if not compatible_units(atom.unit, selected.unit):
            raise ValueValidationError(
                path,
                f"quantity must use a unit compatible with {atom.unit!r}",
                code="incompatible_unit",
            )
        if selected.unit != atom.unit:
            try:
                selected = selected.to(atom.unit)
            except ValueError as error:
                raise ValueValidationError(
                    path,
                    str(error),
                    code="incompatible_unit",
                ) from error
    if atom.finite and not math.isfinite(selected.value):
        raise ValueValidationError(path, "expected a finite quantity")
    _validate_numeric_value(
        selected.value,
        atom.minimum,
        atom.maximum,
        path=path,
    )
    return selected


def _coerce_entity(atom: Entity, value: object, *, path: ValuePath) -> EntityRef:
    if isinstance(value, EntityRef):
        try:
            selected = value.model_copy(
                update={"metadata": normalize_entity_metadata(value.metadata)},
                deep=True,
            )
        except ValueError as error:
            raise ValueValidationError(path, f"invalid entity: {error}") from error
    elif isinstance(value, str):
        selected = EntityRef(id=value, kind=atom.entity_kind)
    elif isinstance(value, Mapping):
        try:
            selected = EntityRef.model_validate(value)
        except ValidationError as error:
            raise ValueValidationError(path, f"invalid entity: {error}") from error
    else:
        raise ValueValidationError(path, f"expected entity reference, got {value!r}")
    if not selected.id:
        raise ValueValidationError(path, "entity id must be non-empty")
    if atom.entity_kind is None:
        return selected
    if selected.kind is not None and selected.kind != atom.entity_kind:
        raise ValueValidationError(
            path,
            f"entity must have kind {atom.entity_kind!r}, got {selected.kind!r}",
        )
    if selected.kind is None:
        return selected.model_copy(update={"kind": atom.entity_kind})
    return selected


def _coerce_payload(atom: Payload, value: object, *, path: ValuePath) -> PayloadValue:
    if isinstance(value, PayloadValue):
        if value.schema_id != atom.schema_id:
            raise ValueValidationError(
                path,
                f"expected payload {atom.schema_id!r}, got {value.schema_id!r}",
            )
        selected_payload = value.payload
    else:
        selected_payload = value
    return PayloadValue(schema_id=atom.schema_id, payload=selected_payload)


def _coerce_table(
    value_type: Table,
    value: object,
    *,
    path: ValuePath,
) -> tuple[dict[str, object], ...]:
    rows = _sequence(value, path=path, label="table")
    columns = {column.id: column for column in value_type.columns}
    result: list[dict[str, object]] = []
    primary_key_indexes: dict[tuple[tuple[object, ...], ...], int] = {}
    quantity_primary_keys: list[tuple[int, tuple[object, ...]]] = []
    for index, raw_row in enumerate(rows):
        row_path = (*path, index)
        row = _string_mapping(raw_row, path=row_path, label="table row")
        missing = [column.id for column in value_type.columns if column.id not in row]
        if missing:
            raise ValueValidationError(
                row_path,
                "table row is missing required columns: " + ", ".join(missing),
            )
        extra = sorted(set(row) - set(columns))
        if extra:
            raise ValueValidationError(
                row_path,
                "table row contains unknown columns: " + ", ".join(extra),
            )
        selected = {
            column_id: coerce_literal(
                column.value_type,
                row[column_id],
                path=(*row_path, column_id),
            )
            for column_id, column in columns.items()
        }
        if value_type.primary_key:
            key = tuple(selected[column_id] for column_id in value_type.primary_key)
            if any(isinstance(value, QuantityValue) for value in key):
                duplicate_index = next(
                    (
                        existing_index
                        for existing_index, existing_key in quantity_primary_keys
                        if _scalar_keys_equal(existing_key, key)
                    ),
                    None,
                )
                quantity_primary_keys.append((index, key))
            else:
                identity = tuple(scalar_identity(value) for value in key)
                duplicate_index = primary_key_indexes.get(identity)
                primary_key_indexes.setdefault(identity, index)
            if duplicate_index is not None:
                raise ValueValidationError(
                    row_path,
                    f"table primary key {key!r} duplicates row {duplicate_index}",
                )
        result.append(selected)
    return tuple(result)


def _scalar_keys_equal(
    left: Sequence[object],
    right: Sequence[object],
) -> bool:
    return len(left) == len(right) and all(
        scalar_values_equal(left_value, right_value)
        for left_value, right_value in zip(left, right, strict=True)
    )


def _validate_numeric_value(
    value: float,
    minimum: float | None,
    maximum: float | None,
    *,
    path: ValuePath,
) -> None:
    if minimum is not None and value < minimum:
        raise ValueValidationError(path, f"value must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueValidationError(path, f"value must be at most {maximum}")


def _string_mapping(
    value: object,
    *,
    path: ValuePath,
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueValidationError(path, f"expected {label} mapping, got {value!r}")
    mapping = cast("Mapping[object, object]", value)
    if not all(isinstance(key, str) for key in mapping):
        raise ValueValidationError(path, f"{label} keys must be strings")
    return cast("Mapping[str, object]", mapping)


def _sequence(
    value: object,
    *,
    path: ValuePath,
    label: str,
) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ValueValidationError(path, f"expected {label} sequence, got {value!r}")
    return value
