"""Shared identity and comparison primitives for closed scalar values.

This module deliberately sits below parameter validation, literal validation,
and relation evaluation.  Those layers must agree on entity identity and
quantity unit normalization without importing one another.
"""

from __future__ import annotations

import math

from scopecat.kernel.entity import (
    EntityRef,
    entity_identity,
)
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.units import compatible_units, to_base_value, unit_kind

type ScalarIdentity = tuple[object, ...]


def scalar_identity(value: object) -> ScalarIdentity:
    """Return the semantic identity of a primary-key-compatible scalar."""

    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int | float):
        normalized = 0.0 if value == 0 else value
        return ("number", normalized)
    if isinstance(value, complex):
        return (
            "complex",
            0.0 if value.real == 0 else value.real,
            0.0 if value.imag == 0 else value.imag,
        )
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, Quantity):
        base_value = to_base_value(value.value, value.unit)
        if base_value is not None:
            normalized = 0.0 if base_value == 0.0 else base_value
            return ("quantity", unit_kind(value.unit), normalized)
        normalized = 0.0 if value.value == 0.0 else value.value
        return ("quantity", unit_kind(value.unit), value.unit, normalized)
    if isinstance(value, EntityRef):
        return ("entity", *entity_identity(value))
    if value is None:
        return ("null",)
    msg = f"value {value!r} cannot be used as a scalar identity"
    raise TypeError(msg)


def quantity_comparison_values(
    left: Quantity,
    right: Quantity,
) -> tuple[float, float]:
    """Normalize compatible quantities symmetrically for equality and order.

    Linear units are compared in their common dimension base unit.  A
    non-linear unit can only be compared with the exact same unit.  In
    particular, this avoids the precision loss and direction dependence of
    converting the right operand into the left operand's display unit.
    """

    if not compatible_units(left.unit, right.unit):
        msg = f"cannot compare quantity units {left.unit!r} and {right.unit!r}"
        raise ValueError(msg)
    left_base = to_base_value(left.value, left.unit)
    right_base = to_base_value(right.value, right.unit)
    if left_base is not None and right_base is not None:
        return left_base, right_base
    if left.unit == right.unit:
        return left.value, right.value
    msg = f"cannot compare quantity units {left.unit!r} and {right.unit!r}"
    raise ValueError(msg)


def scalar_values_equal(left: object, right: object) -> bool:
    """Compare closed scalar values with unit-conversion noise removed.

    Quantity tolerance here is deliberately limited to floating-point
    representation error. Instrument-specific resolution remains a separate
    contract rather than becoming part of scalar identity.
    """

    if isinstance(left, Quantity) or isinstance(right, Quantity):
        if not isinstance(left, Quantity) or not isinstance(right, Quantity):
            return False
        left_value, right_value = quantity_comparison_values(left, right)
        return math.isclose(
            left_value,
            right_value,
            rel_tol=1.0e-12,
            abs_tol=0.0,
        )
    return scalar_identity(left) == scalar_identity(right)
