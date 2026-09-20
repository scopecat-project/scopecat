from __future__ import annotations

import math

import pytest
from scopecat import Quantity

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CircuitOperationId,
    CouplerId,
    GateId,
    PulseEventId,
    PulseImplementationId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import INTEGRATED_IQ_RESULT
from scopecat_quantum.circuits import (
    Measure,
    VerifiedCircuitOperations,
    verify_circuit_operations,
)
from scopecat_quantum.gates import (
    GateArgument,
    GateArgumentValue,
    GateCall,
    GateDefinition,
    GateParameterDefinition,
    GateParameterKind,
)
from scopecat_quantum.pulse_implementations import (
    GatePulseImplementation,
    GatePulseImplementationArgument,
    GatePulseImplementationBinding,
    GatePulseImplementationKey,
    MeasurementPulseImplementation,
    MeasurementPulseImplementationKey,
    PulseImplementationBindingError,
    PulseImplementationBindingIssue,
    PulseImplementationBindingIssueCode,
    ResolvedPulseImplementations,
    bind_pulse_implementations,
)
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    AcquisitionSlot,
    Constant,
    Delay,
    DriveSignal,
    FluxSignal,
    Play,
    PulseProgram,
    ReadoutSignal,
)
from scopecat_quantum.pulses import Parallel as PulseParallel

X = GateDefinition(
    id=GateId("x"),
    qubit_arity=1,
    parameters=(GateParameterDefinition(id="angle", kind=GateParameterKind.ANGLE),),
)
Y = GateDefinition(
    id=GateId("y"),
    qubit_arity=1,
    parameters=(GateParameterDefinition(id="angle", kind=GateParameterKind.ANGLE),),
)
ROTATE = GateDefinition(
    id=GateId("rotate"),
    qubit_arity=1,
    parameters=(
        GateParameterDefinition(id="angle", kind=GateParameterKind.ANGLE),
        GateParameterDefinition(id="phase", kind=GateParameterKind.ANGLE),
    ),
)
Q0 = QubitId("q0")
Q1 = QubitId("q1")
C01 = CouplerId("c01")


def _pulse_template() -> PulseProgram:
    return PulseProgram(
        id=PulseProgramId("calibrated-pulse"),
        body=Delay(
            id=PulseEventId("delay"),
            signal=DriveSignal(Q0),
            duration=Quantity(20, "ns"),
        ),
    )


def _measurement_template() -> PulseProgram:
    acquire_signal = AcquireSignal(Q0)
    slot = AcquisitionSlot(
        AcquisitionSlotId("template-result"),
        INTEGRATED_IQ_RESULT,
        acquire_signal,
    )
    return PulseProgram(
        id=PulseProgramId("readout-template"),
        body=PulseParallel(
            (
                Play(
                    PulseEventId("readout"),
                    ReadoutSignal(Q0),
                    Constant(Quantity(200, "ns"), Quantity(0.2, "ratio")),
                ),
                Acquire(
                    PulseEventId("acquire"),
                    acquire_signal,
                    slot.id,
                    Quantity(200, "ns"),
                ),
            )
        ),
        acquisition_slots=(slot,),
    )


def _call(
    operation_id: str,
    *,
    gate_id: GateId = X.id,
    qubit: QubitId = Q0,
    angle: float = 0.5,
) -> GateCall:
    return GateCall(
        id=CircuitOperationId(operation_id),
        gate_id=gate_id,
        qubits=(qubit,),
        arguments=(GateArgument(id="angle", value=Quantity(angle, "rad")),),
    )


def _verified(*calls: GateCall) -> VerifiedCircuitOperations:
    return verify_circuit_operations(
        calls,
        gate_definitions=(X, Y, ROTATE),
    )


def _implementation(
    implementation_id: str,
    call: GateCall,
    *,
    pulse_template: PulseProgram | None = None,
) -> GatePulseImplementation:
    return GatePulseImplementation(
        id=PulseImplementationId(implementation_id),
        key=GatePulseImplementationKey.from_call(call),
        pulse_template=pulse_template or _pulse_template(),
    )


def _implementations(
    *implementations: GatePulseImplementation,
) -> ResolvedPulseImplementations:
    return ResolvedPulseImplementations(gates=implementations)


def test_exact_implementation_key_contains_call_data_not_gate_definition() -> None:
    call = _call("x-q0")

    key = GatePulseImplementationKey.from_call(call)

    assert key == GatePulseImplementationKey(
        gate_id=GateId("x"),
        operands=(Q0,),
        arguments=(
            GatePulseImplementationArgument(id="angle", value=Quantity(0.5, "rad")),
        ),
    )


