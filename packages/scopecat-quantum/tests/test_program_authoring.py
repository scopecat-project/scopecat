from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Annotated, Literal, cast

import pytest
import scopecat as sc
from scopecat import Quantity

from scopecat_quantum import authoring
from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CouplerId,
    PulseEventId,
    PulseImplementationId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.circuits import Measure
from scopecat_quantum.gates import GateCall, GateParameterKind
from scopecat_quantum.programs import (
    ImplementedGate,
    PulseBlock,
    Repeat,
    materialize_quantum_pulse_program,
    plan_quantum_pulse_lowering,
)
from scopecat_quantum.programs import (
    Parallel as QuantumParallel,
)
from scopecat_quantum.programs import (
    Sequence as QuantumSequence,
)
from scopecat_quantum.pulse_implementations import (
    GatePulseImplementation,
    GatePulseImplementationKey,
    ResolvedPulseImplementations,
)
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    Constant,
    CosineFlatTop,
    Delay,
    DerivativeQuadrature,
    DriveSignal,
    EnvelopePhaseReference,
    FluxSignal,
    FrequencyShift,
    Gaussian,
    Play,
    PulseProgram,
    PulseValidationError,
    ReadoutSignal,
    ShiftPhase,
    iter_pulse_leaves,
    schedule,
)


@pytest.mark.parametrize("alignment", ["start", "end"])
def test_parallel_alignment_moves_whole_branch_without_changing_evolution(
    alignment: Literal["start", "end"],
) -> None:
    left, right = authoring.qubit("left"), authoring.qubit("right")

    def play(q: authoring.Qubit, ns: int) -> authoring.PulseFragment:
        return authoring.play(
            authoring.drive(q),
            authoring.constant(
                duration=Quantity(ns, "ns"), amplitude=Quantity(0.1, "arb")
            ),
        )

    declaration = authoring._close_program(
        "aligned-branches",
        authoring.sequence(
            authoring.delay(authoring.drive(left), Quantity(3, "ns")),
            authoring.parallel(
                play(left, 20),
                authoring.sequence(
                    play(right, 5),
                    authoring.delay(authoring.drive(right), Quantity(7, "ns")),
                ),
                alignment=alignment,
            ),
        ),
    )
    bound = authoring.bind(declaration)
    lowered = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("aligned-pulses"),
    )
    scheduled = schedule(materialize_quantum_pulse_program(lowered))
    events = [
        e
        for e in scheduled.events
        if e.instruction.signal == DriveSignal(QubitId("right"))
    ]
    pulse = next(e for e in events if isinstance(e.instruction, Play))
    idle = next(e for e in events if isinstance(e.instruction, Delay))
    ns = Decimal("1e-9")
    assert pulse.start_seconds == (11 if alignment == "end" else 3) * ns
    assert idle.start_seconds == pulse.start_seconds + 5 * ns
    assert idle.duration_seconds == 7 * ns
    assert scheduled.duration_seconds == 23 * ns


@pytest.mark.parametrize("width", [2, 4, 16, 64])
def test_end_aligned_width_preserves_entity_timing_under_permutation(
    width: int,
) -> None:
    def scheduled_times(order: Iterable[int]) -> dict[str, tuple[Decimal, Decimal]]:
        branches = tuple(
            authoring.play(
                authoring.drive(authoring.qubit(f"q{i}")),
                authoring.constant(
                    duration=Quantity(i + 1, "ns"), amplitude=Quantity(0.1, "arb")
                ),
            )
            for i in order
        )
        bound = authoring.bind(
            authoring._close_program(
                "wide-end-aligned", authoring.parallel(*branches, alignment="end")
            )
        )
        plan = plan_quantum_pulse_lowering(
            bound.verified,
            ResolvedPulseImplementations(),
            output_id=PulseProgramId("wide-pulses"),
        )
        return {
            cast("DriveSignal", e.instruction.signal).qubit.value: (
                e.start_seconds,
                e.duration_seconds,
            )
            for e in schedule(materialize_quantum_pulse_program(plan)).events
        }

    forward = scheduled_times(range(width))
    assert forward == scheduled_times(reversed(range(width)))
    assert all(
        start + duration == width * Decimal("1e-9")
        for start, duration in forward.values()
    )


def _beta_input() -> authoring.ProgramInput:
    return authoring.input(
        "beta",
        sc.ScalarType(sc.QuantityType(unit="ns")),
    )


