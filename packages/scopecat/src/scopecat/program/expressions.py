"""Canonical scalar expressions and runtime values shared across the program.

Expressions retain one shape from authoring composition through verification,
binding, specialization, and evaluation. Runtime materialization remains an
explicitly selected compiler concern.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

import numpy as np

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_data import CellValue as _CellValue
from scopecat.kernel.value_data import is_cell_value as _is_cell_value
from scopecat.kernel.value_type_compatibility import is_assignable, literal_scalar_type
from scopecat.kernel.value_types import Array, Scalar
from scopecat.program.expression_operators import (
    ScalarOperator,
    scalar_operator_result_type,
)
from scopecat.program.identities import InvocationKey


@dataclass(frozen=True, slots=True)
class ParameterLookupUse:
    """One selected typed lookup occurrence on a table parameter."""

    table_id: str
    key_input_types: tuple[tuple[str, Scalar], ...]
    literal_key_columns: frozenset[str]
    column_id: str
    result_type: Scalar

    def __post_init__(self) -> None:
        if not self.table_id or not self.column_id:
            msg = "parameter lookup table and result column ids must be non-empty"
            raise ValueError(msg)
        key_input_types = tuple(sorted(self.key_input_types, key=lambda item: item[0]))
        key_ids = tuple(key for key, _value_type in key_input_types)
        if any(not key for key in key_ids) or len(key_ids) != len(set(key_ids)):
            msg = "parameter lookup key column ids must be non-empty and unique"
            raise ValueError(msg)
        literal_key_columns = frozenset(self.literal_key_columns)
        if not literal_key_columns <= set(key_ids):
            msg = "literal parameter lookup keys must belong to the lookup key"
            raise ValueError(msg)
        object.__setattr__(self, "key_input_types", key_input_types)
        object.__setattr__(self, "literal_key_columns", literal_key_columns)


class ScalarExpr:
    """Common base for scalar expression nodes."""

    value_type: Scalar = cast("Scalar", NotImplemented)

    def _binary(self, op: ScalarOperator, other: object) -> BinaryScalarExpr:
        return BinaryScalarExpr(
            op=op,
            left=self,
            right=as_scalar_expr(other),
        )

    def __add__(self, other: object) -> BinaryScalarExpr:
        return self._binary("+", other)

    def __radd__(self, other: object) -> BinaryScalarExpr:
        return BinaryScalarExpr(
            op="+",
            left=as_scalar_expr(other),
            right=self,
        )

    def __sub__(self, other: object) -> BinaryScalarExpr:
        return self._binary("-", other)

    def __rsub__(self, other: object) -> BinaryScalarExpr:
        return BinaryScalarExpr(
            op="-",
            left=as_scalar_expr(other),
            right=self,
        )

    def __mul__(self, other: object) -> BinaryScalarExpr:
        return self._binary("*", other)

    def __rmul__(self, other: object) -> BinaryScalarExpr:
        return BinaryScalarExpr(
            op="*",
            left=as_scalar_expr(other),
            right=self,
        )

    def __truediv__(self, other: object) -> BinaryScalarExpr:
        return self._binary("/", other)

    def __rtruediv__(self, other: object) -> BinaryScalarExpr:
        return BinaryScalarExpr(
            op="/",
            left=as_scalar_expr(other),
            right=self,
        )


@dataclass(frozen=True, slots=True, init=False)
class LiteralScalarExpr(ScalarExpr):
    """One typed literal captured by value at the program boundary."""

    value_type: Scalar = field()
    _value: _CellValue = field(hash=False, repr=False)

    def __init__(
        self,
        value: _CellValue,
        value_type: Scalar | None = None,
    ) -> None:
        selected_type = value_type or literal_scalar_type(value)
        object.__setattr__(self, "value_type", selected_type)
        object.__setattr__(
            self,
            "_value",
            cast("_CellValue", _snapshot_literal(value)),
        )

    @property
    def value(self) -> _CellValue:
        """Return a defensive snapshot of the retained literal."""

        return cast("_CellValue", _snapshot_literal(self._value))


@dataclass(frozen=True, slots=True)
class PointColumnScalarExpr(ScalarExpr):
    name: str
    value_type: Scalar = field()


@dataclass(frozen=True, slots=True)
class InputScalarExpr(ScalarExpr):
    name: str
    value_type: Scalar = field()


@dataclass(frozen=True, slots=True)
class ParameterScalarExpr(ScalarExpr):
    name: str
    value_type: Scalar = field()


@dataclass(frozen=True, slots=True)
class ComputeResultScalarExpr(ScalarExpr):
    """One point-local compute result retained as an opaque scalar edge."""

    value_id: ValueId
    value_type: Scalar = field()
    origin: tuple[object, ...] = ()
    point_dependencies: tuple[tuple[str, Scalar], ...] = ()


class ArrayExpr:
    """Common base for dense point-local array expression nodes."""

    value_type: Array = cast("Array", NotImplemented)


@dataclass(frozen=True, slots=True)
class LiteralArrayExpr(ArrayExpr):
    """One normalized typed array captured by value."""

    value_type: Array = field()
    _value: np.ndarray = field(hash=False, repr=False, compare=False)

    def __init__(self, value: np.ndarray, value_type: Array) -> None:
        snapshot = value.copy(order="C")
        snapshot.flags.writeable = False
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "_value", snapshot)

    @property
    def value(self) -> np.ndarray:
        return self._value


@dataclass(frozen=True, slots=True)
class InputArrayExpr(ArrayExpr):
    name: str
    value_type: Array = field()


@dataclass(frozen=True, slots=True)
class ComputeResultArrayExpr(ArrayExpr):
    """One point-local compute result retained as an opaque array edge."""

    value_id: ValueId
    value_type: Array = field()
    origin: tuple[object, ...] = ()
    point_dependencies: tuple[tuple[str, Scalar], ...] = ()


@dataclass(frozen=True, slots=True)
class ModuleExportScalarExpr(ScalarExpr):
    """One unresolved scalar projection from a module invocation."""

    invocation_key: InvocationKey
    export_id: str
    value_type: Scalar = field()
    source_value_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleExportArrayExpr(ArrayExpr):
    """One unresolved array projection from a module invocation."""

    invocation_key: InvocationKey
    export_id: str
    value_type: Array = field()
    source_value_id: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterLookupScalarExpr(ScalarExpr):
    use: ParameterLookupUse
    key: Mapping[str, ScalarExpr] = field(hash=False)
    value_type: Scalar = field(init=False)

    def __post_init__(self) -> None:
        key = FrozenMapping(self.key.items())
        expected = dict(self.use.key_input_types)
        if set(key) != set(expected):
            msg = "parameter lookup key expressions must exactly match its typed inputs"
            raise ValueError(msg)
        for name, expression in key.items():
            if not is_assignable(expression.value_type, expected[name]):
                msg = f"parameter lookup key {name!r} has an incompatible value type"
                raise TypeError(msg)
            _require_plan_expression(expression)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "value_type", self.use.result_type)


@dataclass(frozen=True, slots=True)
class BinaryScalarExpr(ScalarExpr):
    op: ScalarOperator
    left: ScalarExpr
    right: ScalarExpr
    value_type: Scalar = field(init=False)

    def __post_init__(self) -> None:
        _require_plan_expression(self.left)
        _require_plan_expression(self.right)
        object.__setattr__(
            self,
            "value_type",
            scalar_operator_result_type(
                self.left.value_type,
                self.right.value_type,
                self.op,
            ),
        )


def lit(
    value: _CellValue,
    value_type: Scalar | None = None,
) -> LiteralScalarExpr:
    return LiteralScalarExpr(value, value_type)


def point_col(name: str, value_type: Scalar) -> PointColumnScalarExpr:
    """Reference a field from the current experiment point."""

    return PointColumnScalarExpr(name=name, value_type=value_type)


def input_ref(name: str, value_type: Scalar) -> InputScalarExpr:
    return InputScalarExpr(name=name, value_type=value_type)


def param(parameter_id: str, value_type: Scalar) -> ParameterScalarExpr:
    return ParameterScalarExpr(name=parameter_id, value_type=value_type)


def parameter_lookup(
    use: ParameterLookupUse,
    *,
    key: Mapping[str, object],
) -> ParameterLookupScalarExpr:
    key_ids = set(key)
    expected_key_ids = {name for name, _value_type in use.key_input_types}
    if key_ids != expected_key_ids:
        msg = "parameter lookup key expressions must exactly match its typed key inputs"
        raise ValueError(msg)
    key_types = dict(use.key_input_types)
    return ParameterLookupScalarExpr(
        use=use,
        key={
            name: as_scalar_expr(value, value_type=key_types[name])
            for name, value in key.items()
        },
    )


def as_scalar_expr(
    value: object,
    *,
    value_type: Scalar | None = None,
) -> ScalarExpr:
    if isinstance(value, ScalarExpr):
        return value
    if _is_cell_value(value):
        return lit(value, value_type)
    msg = f"cannot convert {value!r} to scalar expression"
    raise TypeError(msg)


def _require_plan_expression(expression: ScalarExpr) -> None:
    """Reject compute results nested inside a pure scalar expression."""

    pending = [expression]
    while pending:
        selected = pending.pop()
        if isinstance(selected, ComputeResultScalarExpr):
            msg = (
                "compute outputs cannot be bound inside scalar expressions; "
                "express this calculation with ModuleContext.compute"
            )
            raise TypeError(msg)
        if isinstance(selected, ParameterLookupScalarExpr):
            pending.extend(selected.key.values())
        elif isinstance(selected, BinaryScalarExpr):
            pending.extend((selected.left, selected.right))


def _snapshot_literal(value: object) -> object:
    """Copy literal containers without cloning opaque payload bodies."""

    if isinstance(value, PayloadValue):
        return value
    if isinstance(value, EntityRef | Quantity):
        return value.model_copy(deep=True)
    if value is None or isinstance(value, str | int | float | complex | bool):
        return value
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {
            key: _snapshot_literal(nested_value)
            for key, nested_value in mapping.items()
        }
    if isinstance(value, list):
        items = cast("list[object]", value)
        return [_snapshot_literal(item) for item in items]
    if isinstance(value, tuple):
        items = cast("tuple[object, ...]", value)
        return tuple(_snapshot_literal(item) for item in items)
    return value
