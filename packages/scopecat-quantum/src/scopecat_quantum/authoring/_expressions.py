"""Bounded quantity arithmetic for Python-authored pulse recipes.

Expressions are authoring values only: binding evaluates them before existing
quantum IR and target inspection receive concrete quantities.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast, override

from scopecat import Quantity
from scopecat.kernel.units import compatible_units, unit_kind
from scopecat.program.value_types import Quantity as QuantityType
from scopecat.program.value_types import Scalar, coerce_literal


class _QuantityArithmetic:
    __slots__ = ()

    def __mul__(self, scalar: float) -> QuantityExpression:
        return _scale(cast("QuantumQuantity", self), scalar)

    def __rmul__(self, scalar: float) -> QuantityExpression:
        return self * scalar

    def __truediv__(self, scalar: float) -> QuantityExpression:
        _require_scalar(scalar, f"{self!r} / {scalar!r}")
        if scalar == 0:
            raise ZeroDivisionError(
                f"cannot divide quantity expression {self!r} by zero"
            )
        return _scale(cast("QuantumQuantity", self), 1 / scalar)

    def __add__(self, other: QuantumQuantity) -> QuantityExpression:
        return _combine(cast("QuantumQuantity", self), other, "+")

    def __radd__(self, other: Quantity) -> QuantityExpression:
        return _combine(other, cast("QuantumQuantity", self), "+")

    def __sub__(self, other: QuantumQuantity) -> QuantityExpression:
        return _combine(cast("QuantumQuantity", self), other, "-")

    def __rsub__(self, other: Quantity) -> QuantityExpression:
        return _combine(other, cast("QuantumQuantity", self), "-")

    def __neg__(self) -> QuantityExpression:
        return self * -1


@dataclass(frozen=True, slots=True)
class ProgramInput(_QuantityArithmetic):
    """One core-typed scalar input shared by circuit and pulse authoring."""

    _id: str
    value_type: Scalar

    @property
    def id(self) -> str:
        """Return the stable input-port identity."""

        return self._id


@dataclass(frozen=True, slots=True)
class QuantityExpression(_QuantityArithmetic):
    """A quantity expression; never a concrete float or a new runtime port.

    Supported operations are scalar multiplication/division, unary negation,
    and addition/subtraction of compatible quantities. Quantity products,
    powers, arbitrary functions and scalar offsets are deliberately excluded.
    """

    left: QuantumQuantity
    operator: Literal["*", "+", "-"]
    right: QuantumQuantity | float
    binding_type: Scalar | None = None

    @property
    def value_type(self) -> Scalar:
        """Describe result units without inheriting input-only bounds."""
        return self.binding_type or Scalar(_quantity_type(self.left))

    @property
    def id(self) -> str:
        """Describe this expression in author errors, not as a port identity."""
        return repr(self)

    @override
    def __repr__(self) -> str:
        return f"({self.left!r} {self.operator} {self.right!r})"


type QuantumQuantity = Quantity | ProgramInput | QuantityExpression


def _quantity_type(value: QuantumQuantity) -> QuantityType:
    if isinstance(value, Quantity):
        return QuantityType(unit=value.unit)
    atom = value.value_type.atom
    if not isinstance(atom, QuantityType):
        raise TypeError(
            f"quantity expression {value!r} requires a quantity-valued input"
        )
    return QuantityType(unit=atom.unit, dimension=atom.dimension)


def _require_scalar(value: object, expression: str) -> None:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise TypeError(
            f"quantity expression {expression} requires a finite numeric scalar"
        )


def _scale(value: QuantumQuantity, scalar: float) -> QuantityExpression:
    _quantity_type(value)
    _require_scalar(scalar, f"{value!r} * {scalar!r}")
    return QuantityExpression(value, "*", scalar)


def _combine(
    left: QuantumQuantity, right: object, operator: Literal["+", "-"]
) -> QuantityExpression:
    if not isinstance(right, Quantity | ProgramInput | QuantityExpression):
        raise TypeError(
            f"quantity expression {left!r} {operator} {right!r} "
            "requires a quantity offset"
        )
    lhs, rhs = _quantity_type(left), _quantity_type(right)
    left_dimension = unit_kind(lhs.unit) if lhs.unit is not None else lhs.dimension
    right_dimension = unit_kind(rhs.unit) if rhs.unit is not None else rhs.dimension
    if (
        left_dimension is None
        or left_dimension != right_dimension
        or (
            lhs.unit is not None
            and rhs.unit is not None
            and not compatible_units(lhs.unit, rhs.unit)
        )
    ):
        raise TypeError(
            f"incompatible units in quantity expression {left!r} {operator} {right!r}"
        )
    return QuantityExpression(left, operator, right)


def expression_inputs(value: object) -> tuple[ProgramInput, ...]:
    if isinstance(value, ProgramInput):
        return (value,)
    if isinstance(value, QuantityExpression):
        return (*expression_inputs(value.left), *expression_inputs(value.right))
    return ()


def resolve_expression(value: object, bindings: Mapping[str, object]) -> object:
    """Resolve both ordinary input handles and bounded expression trees."""
    if isinstance(value, ProgramInput):
        return bindings[value.id]
    if not isinstance(value, QuantityExpression):
        return value
    left = resolve_expression(value.left, bindings)
    right = resolve_expression(value.right, bindings)
    if not isinstance(left, Quantity):
        raise AssertionError("quantity expression operand must bind to Quantity")
    try:
        if value.operator == "*":
            result = left * cast("float", right)
        else:
            if not isinstance(right, Quantity):
                raise AssertionError("quantity expression offset must bind to Quantity")
            result = left + right if value.operator == "+" else left - right
        if value.binding_type is not None:
            return coerce_literal(value.binding_type, result)
        return result
    except ValueError as error:
        raise ValueError(
            f"cannot evaluate quantity expression {value!r}: {error}"
        ) from error


def substitute_expression(
    value: object, bindings: Mapping[ProgramInput, object]
) -> object:
    """Keep nested template calls symbolic until all their inputs are bound."""
    if isinstance(value, ProgramInput):
        return bindings[value]
    if not isinstance(value, QuantityExpression):
        return value
    left = cast("QuantumQuantity", substitute_expression(value.left, bindings))
    right = cast(
        "QuantumQuantity | float", substitute_expression(value.right, bindings)
    )
    expression = QuantityExpression(left, value.operator, right, value.binding_type)
    if not expression_inputs(expression):
        return resolve_expression(expression, {})
    return expression


def constrain_expression(
    value: QuantityExpression, expected: Scalar, *, port: str
) -> QuantityExpression:
    """Apply a template's units and bounds when its argument becomes concrete."""
    atom = expected.atom
    if not isinstance(atom, QuantityType):
        raise TypeError(f"pulse template input {port!r} requires {expected!r}")
    actual = _quantity_type(value)
    wanted_dimension = unit_kind(atom.unit) if atom.unit else atom.dimension
    actual_dimension = unit_kind(actual.unit) if actual.unit else actual.dimension
    if wanted_dimension is not None and wanted_dimension != actual_dimension:
        raise TypeError(
            f"pulse template input {port!r} has incompatible expression {value!r}"
        )
    if (
        atom.unit is not None
        and actual.unit is not None
        and not compatible_units(atom.unit, actual.unit)
    ):
        raise TypeError(
            f"pulse template input {port!r} has incompatible expression {value!r}"
        )
    # Keep a boundary even when later helper arithmetic changes the result.
    return QuantityExpression(value, "*", 1, binding_type=expected)