def _symbolic_quantity(value: authoring.ProgramInput) -> Quantity:
    """Expose a runtime symbolic handle through a decorated source signature."""

    return cast("Quantity", cast("object", value))


def _drag_play(
    qubit: authoring.Qubit,
    beta: authoring.QuantumQuantity,
) -> authoring.PulseFragment:
    return authoring.play(
        authoring.drive(qubit),
        authoring.drag(
            duration=Quantity(16, "ns"),
            amplitude=Quantity(0.2, "arb"),
            sigma=Quantity(4, "ns"),
            beta=beta,
        ),
    )


def test_sequence_composes_baseline_gate_candidate_pulse_and_measurement() -> None:
    q0 = authoring.qubit("q0")
    beta = _beta_input()
    x90 = authoring.single_qubit_gate("x90")
    readout = authoring.measure(q0, result="raw_iq")
    declaration = authoring._close_program(
        "drag-calibration-point",
        authoring.sequence(
            x90(q0),
            authoring._implement_gate(
                x90(q0),
                _drag_play(q0, beta),
                candidate="x90.drag",
            ),
            readout,
        ),
    )

    bound = authoring.bind(declaration, {"beta": Quantity(0.75, "ns")})

    assert declaration.inputs == (beta,)
    assert tuple(declaration.results) == (readout.result,)
    assert isinstance(bound.program.body, QuantumSequence)
    assert [type(operation) for operation in bound.verified.operations] == [
        GateCall,
        ImplementedGate,
        Measure,
    ]
    unresolved_types = [
        type(operation) for operation in bound.verified.unresolved.operations
    ]
    assert unresolved_types == [
        GateCall,
        Measure,
    ]


def test_repeat_preserves_one_candidate_call_until_pulse_lowering() -> None:
    q0 = authoring.qubit("q0")
    beta = _beta_input()
    repetitions = authoring.input(
        "repetitions",
        sc.ScalarType(sc.IntType()),
    )
    x90 = authoring.single_qubit_gate("x90")
    candidate = authoring._implement_gate(
        x90(q0),
        _drag_play(q0, beta),
        candidate="x90.drag",
    )
    declaration = authoring._close_program(
        "repeated-drag-candidate",
        authoring.repeat(candidate, repetitions),
    )

    bound = authoring.bind(
        declaration,
        {"beta": Quantity(0.75, "ns"), "repetitions": 3},
    )
    implementations = tuple(
        operation
        for operation in bound.verified.operations
        if isinstance(operation, ImplementedGate)
    )

    assert isinstance(bound.program.body, Repeat)
    assert bound.program.body.count == 3
    [implementation] = implementations
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("repeated-drag-pulses"),
    )
    assert isinstance(plan.body, Repeat)
    assert plan.body.count == 3
    pulses = materialize_quantum_pulse_program(plan)
    assert len(tuple(iter_pulse_leaves(pulses.body))) == 3
    assert implementation.candidate_id == "x90.drag"
    [pulse] = tuple(iter_pulse_leaves(implementation.pulse_template.body))
    assert isinstance(pulse, Play)
    assert isinstance(pulse.envelope, DerivativeQuadrature)
    assert isinstance(pulse.envelope.envelope, Gaussian)
    assert pulse.envelope.beta == Quantity(0.75, "ns")


def test_gate_and_pulse_can_bind_in_parallel_before_final_signal_check() -> None:
    q0 = authoring.qubit("q0")
    x90 = authoring.single_qubit_gate("x90")
    declaration = authoring._close_program(
        "parallel-drive-conflict",
        authoring.parallel(
            x90(q0),
            authoring.play(
                authoring.drive(q0),
                authoring.constant(
                    duration=Quantity(16, "ns"),
                    amplitude=Quantity(0.1, "arb"),
                ),
            ),
        ),
    )

    bound = authoring.bind(declaration)
    assert isinstance(bound.program.body, QuantumParallel)
    gate_call = next(
        operation
        for operation in bound.verified.operations
        if isinstance(operation, GateCall)
    )
    implementation_template = PulseProgram(
        id=PulseProgramId("x90-q0-template"),
        body=Play(
            id=PulseEventId("drive"),
            signal=DriveSignal(gate_call.qubits[0]),
            envelope=Constant(
                duration=Quantity(16, "ns"),
                amplitude=Quantity(0.2, "arb"),
            ),
        ),
    )
    implementations = ResolvedPulseImplementations(
        gates=(
            GatePulseImplementation(
                id=PulseImplementationId("x90-q0"),
                key=GatePulseImplementationKey.from_call(gate_call),
                pulse_template=implementation_template,
            ),
        )
    )

    plan = plan_quantum_pulse_lowering(
        bound.verified,
        implementations,
        output_id=PulseProgramId("parallel-drive-conflict-pulses"),
    )

    with pytest.raises(PulseValidationError) as caught:
        schedule(materialize_quantum_pulse_program(plan))

    assert {issue.code for issue in caught.value.issues} == {"pulse_signal_overlap"}