def test_bindings_have_exact_gate_call_coverage() -> None:
    first = _call("first")
    second = _call("second")
    pulse = _pulse_template()

    bindings = bind_pulse_implementations(
        _verified(first, second),
        _implementations(_implementation("x-q0", first, pulse_template=pulse)),
    )

    assert all(
        isinstance(binding, GatePulseImplementationBinding)
        for binding in bindings.bindings
    )
    assert tuple(
        binding.call_id
        for binding in bindings.bindings
        if isinstance(binding, GatePulseImplementationBinding)
    ) == (
        first.id,
        second.id,
    )
    assert bindings.binding_for(first.id).pulse_template is pulse


def test_binding_distinguishes_one_recipe_resolved_with_different_values() -> None:
    call = _call("x")
    first = _implementation("x-q0", call)
    second = _implementation(
        "x-q0",
        call,
        pulse_template=PulseProgram(
            id=first.pulse_template.id,
            body=Delay(
                id=PulseEventId("delay"),
                signal=DriveSignal(Q0),
                duration=Quantity(21, "ns"),
            ),
        ),
    )

    [binding] = bind_pulse_implementations(
        _verified(call),
        _implementations(first),
    ).bindings

    assert first.id == second.id
    assert first.fingerprint != second.fingerprint
    assert binding.implementation_fingerprint == first.fingerprint


def test_named_argument_order_does_not_change_implementation_bindings() -> None:
    implementation_call = GateCall(
        id=CircuitOperationId("implementation-call"),
        gate_id=ROTATE.id,
        qubits=(Q0,),
        arguments=(
            GateArgument(id="phase", value=Quantity(0.25, "rad")),
            GateArgument(id="angle", value=Quantity(0.5, "rad")),
        ),
    )
    program_call = GateCall(
        id=CircuitOperationId("program-call"),
        gate_id=ROTATE.id,
        qubits=(Q0,),
        arguments=(
            GateArgument(id="angle", value=Quantity(0.5, "rad")),
            GateArgument(id="phase", value=Quantity(0.25, "rad")),
        ),
    )

    bindings = bind_pulse_implementations(
        _verified(program_call),
        _implementations(_implementation("rotate", implementation_call)),
    )

    [binding] = bindings.bindings
    assert isinstance(binding, GatePulseImplementationBinding)
    assert binding.key.arguments == (
        GatePulseImplementationArgument(id="angle", value=Quantity(0.5, "rad")),
        GatePulseImplementationArgument(id="phase", value=Quantity(0.25, "rad")),
    )


def test_equivalent_angle_units_have_one_exact_implementation_key() -> None:
    implementation_call = GateCall(
        id=CircuitOperationId("implementation-call"),
        gate_id=X.id,
        qubits=(Q0,),
        arguments=(GateArgument(id="angle", value=Quantity(180, "deg")),),
    )
    program_call = GateCall(
        id=CircuitOperationId("program-call"),
        gate_id=X.id,
        qubits=(Q0,),
        arguments=(GateArgument(id="angle", value=Quantity(math.pi, "rad")),),
    )
    implementation = _implementation("x-180", implementation_call)

    bindings = bind_pulse_implementations(
        _verified(program_call),
        _implementations(implementation),
    )

    [binding] = bindings.bindings
    assert isinstance(binding, GatePulseImplementationBinding)
    assert binding.key == implementation.key
    assert implementation.key.arguments[0].value == Quantity(math.pi, "rad")


def test_implementation_key_rejects_duplicate_named_arguments() -> None:
    with pytest.raises(ValueError, match="argument ids must be unique"):
        GatePulseImplementationKey(
            gate_id=ROTATE.id,
            operands=(Q0,),
            arguments=(
                GatePulseImplementationArgument(id="angle", value=Quantity(0.5, "rad")),
                GatePulseImplementationArgument(
                    id="angle", value=Quantity(0.25, "rad")
                ),
            ),
        )


def test_measurement_without_implementation_prevents_aggregate_binding() -> None:
    call = _call("gate")
    measurement = Measure(
        id=CircuitOperationId("measure"),
        qubit=Q0,
        acquisition_slot_id=AcquisitionSlotId("readout"),
        contract=INTEGRATED_IQ_RESULT,
    )
    program = verify_circuit_operations(
        (call, measurement),
        gate_definitions=(X,),
    )

    with pytest.raises(PulseImplementationBindingError) as raised:
        bind_pulse_implementations(
            program, _implementations(_implementation("gate", call))
        )

    assert len(raised.value.issues) == 1
    issue = raised.value.issues[0]
    assert issue.code is PulseImplementationBindingIssueCode.MISSING
    assert issue.operation_id == measurement.id
    assert issue.message == "operation 'measure' has no exact pulse implementation"
    assert issue.key == MeasurementPulseImplementationKey(
        qubit=Q0,
        contract=INTEGRATED_IQ_RESULT,
    )


