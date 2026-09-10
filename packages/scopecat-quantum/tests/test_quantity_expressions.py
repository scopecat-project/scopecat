"""Recipes use the same helpers for concrete quantities and scanned inputs."""

from __future__ import annotations

from typing import Annotated, assert_type, cast

import pytest
import scopecat as sc

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import PulseProgramId
from scopecat_quantum.programs import (
    materialize_quantum_pulse_program,
    plan_quantum_pulse_lowering,
)
from scopecat_quantum.pulse_implementations import ResolvedPulseImplementations
from scopecat_quantum.pulses import Play, ShiftPhase, schedule


def calibrated_pulse(
    qubit: q.Qubit,
    duration: q.QuantumQuantity,
    amplitude: q.QuantumQuantity,
    phase: q.QuantumQuantity,
) -> q.QuantumFragment:
    return q.sequence(
        q.shift_phase(q.drive(qubit), phase / 2 + sc.Quantity(90, "deg")),
        q.play(
            q.drive(qubit),
            q.gaussian(
                duration=duration + sc.Quantity(0.004, "us"),
                sigma=duration / 4,
                amplitude=0.9 * amplitude,
                phase=-phase,
            ),
        ),
    )


@q.pulse_template(id="recipe.nested")
def nested(
    qubit: q.Qubit,
    duration: Annotated[q.QuantumQuantity, sc.QuantityType(unit="ns")],
    amplitude: Annotated[q.QuantumQuantity, sc.QuantityType(unit="arb")],
    phase: Annotated[q.QuantumQuantity, sc.QuantityType(unit="rad")],
) -> q.QuantumFragment:
    return calibrated_pulse(qubit, duration, amplitude, phase)


@q.program(id="recipe.scan")
def scanned(
    qubit: q.Qubit,
    duration: Annotated[q.QuantumQuantity, sc.QuantityType(unit="ns")],
    amplitude: Annotated[q.QuantumQuantity, sc.QuantityType(unit="arb")],
    phase: Annotated[q.QuantumQuantity, sc.QuantityType(unit="rad")],
) -> q.QuantumFragment:
    return nested(qubit, duration, amplitude, phase + sc.Quantity(0, "rad"))


@pytest.mark.parametrize("duration, phase", [(16, 0), (24, 90)])
def test_nested_recipe_matches_concrete_at_each_scan_point(
    duration: int, phase: int
) -> None:
    values = {
        "duration": sc.Quantity(duration, "ns"),
        "amplitude": sc.Quantity(0.2, "arb"),
        "phase": sc.Quantity(phase, "deg"),
    }
    actual = q.bind(scanned, {"qubit": "q0", **values})
    concrete = q.bind(
        q._close_program(
            "recipe.scan",
            nested(q.qubit("q0"), **values),
        )
    )

    def scheduled(bound: q.BoundProgram):
        return schedule(
            materialize_quantum_pulse_program(
                plan_quantum_pulse_lowering(
                    bound.verified,
                    ResolvedPulseImplementations(),
                    output_id=PulseProgramId("recipe.preview"),
                )
            )
        )

    symbolic_preview, concrete_preview = scheduled(actual), scheduled(concrete)
    assert symbolic_preview == concrete_preview
    [frame, event] = symbolic_preview.events
    assert isinstance(frame.instruction, ShiftPhase)
    assert frame.instruction.phase.to("deg").value == pytest.approx(phase / 2 + 90)
    assert isinstance(event.instruction, Play)
    assert event.instruction.envelope.amplitude.value == pytest.approx(0.18)
    assert event.instruction.envelope.duration.to("ns").value == duration + 4
    assert event.instruction.envelope.phase.to("deg").value == pytest.approx(-phase)
    assert "(($phase + 0 rad) * 0.5)" in scanned.draw()
    assert tuple(port.id for port in scanned.inputs) == (
        "duration",
        "amplitude",
        "phase",
    )


def test_unit_arithmetic_reports_the_author_expression() -> None:
    duration = q.input("duration", sc.ScalarType(sc.QuantityType(unit="ns")))
    with pytest.raises(TypeError, match=r"incompatible units.*duration"):
        _ = duration + sc.Quantity(1, "rad")
    with pytest.raises(TypeError, match="requires a finite numeric scalar"):
        _ = duration * cast("float", cast("object", sc.Quantity(1, "ns")))
    with pytest.raises(TypeError, match="requires a quantity offset"):
        _ = duration + cast("q.QuantumQuantity", cast("object", 2))
    with pytest.raises(ZeroDivisionError, match="duration"):
        _ = duration / 0
    number = q.input("number", sc.ScalarType(sc.FloatType()))
    with pytest.raises(TypeError, match="quantity-valued input"):
        _ = number * 2


def test_compatible_inputs_offsets_and_reflected_subtraction() -> None:
    left = q.input("left", sc.ScalarType(sc.QuantityType(unit="ns")))
    right = q.input("right", sc.ScalarType(sc.QuantityType(unit="us")))
    expression = sc.Quantity(100, "ns") - (left + right)
    declaration = q._close_program(
        "recipe.offsets", q.delay(q.drive(q.qubit("q0")), expression)
    )
    bound = q.bind(
        declaration, {"left": sc.Quantity(20, "ns"), "right": sc.Quantity(0.03, "us")}
    )
    lowered = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("offsets"),
    )
    assert float(
        schedule(materialize_quantum_pulse_program(lowered)).duration_seconds
    ) == pytest.approx(50e-9)


def test_symbolic_arithmetic_has_honest_static_result_types() -> None:
    duration = q.input("duration", sc.ScalarType(sc.QuantityType(unit="ns")))
    assert_type(duration * 2, q.QuantityExpression)
    assert_type(duration + sc.Quantity(4, "ns"), q.QuantityExpression)
    assert_type(sc.Quantity(4, "ns") + duration, q.QuantityExpression)
    assert_type(sc.Quantity(4, "ns") - duration, q.QuantityExpression)
    assert_type(sc.Quantity(4, "ns") + sc.Quantity(1, "ns"), sc.Quantity)
    assert_type(-sc.Quantity(4, "ns"), sc.Quantity)


def test_expression_into_bounded_template_checks_after_point_binding() -> None:
    @q.pulse_template(id="bounded-duration")
    def bounded(
        qubit: q.Qubit,
        duration: Annotated[
            q.QuantumQuantity, sc.QuantityType(unit="ns", minimum=8, maximum=24)
        ],
    ) -> q.QuantumFragment:
        return q.delay(q.drive(qubit), duration / 2)

    duration = q.input("duration", sc.ScalarType(sc.QuantityType(unit="us")))
    declaration = q._close_program(
        "bounded-helper", bounded(q.qubit("q0"), duration + sc.Quantity(0.004, "us"))
    )
    q.bind(declaration, {"duration": sc.Quantity(0.012, "us")})
    # The template boundary checks 32 ns, even though its body later halves it.
    with pytest.raises(ValueError, match=r"quantity expression.*24"):
        q.bind(declaration, {"duration": sc.Quantity(0.028, "us")})