def test_gate_implementation_rejects_a_foreign_pulse_qubit() -> None:
    q0 = authoring.qubit("q0")
    q1 = authoring.qubit("q1")
    x90 = authoring.single_qubit_gate("x90")

    with pytest.raises(ValueError, match="unauthorized signal owners: 'q1'"):
        authoring._implement_gate(
            x90(q0),
            authoring.play(
                authoring.drive(q1),
                authoring.constant(
                    duration=Quantity(16, "ns"),
                    amplitude=Quantity(0.2, "arb"),
                ),
            ),
        )


def test_two_qubit_gate_implementation_authorizes_and_lowers_coupler_pulse() -> None:
    amplitude = authoring.input(
        "amplitude",
        sc.ScalarType(sc.QuantityType(unit="arb")),
    )

    cz = authoring.two_qubit_gate("cz")

    @authoring.implementation(
        of=cz,
        candidate="cz.conditional-phase",
        id="cz.flux",
    )
    def cz_flux(
        control: authoring.Qubit,
        target: authoring.Qubit,
        coupler: authoring.Coupler,
        amplitude: Annotated[Quantity, sc.QuantityType(unit="arb")],
    ) -> authoring.QuantumFragment:
        return authoring.play(
            authoring.flux(coupler),
            authoring.constant(
                duration=Quantity(32, "ns"),
                amplitude=amplitude,
            ),
        )

    q0 = authoring.qubit("q0")
    q1 = authoring.qubit("q1")
    c01 = authoring.coupler("coupler-q0-q1")
    declaration = authoring._close_program(
        "cz-candidate",
        cz_flux(q0, q1, c01, amplitude=_symbolic_quantity(amplitude)),
    )

    bound = authoring.bind(declaration, {"amplitude": Quantity(0.24, "arb")})
    [logical_call] = bound.verified.logical_operations
    [implementation] = bound.verified.operations
    assert isinstance(logical_call, GateCall)
    assert logical_call.qubits == (QubitId("q0"), QubitId("q1"))
    assert isinstance(implementation, ImplementedGate)
    assert implementation.call == logical_call

    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("cz-candidate-pulses"),
    )
    [leaf] = iter_pulse_leaves(materialize_quantum_pulse_program(plan).body)
    assert isinstance(leaf, Play)
    assert leaf.signal == FluxSignal(CouplerId("coupler-q0-q1"))
    assert implementation.candidate_id == "cz.conditional-phase"
    assert implementation.pulse_template.id == PulseProgramId("cz.flux")


def test_gate_implementation_maps_named_semantic_parameters() -> None:
    rx = authoring.single_qubit_gate(
        "rx",
        parameters={"theta": GateParameterKind.ANGLE},
    )

    @authoring.implementation(of=rx, id="rx.frame-shift")
    def rx_frame_shift(
        qubit: authoring.Qubit,
        theta: Annotated[Quantity, sc.QuantityType(unit="rad")],
    ) -> authoring.QuantumFragment:
        return authoring.shift_phase(authoring.drive(qubit), theta)

    q0 = authoring.qubit("q0")
    theta = authoring.input(
        "theta",
        sc.ScalarType(sc.QuantityType(unit="rad")),
    )
    declaration = authoring._close_program(
        "parameterized-rx",
        rx_frame_shift(q0, theta=_symbolic_quantity(theta)),
    )

    bound = authoring.bind(declaration, {"theta": Quantity(90, "deg")})
    [logical_call] = bound.verified.logical_operations
    [implemented] = bound.verified.operations

    assert isinstance(logical_call, GateCall)
    assert logical_call.arguments[0].id == "theta"
    assert logical_call.arguments[0].value == Quantity(90, "deg").to("rad")
    assert isinstance(implemented, ImplementedGate)
    assert implemented.call == logical_call


