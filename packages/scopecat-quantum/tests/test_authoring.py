from __future__ import annotations

from typing import Annotated

import pytest
import scopecat as sc
from scopecat import Quantity

from scopecat_quantum import authoring
from scopecat_quantum._ids import AcquisitionSlotId, QubitId
from scopecat_quantum.acquisitions import AcquisitionKind
from scopecat_quantum.gates import GateCall, GateParameterKind
from scopecat_quantum.programs import Repeat, Sequence


def _x_count_declaration() -> tuple[
    authoring.Program,
    authoring.ProgramInput,
    authoring.MeasurementResult,
]:
    q0 = authoring.qubit("q0")
    x_count = authoring.scalar_input("x_count", GateParameterKind.INTEGER)
    x = authoring.single_qubit_gate("x")
    readout = authoring.measure(q0, result="raw_iq")
    declaration = authoring._close_program(
        "x-count",
        authoring.sequence(
            authoring.repeat(x(q0), x_count),
            readout,
        ),
    )
    return declaration, x_count, readout.result


def test_symbolic_repeat_and_measurement_declare_typed_ports() -> None:
    declaration, x_count, raw_iq = _x_count_declaration()
    bound = authoring.bind(declaration, {"x_count": 3})

    assert declaration.id == "x-count"
    assert declaration.inputs == (x_count,)
    assert x_count.id == "x_count"
    assert x_count.value_type == sc.ScalarType(sc.IntType())
    assert tuple(declaration.results) == (raw_iq,)
    assert raw_iq.id == "raw_iq"
    assert raw_iq.owner.id == "q0"
    assert raw_iq.acquisition_kind is AcquisitionKind.INTEGRATED_IQ
    assert raw_iq.acquisition_slot_id == AcquisitionSlotId("raw_iq")
    assert isinstance(bound.verified.program.body, Sequence)
    repeated = bound.verified.program.body.operations[0]
    assert isinstance(repeated, Repeat)
    assert repeated.count == 3


def test_two_qubit_gate_declares_ordered_unique_operands() -> None:
    q0 = authoring.qubit("q0")
    q1 = authoring.qubit("q1")
    cz = authoring.gate("cz", arity=2)

    bound = authoring.bind(authoring._close_program("cz", cz(q0, q1)))
    [call] = bound.verified.operations

    assert isinstance(cz, authoring.TwoQubitGate)
    assert isinstance(call, GateCall)
    assert call.qubits == (QubitId("q0"), QubitId("q1"))
    with pytest.raises(ValueError, match="operands must be unique"):
        cz(q0, q0)


def test_literal_zero_repeat_elides_dead_inputs_and_gate_definitions() -> None:
    q0 = authoring.qubit("q0")
    amplitude = authoring.scalar_input("amplitude", GateParameterKind.NUMBER)
    drive = authoring.single_qubit_gate(
        "drive",
        parameters={"amplitude": GateParameterKind.NUMBER},
    )
    declaration = authoring._close_program(
        "dead-drive",
        authoring.repeat(drive(q0, amplitude=amplitude), 0),
    )

    bound = authoring.bind(declaration)

    assert declaration.inputs == ()
    assert bound.gate_definitions == ()
    assert bound.verified.operations == ()