def test_resolved_measurement_implementation_keys_must_be_unique() -> None:
    measurement = Measure(
        id=CircuitOperationId("measure"),
        qubit=Q0,
        acquisition_slot_id=AcquisitionSlotId("result"),
        contract=INTEGRATED_IQ_RESULT,
    )
    key = MeasurementPulseImplementationKey.from_measurement(measurement)
    template = _measurement_template()
    entries = (
        MeasurementPulseImplementation(
            PulseImplementationId("readout-z"), key, template
        ),
        MeasurementPulseImplementation(
            PulseImplementationId("readout-a"), key, template
        ),
    )

    for ordered_entries in (entries, tuple(reversed(entries))):
        with pytest.raises(ValueError, match=r"measurement.*keys must be unique"):
            ResolvedPulseImplementations(measurements=ordered_entries)


def test_missing_implementations_are_aggregated_deterministically() -> None:
    first = _call("z-missing", gate_id=X.id)
    second = _call("a-missing", gate_id=Y.id)

    with pytest.raises(PulseImplementationBindingError) as raised:
        bind_pulse_implementations(_verified(first, second), _implementations())

    assert tuple(issue.operation_id for issue in raised.value.issues) == (
        second.id,
        first.id,
    )
    assert all(
        issue.code is PulseImplementationBindingIssueCode.MISSING
        for issue in raised.value.issues
    )


@pytest.mark.parametrize(
    "other_call",
    [
        _call("different-operand", qubit=Q1),
        _call("different-argument", angle=0.25),
        _call("different-gate", gate_id=Y.id),
    ],
)
def test_binding_never_falls_back_from_an_exact_key(other_call: GateCall) -> None:
    implementation_call = _call("implementation-call")

    with pytest.raises(PulseImplementationBindingError) as raised:
        bind_pulse_implementations(
            _verified(other_call),
            _implementations(_implementation("reference", implementation_call)),
        )

    assert raised.value.issues[0].code is PulseImplementationBindingIssueCode.MISSING


def test_resolved_implementation_ids_are_unique_across_keys() -> None:
    with pytest.raises(ValueError, match="implementation ids must be unique"):
        ResolvedPulseImplementations(
            gates=(
                _implementation("duplicate", _call("first")),
                _implementation("duplicate", _call("second", gate_id=Y.id)),
            )
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        float("nan"),
        float("inf"),
        Quantity(float("nan"), "rad"),
    ],
)
def test_implementation_argument_rejects_invalid_values(
    value: GateArgumentValue,
) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        GatePulseImplementationArgument(id="angle", value=value)


@pytest.mark.parametrize("operands", [(), (Q0, Q0)])
def test_implementation_key_requires_nonempty_unique_operands(
    operands: tuple[QubitId, ...],
) -> None:
    with pytest.raises(ValueError, match="operand"):
        GatePulseImplementationKey(gate_id=X.id, operands=operands)


def test_issue_enforces_value_invariants() -> None:
    call = _call("call")
    key = GatePulseImplementationKey.from_call(call)
    with pytest.raises(ValueError, match="message must be non-empty"):
        PulseImplementationBindingIssue(
            code=PulseImplementationBindingIssueCode.MISSING,
            operation_id=call.id,
            key=key,
            message=" ",
        )


def test_gate_implementations_cannot_produce_acquisition_results() -> None:
    call = _call("call")
    key = GatePulseImplementationKey.from_call(call)
    acquire_signal = AcquireSignal(Q0)
    slot = AcquisitionSlot(
        id=AcquisitionSlotId("readout"),
        contract=INTEGRATED_IQ_RESULT,
        signal=acquire_signal,
    )
    declared_slot_program = PulseProgram(
        id=PulseProgramId("declares-acquisition"),
        body=Delay(
            id=PulseEventId("delay"),
            signal=DriveSignal(Q0),
            duration=Quantity(20, "ns"),
        ),
        acquisition_slots=(slot,),
    )
    with pytest.raises(ValueError, match="cannot declare acquisition slots"):
        GatePulseImplementation(
            id=PulseImplementationId("implementation"),
            key=key,
            pulse_template=declared_slot_program,
        )


def test_gate_implementation_coupler_resources_authorize_signal_owners() -> None:
    call = _call("call")
    pulse_template = PulseProgram(
        id=PulseProgramId("coupler-flux"),
        body=Delay(
            id=PulseEventId("flux"),
            signal=FluxSignal(C01),
            duration=Quantity(20, "ns"),
        ),
    )

    with pytest.raises(ValueError, match="unauthorized signal owners"):
        GatePulseImplementation(
            id=PulseImplementationId("missing-resource"),
            key=GatePulseImplementationKey.from_call(call),
            pulse_template=pulse_template,
        )

    implementation = GatePulseImplementation(
        id=PulseImplementationId("with-resource"),
        key=GatePulseImplementationKey.from_call(call),
        pulse_template=pulse_template,
        resources=(C01,),
    )

    assert implementation.resources == (C01,)