def test_gate_implementation_requires_coupler_resource_authorization() -> None:
    q0 = authoring.qubit("q0")
    q1 = authoring.qubit("q1")
    c01 = authoring.coupler("coupler-q0-q1")
    cz = authoring.two_qubit_gate("cz")
    pulse = authoring.play(
        authoring.flux(c01),
        authoring.constant(
            duration=Quantity(32, "ns"),
            amplitude=Quantity(0.24, "arb"),
        ),
    )

    with pytest.raises(
        ValueError,
        match="unauthorized signal owners: 'coupler-q0-q1'",
    ):
        authoring._implement_gate(cz(q0, q1), pulse)


def test_drag_beta_requires_a_time_typed_input() -> None:
    frequency = authoring.input(
        "frequency",
        sc.ScalarType(sc.QuantityType(unit="GHz")),
    )

    with pytest.raises(TypeError, match="must declare 'time' quantity units"):
        authoring.drag(
            duration=Quantity(16, "ns"),
            amplitude=Quantity(0.2, "arb"),
            sigma=Quantity(4, "ns"),
            beta=frequency,
        )


def test_derivative_quadrature_wraps_a_cosine_envelope() -> None:
    q0 = authoring.qubit("q0")
    beta = _beta_input()
    declaration = authoring._close_program(
        "cosine-derivative",
        authoring.play(
            authoring.drive(q0),
            authoring.derivative_quadrature(
                authoring.cosine_flat_top(
                    duration=Quantity(16, "ns"),
                    amplitude=Quantity(0.2, "arb"),
                    rise_duration=Quantity(8, "ns"),
                    fall_duration=Quantity(8, "ns"),
                ),
                beta=beta,
            ),
        ),
    )

    bound = authoring.bind(declaration, {"beta": Quantity(-0.5, "ns")})
    [block] = bound.verified.operations
    assert isinstance(block, PulseBlock)
    [play] = iter_pulse_leaves(block.pulse_template.body)
    assert isinstance(play, Play)
    assert isinstance(play.envelope, DerivativeQuadrature)
    assert isinstance(play.envelope.envelope, CosineFlatTop)
    assert play.envelope.beta == Quantity(-0.5, "ns")


def test_derivative_quadrature_rejects_a_constant_envelope() -> None:
    with pytest.raises(TypeError, match="Gaussian or cosine-flat-top"):
        authoring.derivative_quadrature(
            authoring.constant(
                duration=Quantity(16, "ns"),
                amplitude=Quantity(0.2, "arb"),
            ),
            beta=Quantity(-0.5, "ns"),
        )


def test_pulse_local_frequency_shift_binds_outside_derivative_quadrature() -> None:
    q0 = authoring.qubit("q0")
    detuning = authoring.input(
        "detuning",
        sc.ScalarType(sc.QuantityType(unit="MHz")),
    )
    envelope = authoring.frequency_shift(
        authoring.derivative_quadrature(
            authoring.gaussian(
                duration=Quantity(16, "ns"),
                amplitude=Quantity(0.2, "arb"),
                sigma=Quantity(4, "ns"),
            ),
            beta=Quantity(-0.4, "ns"),
        ),
        offset=detuning,
    )
    declaration = authoring._close_program(
        "local-detuning",
        authoring.play(authoring.drive(q0), envelope),
    )

    bound = authoring.bind(declaration, {"detuning": Quantity(-1.25, "MHz")})
    [block] = bound.verified.operations
    assert isinstance(block, PulseBlock)
    [play] = iter_pulse_leaves(block.pulse_template.body)
    assert isinstance(play, Play)
    assert isinstance(play.envelope, FrequencyShift)
    assert play.envelope.frequency_offset == Quantity(-1.25, "MHz")
    assert play.envelope.phase_reference == "center"
    assert isinstance(play.envelope.envelope, DerivativeQuadrature)


def test_pulse_local_frequency_shift_rejects_time_and_invalid_reference() -> None:
    envelope = authoring.gaussian(
        duration=Quantity(16, "ns"),
        amplitude=Quantity(0.2, "arb"),
        sigma=Quantity(4, "ns"),
    )

    with pytest.raises(TypeError, match="frequency quantity"):
        authoring.frequency_shift(envelope, offset=Quantity(1, "ns"))
    with pytest.raises(ValueError, match="phase_reference"):
        authoring.frequency_shift(
            envelope,
            offset=Quantity(1, "MHz"),
            phase_reference=cast("EnvelopePhaseReference", cast("object", "end")),
        )


