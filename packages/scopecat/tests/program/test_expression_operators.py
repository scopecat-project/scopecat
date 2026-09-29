"""Shared authored and runtime expression operator semantics."""

from __future__ import annotations

import math
from typing import assert_type, cast

import pytest
from scopecat_testkit.expressions import evaluate_scalar

import scopecat as sc
from scopecat.compiler.relations.context import EvalContext
from scopecat.compiler.relations.verification import ExpressionTypeBindings
from scopecat.program.expression_operators import runtime_values_equal
from scopecat.program.value_refs import internal_lower_scalar_value_ref
from scopecat.program.values import input as program_input


def _input_bindings(**inputs: sc.ValueType) -> ExpressionTypeBindings:
    return ExpressionTypeBindings(inputs=cast("dict[str, sc.ScalarType]", inputs))


def test_typed_arithmetic_and_runtime_use_the_same_operator_contract() -> None:
    text = program_input("text", sc.ScalarType(sc.StringType()))
    count = program_input("count", sc.ScalarType(sc.IntType()))

    with pytest.raises(TypeError, match=r"operator '\+' is not defined"):
        _ = text + "suffix"

    numeric = count + 0.5
    assert numeric.value_type == sc.ScalarType(sc.FloatType())
    assert (
        evaluate_scalar(
            internal_lower_scalar_value_ref(numeric),
            EvalContext(inputs={"count": 2}),
            bindings=_input_bindings(count=count.value_type),
        )
        == 2.5
    )


def test_arithmetic_supports_literal_operands_on_either_side() -> None:
    count = program_input("count", sc.ScalarType(sc.IntType(minimum=1)))
    expressions = (
        count + 2,
        2 + count,
        count - 2,
        5 - count,
        count * 2,
        2 * count,
        count / 2,
        6 / count,
    )

    assert [
        evaluate_scalar(
            internal_lower_scalar_value_ref(expression),
            EvalContext(inputs={"count": 2}),
            bindings=_input_bindings(count=count.value_type),
        )
        for expression in expressions
    ] == [4, 4, 0, 3, 4, 4, 1.0, 3.0]


def test_quantity_arithmetic_reduces_inverse_units_to_plain_numbers() -> None:
    frequency_type = sc.ScalarType(sc.QuantityType(dimension="frequency"))
    delay_type = sc.ScalarType(sc.QuantityType(dimension="time"))
    frequency = program_input("frequency", frequency_type)
    delay = program_input("delay", delay_type)
    cycles = frequency * delay
    phase = cycles * sc.Quantity(math.tau, "rad")

    assert cycles.value_type == sc.ScalarType(sc.FloatType())
    assert phase.value_type == sc.ScalarType(sc.QuantityType(unit="rad"))
    assert evaluate_scalar(
        internal_lower_scalar_value_ref(cycles),
        EvalContext(
            inputs={
                "frequency": sc.Quantity(5.0, "GHz"),
                "delay": sc.Quantity(20.0, "ns"),
            }
        ),
        bindings=_input_bindings(
            frequency=frequency_type,
            delay=delay_type,
        ),
    ) == pytest.approx(100.0)
    assert evaluate_scalar(
        internal_lower_scalar_value_ref(phase),
        EvalContext(
            inputs={
                "frequency": sc.Quantity(5.0, "GHz"),
                "delay": sc.Quantity(20.0, "ns"),
            }
        ),
        bindings=_input_bindings(
            frequency=frequency_type,
            delay=delay_type,
        ),
    ) == sc.Quantity(100.0 * math.tau, "rad")


def test_quantity_arithmetic_normalizes_linear_ratios() -> None:
    frequency_type = sc.ScalarType(sc.QuantityType(dimension="frequency"))
    measured = program_input("measured", frequency_type)
    reference = program_input("reference", frequency_type)
    ratio = measured / reference

    assert ratio.value_type == sc.ScalarType(sc.FloatType())
    assert (
        evaluate_scalar(
            internal_lower_scalar_value_ref(ratio),
            EvalContext(
                inputs={
                    "measured": sc.Quantity(5_000.0, "MHz"),
                    "reference": sc.Quantity(5.0, "GHz"),
                }
            ),
            bindings=_input_bindings(
                measured=frequency_type,
                reference=frequency_type,
            ),
        )
        == 1.0
    )