def test_literal_zero_pulse_repeat_elides_inputs_but_retains_signal_owners() -> None:
    amplitude = authoring.input(
        "amplitude",
        sc.ScalarType(sc.QuantityType(unit="arb")),
    )

    @authoring.pulse_template(id="dead-pulse")
    def dead_pulse(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.repeat(
            authoring.play(
                authoring.drive(qubit),
                authoring.constant(
                    duration=Quantity(8, "ns"),
                    amplitude=amplitude,
                ),
            ),
            0,
        )

    assert [element.id for element in dead_pulse.elements] == ["qubit"]
    assert dead_pulse.inputs == ()


def test_implemented_gate_only_tightens_pulse_repeat_inputs() -> None:
    q0 = authoring.qubit("q0")
    gate_value = authoring.scalar_input("gate_value", GateParameterKind.INTEGER)
    pulse_count = authoring.input(
        "pulse_count",
        sc.ScalarType(sc.IntType()),
    )
    custom = authoring.single_qubit_gate(
        "custom",
        parameters={"value": GateParameterKind.INTEGER},
    )
    pulse = authoring.repeat(
        authoring.play(
            authoring.drive(q0),
            authoring.constant(
                duration=Quantity(8, "ns"),
                amplitude=Quantity(0.2, "arb"),
            ),
        ),
        pulse_count,
    )
    declaration = authoring._close_program(
        "implemented-repeat-inputs",
        authoring._implement_gate(custom(q0, value=gate_value), pulse),
    )

    assert declaration.inputs == (gate_value, pulse_count)
    authoring.bind(declaration, {"gate_value": -1, "pulse_count": 1})
    with pytest.raises(
        authoring.ProgramBindingError,
        match=r"bindings\.pulse_count",
    ):
        authoring.bind(declaration, {"gate_value": -1, "pulse_count": -1})


@pytest.mark.parametrize("count", [-1, 1.5, True])
def test_symbolic_repeat_rejects_invalid_bound_counts(count: object) -> None:
    declaration, _x_count, _raw_iq = _x_count_declaration()

    with pytest.raises(
        authoring.ProgramBindingError,
        match=r"bindings\.x_count",
    ):
        authoring.bind(
            declaration,
            {"x_count": count},
        )


@pytest.mark.parametrize("count", [-1, 1.5, True])
def test_repeat_rejects_invalid_literal_counts(count: int) -> None:
    q0 = authoring.qubit("q0")
    x = authoring.single_qubit_gate("x")

    with pytest.raises(ValueError, match="non-negative integer"):
        authoring.repeat(x(q0), count)


def test_repeat_rejects_non_integer_input_and_result_producers() -> None:
    q0 = authoring.qubit("q0")
    angle = authoring.scalar_input("theta", GateParameterKind.ANGLE)

    with pytest.raises(TypeError, match="integer kind"):
        authoring.repeat(authoring.single_qubit_gate("x")(q0), angle)
    with pytest.raises(ValueError, match="require result_dimension"):
        authoring.repeat(authoring.measure(q0, result="raw_iq"), 2)


def test_gate_parameter_inputs_are_checked_and_angle_values_are_canonicalized() -> None:
    q0 = authoring.qubit("q0")
    integer = authoring.scalar_input("count", GateParameterKind.INTEGER)
    theta = authoring.scalar_input("theta", GateParameterKind.ANGLE)
    rx = authoring.single_qubit_gate(
        "rx",
        parameters={"theta": GateParameterKind.ANGLE},
    )
    with pytest.raises(TypeError, match="requires 'angle'"):
        rx(q0, theta=integer)

    declaration = authoring._close_program("rx", rx(q0, theta=theta))
    bound = authoring.bind(declaration, {"theta": Quantity(180, "deg")})
    operation = bound.verified.operations[0]

    assert isinstance(operation, GateCall)
    assert operation.arguments[0].id == "theta"
    assert operation.arguments[0].value == Quantity(180, "deg").to("rad")


def test_gate_call_requires_exact_named_arguments() -> None:
    q0 = authoring.qubit("q0")
    rx = authoring.single_qubit_gate(
        "rx",
        parameters={"theta": GateParameterKind.ANGLE},
    )

    with pytest.raises(ValueError, match="missing 'theta'"):
        rx(q0)
    with pytest.raises(ValueError, match="unknown 'phase'"):
        rx(q0, theta=Quantity(0, "rad"), phase=0)


def test_program_rejects_duplicate_result_ports() -> None:
    q0 = authoring.qubit("q0")
    first = authoring.measure(q0, result="raw_iq")
    second = authoring.measure(q0, result="raw_iq")

    with pytest.raises(ValueError, match="duplicate result ids"):
        authoring._close_program("duplicate-results", authoring.sequence(first, second))


def test_program_rejects_conflicting_gate_definitions() -> None:
    q0 = authoring.qubit("q0")
    first = authoring.single_qubit_gate("custom")
    second = authoring.single_qubit_gate(
        "custom",
        parameters={"value": GateParameterKind.NUMBER},
    )

    with pytest.raises(ValueError, match="conflicting definitions"):
        authoring._close_program(
            "conflicting-gates",
            authoring.sequence(first(q0), second(q0, value=1.0)),
        )


def test_program_call_normalizes_number_literal() -> None:
    drive = authoring.single_qubit_gate(
        "drive",
        parameters={"amplitude": GateParameterKind.NUMBER},
    )

    @authoring.program(id="number-input")
    def declaration(
        qubit: authoring.Qubit,
        amplitude: Annotated[float, GateParameterKind.NUMBER],
    ) -> authoring.QuantumFragment:
        return authoring.sequence(
            drive(qubit, amplitude=amplitude),
            authoring.measure(qubit, result="iq"),
        )

    call = declaration.call("call", "q0", 1)
    assert dict(call.domain_call.execution.input_bindings)["amplitude"] == 1.0