def test_program_binding_requires_exact_typed_inputs() -> None:
    q0 = authoring.qubit("q0")
    beta = _beta_input()
    declaration = authoring._close_program("drag", _drag_play(q0, beta))

    with pytest.raises(authoring.ProgramBindingError, match="missing 'beta'"):
        authoring.bind(declaration)
    with pytest.raises(authoring.ProgramBindingError, match="unknown 'other'"):
        authoring.bind(
            declaration,
            {"beta": Quantity(0.75, "ns"), "other": 1},
        )
    with pytest.raises(
        authoring.ProgramBindingError,
        match="expected quantity",
    ):
        authoring.bind(declaration, {"beta": "not-a-quantity"})


def test_domain_program_and_execution_expose_typed_ports() -> None:
    beta_type = sc.ScalarType(sc.QuantityType(unit="ns"))

    @authoring.program(id="typed-drag-program")
    def declaration(
        qubit: authoring.Qubit,
        repetitions: int,
        beta: Annotated[
            Quantity,
            sc.ScalarType(sc.QuantityType(unit="ns")),
        ],
    ) -> authoring.QuantumFragment:
        return authoring.sequence(
            authoring.repeat(_drag_play(qubit, beta), repetitions),
            authoring.measure(qubit, result="raw_iq"),
        )

    repetitions_type = sc.ScalarType(sc.IntType(minimum=0))
    repetitions_point = sc.coordinate("repetitions", repetitions_type)
    beta_point = sc.coordinate("beta", beta_type)

    call = declaration.call(
        "drag",
        "q0",
        repetitions_point,
        beta_point,
    )
    execution = call.domain_call.execution
    domain_program = execution.program

    assert domain_program.dialect_id == authoring.QUANTUM_PROGRAM_DIALECT_ID
    assert domain_program.body is declaration
    assert [(port.id, port.value_type) for port in domain_program.input_ports] == [
        (
            "qubit",
            sc.ScalarType(
                sc.EntityType(entity_kind="logical_qubit"),
            ),
        ),
        ("repetitions", repetitions_type),
        ("beta", beta_type),
    ]
    assert domain_program.result_ports[0].id == "raw_iq"
    assert domain_program.result_ports[0].contract is declaration.results.raw_iq
    assert execution.input_bindings[1:] == (
        ("repetitions", repetitions_point),
        ("beta", beta_point),
    )
    assert execution.result_bindings[0][0] == "raw_iq"
    assert execution.result_bindings[0][1].id == "drag/raw_iq"


def test_explicit_acquire_composes_with_readout_play_and_keeps_public_slot() -> None:
    q0 = authoring.qubit("q0")
    capture = authoring.acquire(
        q0,
        duration=Quantity(8, "ns"),
        result="iq_shots",
    )
    declaration = authoring._close_program(
        "explicit-readout",
        authoring.parallel(
            authoring.play(
                authoring.readout(q0),
                authoring.constant(
                    duration=Quantity(12, "ns"),
                    amplitude=Quantity(0.1, "arb"),
                ),
            ),
            capture,
        ),
    )

    bound = authoring.bind(declaration)
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("explicit-readout-pulses"),
    )
    pulses = materialize_quantum_pulse_program(plan)
    scheduled = schedule(pulses)

    assert tuple(declaration.results) == (capture.result,)
    assert capture.result.qubit is q0
    assert capture.result.acquisition_slot_id == AcquisitionSlotId("iq_shots")
    assert pulses.acquisition_slots[0].id == capture.result.acquisition_slot_id
    assert scheduled.duration_seconds == Decimal("1.2e-8")
    assert {type(event.instruction) for event in scheduled.events} == {Play, Acquire}
    assert {event.start_seconds for event in scheduled.events} == {0}