def test_quantity_arithmetic_exposes_dimension_reducing_static_types() -> None:
    frequency = sc.coordinate("frequency", sc.QuantityType(unit="GHz"))
    delay = sc.coordinate("delay", sc.QuantityType(unit="ns"))
    scale = sc.coordinate("scale", sc.FloatType())
    phase = frequency * delay * sc.Quantity(math.tau, "rad")

    assert_type(frequency * delay, sc.ValueRef[float])
    assert_type(phase, sc.ValueRef[sc.Quantity])
    assert_type(frequency / sc.Quantity(1.0, "GHz"), sc.ValueRef[float])
    assert_type(frequency * 2.0, sc.ValueRef[sc.Quantity])
    assert_type(sc.Quantity(6.0, "GHz") - frequency, sc.ValueRef[sc.Quantity])
    assert_type(frequency * scale, sc.ValueRef[sc.Quantity])
    assert_type(frequency / scale, sc.ValueRef[sc.Quantity])


def test_quantity_arithmetic_rejects_non_reducing_or_nonlinear_units() -> None:
    voltage = program_input(
        "voltage",
        sc.ScalarType(sc.QuantityType(dimension="voltage")),
    )
    duration = program_input(
        "duration",
        sc.ScalarType(sc.QuantityType(dimension="time")),
    )
    logarithmic_power = program_input(
        "power",
        sc.ScalarType(sc.QuantityType(unit="dBm")),
    )
    linear_power = program_input(
        "linear_power",
        sc.ScalarType(sc.QuantityType(unit="W")),
    )
    generic_power = program_input(
        "generic_power",
        sc.ScalarType(sc.QuantityType(dimension="power")),
    )

    assert (linear_power / linear_power).value_type == sc.ScalarType(sc.FloatType())
    with pytest.raises(TypeError, match="must cancel to a dimensionless value"):
        _ = voltage * duration
    with pytest.raises(TypeError, match="shared linearly scaled dimension"):
        _ = logarithmic_power / logarithmic_power
    with pytest.raises(TypeError, match="shared linearly scaled dimension"):
        _ = generic_power / generic_power


def test_integer_arithmetic_preserves_affine_bounds() -> None:
    count = sc.coordinate(
        "count",
        sc.ScalarType(sc.IntType(minimum=0, maximum=4)),
    )
    unbounded_count = sc.coordinate(
        "unbounded_count",
        sc.ScalarType(sc.IntType(minimum=0)),
    )

    assert (2 * count + 1).value_type == sc.ScalarType(sc.IntType(minimum=1, maximum=9))
    assert (3 - count).value_type == sc.ScalarType(sc.IntType(minimum=-1, maximum=3))
    assert (2 * unbounded_count + 1).value_type == sc.ScalarType(sc.IntType(minimum=1))


def test_typed_arithmetic_rejects_non_finite_runtime_results() -> None:
    value = program_input("value", sc.ScalarType(sc.FloatType()))
    overflow = internal_lower_scalar_value_ref(value * 1e308)

    with pytest.raises(ValueError, match="non-finite result"):
        evaluate_scalar(
            overflow,
            EvalContext(inputs={"value": 1e308}),
            bindings=_input_bindings(value=value.value_type),
        )


def test_runtime_key_equality_normalizes_quantity_units_symmetrically() -> None:
    tiny_ghz = sc.Quantity(1e-13, "GHz")
    tiny_hz = sc.Quantity(1e-4, "Hz")

    assert runtime_values_equal(tiny_ghz, tiny_hz)
    assert runtime_values_equal(tiny_hz, tiny_ghz)
    assert runtime_values_equal(
        sc.Quantity(100.0, "uA"),
        sc.Quantity(0.0001, "A"),
    )


def test_runtime_key_equality_uses_stable_entity_identity() -> None:
    left = sc.EntityRef(
        id="q0",
        kind="qubit",
        metadata={"source": "left"},
    )
    right = sc.EntityRef(
        id="q0",
        kind="qubit",
        metadata={"source": "right"},
    )
    assert runtime_values_equal(left, right)
    assert not runtime_values_equal(
        left,
        sc.EntityRef(id="q0", kind="resonator"),
    )
    with pytest.raises(TypeError, match="matching"):
        runtime_values_equal(True, 1)