def test_symbolic_cosine_flat_top_binds_into_explicit_pulse_ir() -> None:
    q0 = authoring.qubit("q0")
    edge = authoring.input(
        "edge",
        sc.ScalarType(sc.QuantityType(unit="ns")),
    )
    declaration = authoring._close_program(
        "cosine-flat-top",
        authoring.play(
            authoring.drive(q0),
            authoring.cosine_flat_top(
                duration=Quantity(20, "ns"),
                amplitude=Quantity(0.2, "arb"),
                rise_duration=_symbolic_quantity(edge),
                fall_duration=_symbolic_quantity(edge),
            ),
        ),
    )

    bound = authoring.bind(declaration, {"edge": Quantity(2, "ns")})
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("cosine-flat-top-pulses"),
    )
    pulses = materialize_quantum_pulse_program(plan)
    [event] = schedule(pulses).events

    assert declaration.inputs == (edge,)
    assert isinstance(event.instruction, Play)
    assert isinstance(event.instruction.envelope, CosineFlatTop)
    assert event.instruction.envelope.rise_duration == Quantity(2e-9, "s")
    assert event.instruction.envelope.fall_duration == Quantity(2e-9, "s")


def test_explicit_acquire_results_cannot_repeat_or_reuse_an_id() -> None:
    q0 = authoring.qubit("q0")
    first = authoring.acquire(
        q0,
        duration=Quantity(8, "ns"),
        result="iq_shots",
    )
    second = authoring.acquire(
        q0,
        duration=Quantity(8, "ns"),
        result="iq_shots",
    )

    with pytest.raises(ValueError, match="require result_dimension"):
        authoring.repeat(first, 2)
    with pytest.raises(ValueError, match="duplicate result ids"):
        authoring._close_program(
            "duplicate-acquisitions",
            authoring.sequence(first, second),
        )


def test_pulse_template_substitutes_qubit_and_outer_input_hygienically() -> None:
    @authoring.pulse_template(id="x90-with-frame")
    def template(
        qubit: authoring.Qubit,
        phase: Annotated[Quantity, sc.QuantityType(unit="rad")],
    ) -> authoring.QuantumFragment:
        return authoring.sequence(
            authoring.shift_phase(authoring.drive(qubit), phase),
            authoring.play(
                authoring.drive(qubit),
                authoring.constant(
                    duration=Quantity(8, "ns"),
                    amplitude=Quantity(0.2, "arb"),
                ),
            ),
        )

    [formal_q] = template.elements
    [formal_phase] = template.inputs
    q0 = authoring.qubit("q0")
    outer_phase = authoring.input(
        "ramsey_phase",
        sc.ScalarType(sc.QuantityType(unit="rad")),
    )
    declaration = authoring._close_program(
        "two-template-calls",
        authoring.sequence(
            template(q0, phase=_symbolic_quantity(outer_phase)),
            template(q0, phase=_symbolic_quantity(outer_phase)),
        ),
    )

    assert template.id == "x90-with-frame"
    assert template.elements == (formal_q,)
    assert template.inputs == (formal_phase,)
    assert declaration.inputs == (outer_phase,)
    bound = authoring.bind(
        declaration,
        {"ramsey_phase": Quantity(90, "deg")},
    )
    blocks = tuple(
        operation
        for operation in bound.verified.operations
        if isinstance(operation, PulseBlock)
    )
    assert len(blocks) == 2
    assert {block.pulse_template.id for block in blocks} == {
        PulseProgramId("x90-with-frame")
    }
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("two-template-calls-pulses"),
    )
    pulses = materialize_quantum_pulse_program(plan)
    assert len({leaf.id for leaf in iter_pulse_leaves(pulses.body)}) == 4
    scheduled = schedule(pulses)
    shifts = tuple(
        event.instruction
        for event in scheduled.events
        if isinstance(event.instruction, ShiftPhase)
    )
    assert [shift.signal for shift in shifts] == [
        DriveSignal(QubitId("q0")),
        DriveSignal(QubitId("q0")),
    ]
    assert all(shift.phase == Quantity(90, "deg").to("rad") for shift in shifts)


def test_pulse_template_rejects_results_and_invalid_typed_values() -> None:
    with pytest.raises(ValueError, match="cannot capture acquisition results"):

        @authoring.pulse_template(id="invalid-readout")
        def invalid_readout(
            qubit: authoring.Qubit,
        ) -> authoring.QuantumFragment:
            return authoring.acquire(
                qubit,
                duration=Quantity(8, "ns"),
                result="iq",
            )

    @authoring.pulse_template(id="delay")
    def delay_template(
        qubit: authoring.Qubit,
        duration: Annotated[Quantity, sc.QuantityType(unit="ns")],
    ) -> authoring.QuantumFragment:
        return authoring.delay(authoring.drive(qubit), duration)

    q0 = authoring.qubit("q0")
    with pytest.raises(TypeError, match="invalid pulse template input 'duration'"):
        delay_template(q0, duration=Quantity(1, "rad"))


def test_shift_phase_accepts_symbolic_phase() -> None:
    q0 = authoring.qubit("q0")
    phase = authoring.input(
        "phase",
        sc.ScalarType(sc.QuantityType(unit="rad")),
    )
    declaration = authoring._close_program(
        "virtual-z",
        authoring.sequence(
            authoring.shift_phase(authoring.drive(q0), phase),
            authoring.play(
                authoring.drive(q0),
                authoring.constant(
                    duration=Quantity(4, "ns"),
                    amplitude=Quantity(0.1, "arb"),
                ),
            ),
        ),
    )
    bound = authoring.bind(declaration, {"phase": Quantity(180, "deg")})
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("virtual-z-pulses"),
    )
    [shift, _play] = schedule(materialize_quantum_pulse_program(plan)).events
    assert isinstance(shift.instruction, ShiftPhase)
    assert shift.duration_seconds == 0
    assert shift.instruction.phase == Quantity(180, "deg").to("rad")


def test_program_element_ports_bind_every_logical_owner() -> None:
    formal_q0 = authoring.qubit("control")
    formal_q1 = authoring.qubit("target")
    formal_coupler = authoring.coupler("coupler")
    fixed = authoring.qubit("fixed")
    x = authoring.single_qubit_gate("x")
    cz = authoring.two_qubit_gate("cz")
    measure = authoring.measure(formal_q0, result="measure_iq")
    capture = authoring.acquire(
        formal_q1,
        duration=Quantity(8, "ns"),
        result="capture_iq",
    )
    envelope = authoring.constant(
        duration=Quantity(8, "ns"),
        amplitude=Quantity(0.1, "arb"),
    )
    declaration = authoring._close_program(
        "formal-elements",
        authoring.sequence(
            x(formal_q0),
            x(fixed),
            measure,
            authoring.play(authoring.drive(formal_q0), envelope),
            authoring.play(authoring.readout(formal_q1), envelope),
            capture,
            authoring._implement_gate(
                cz(formal_q0, formal_q1),
                authoring.play(authoring.flux(formal_coupler), envelope),
                resources=(formal_coupler,),
            ),
        ),
        elements=(formal_q0, formal_q1, formal_coupler),
    )

    bound = authoring.bind(
        declaration,
        {
            "control": "q2",
            "target": sc.entity_ref("q3", kind="logical_qubit"),
            "coupler": "coupler-q2-q3",
        },
    )
    gate, fixed_gate, measurement, drive, readout, acquisition, implemented = (
        bound.verified.operations
    )
    assert isinstance(gate, GateCall)
    assert gate.qubits == (QubitId("q2"),)
    assert isinstance(fixed_gate, GateCall)
    assert fixed_gate.qubits == (QubitId("fixed"),)
    assert isinstance(measurement, Measure)
    assert measurement.qubit == QubitId("q2")
    assert isinstance(drive, PulseBlock)
    [drive_leaf] = iter_pulse_leaves(drive.pulse_template.body)
    assert isinstance(drive_leaf, Play)
    assert drive_leaf.signal == DriveSignal(QubitId("q2"))
    assert isinstance(readout, PulseBlock)
    [readout_leaf] = iter_pulse_leaves(readout.pulse_template.body)
    assert isinstance(readout_leaf, Play)
    assert readout_leaf.signal == ReadoutSignal(QubitId("q3"))
    assert isinstance(acquisition, PulseBlock)
    [acquire_leaf] = iter_pulse_leaves(acquisition.pulse_template.body)
    assert isinstance(acquire_leaf, Acquire)
    assert acquire_leaf.signal == AcquireSignal(QubitId("q3"))
    assert isinstance(implemented, ImplementedGate)
    assert implemented.call.qubits == (QubitId("q2"), QubitId("q3"))
    [flux_leaf] = iter_pulse_leaves(implemented.pulse_template.body)
    assert isinstance(flux_leaf, Play)
    assert flux_leaf.signal == FluxSignal(CouplerId("coupler-q2-q3"))

    with pytest.raises(authoring.ProgramBindingError, match="logical_qubit"):
        authoring.bind(
            declaration,
            {
                "control": sc.entity_ref("c0", kind="logical_coupler"),
                "target": "q3",
                "coupler": "coupler-q2-q3",
            },
        )
