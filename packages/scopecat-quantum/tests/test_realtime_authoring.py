from __future__ import annotations

from decimal import Decimal

import pytest
import scopecat as sc
from scopecat import Quantity

from scopecat_quantum import authoring
from scopecat_quantum._ids import PulseProgramId, TargetCompileEntryId
from scopecat_quantum.inspection import build_quantum_program_inspection_snapshot
from scopecat_quantum.program_targets import prepare_quantum_target_entry
from scopecat_quantum.programs import (
    Conditional,
    ParallelEach,
    Repeat,
    Sequence,
    materialize_quantum_pulse_program,
    plan_quantum_pulse_lowering,
)
from scopecat_quantum.pulse_implementations import ResolvedPulseImplementations
from scopecat_quantum.pulses import iter_pulse_leaves, schedule
from scopecat_quantum.realtime import (
    RealtimeConditional,
    RealtimeParallel,
    RealtimeRepeat,
    RealtimeSequence,
    ScheduledBlock,
)


def _classified_acquire(
    qubit: authoring.Qubit,
    *,
    result: str = "state",
    contract: authoring.QuantumResultContract = authoring.CLASSIFIED_STATE_RESULT,
) -> authoring.Acquisition:
    return authoring.acquire(
        qubit,
        duration=Quantity(8, "ns"),
        result=result,
        contract=contract,
    )


def _correction(qubit: authoring.Qubit) -> authoring.PulseFragment:
    return authoring.play(
        authoring.drive(qubit),
        authoring.constant(
            duration=Quantity(12, "ns"),
            amplitude=Quantity(0.2, "arb"),
        ),
    )


def _target_program(declaration: authoring.Program):
    bound = authoring.bind(declaration)
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId(f"{declaration.id}-pulses"),
    )
    return prepare_quantum_target_entry(
        TargetCompileEntryId("point-0"),
        plan,
    ).program


def test_switch_is_retained_from_authoring_through_target_lowering() -> None:
    qubit = authoring.qubit("q0")
    state = _classified_acquire(qubit)
    declaration = authoring._close_program(
        "active-reset",
        authoring.sequence(
            state,
            authoring.switch(state.result, {1: _correction(qubit)}),
        ),
    )

    bound = authoring.bind(declaration)
    assert isinstance(bound.program.body, Sequence)
    conditional = bound.program.body.operations[1]
    assert isinstance(conditional, Conditional)
    assert conditional.predicate == state.result.acquisition_slot_id
    assert [case for case, _body in conditional.cases] == [1]
    assert conditional.default is None

    target = _target_program(declaration)
    assert isinstance(target.body, RealtimeSequence)
    first, second = target.body.instructions
    assert isinstance(first, ScheduledBlock)
    assert isinstance(second, RealtimeConditional)
    assert target.envelope.has_variable_duration
    assert target.envelope.minimum_duration_seconds == Decimal("8e-9")
    assert target.envelope.worst_case_duration_seconds == Decimal("20e-9")
    assert target.envelope.worst_case_operation_count == 3
    assert target.envelope.worst_case_acquisition_count == 1
    assert target.acquisition_slots[0].contract == authoring.CLASSIFIED_STATE_RESULT


def test_result_repeat_uses_one_slot_and_one_local_round_axis() -> None:
    qubit = authoring.qubit("q0")
    contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
        authoring.QuantumResultDimension("round", "round", 3)
    )
    state = _classified_acquire(qubit, contract=contract)
    declaration = authoring._close_program(
        "feedback-rounds",
        authoring.repeat(
            authoring.sequence(
                state,
                authoring.switch(state.result, {1: _correction(qubit)}),
            ),
            3,
            result_dimension="round",
        ),
    )

    bound = authoring.bind(declaration)
    assert isinstance(bound.program.body, Repeat)
    assert bound.program.body.count == 3
    assert bound.program.body.result_dimension_id == "round"

    target = _target_program(declaration)
    assert isinstance(target.body, RealtimeRepeat)
    assert target.body.count == 3
    assert target.body.result_dimension_id == "round"
    assert len(target.acquisition_slots) == 1
    assert target.acquisition_slots[0].contract == contract
    assert target.envelope.worst_case_acquisition_count == 3


def test_result_repeat_accepts_the_same_bounded_count_input() -> None:
    qubit = authoring.qubit("q0")
    rounds = authoring.input(
        "rounds",
        sc.ScalarType(sc.IntType(minimum=1, maximum=8)),
    )
    contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
        authoring.QuantumResultDimension("round", "round", rounds)
    )
    state = _classified_acquire(qubit, contract=contract)
    declaration = authoring._close_program(
        "dynamic-feedback-rounds",
        authoring.repeat(
            authoring.sequence(
                state,
                authoring.switch(state.result, {1: _correction(qubit)}),
            ),
            rounds,
            result_dimension="round",
        ),
    )

    bound = authoring.bind(declaration, {"rounds": 5})
    assert isinstance(bound.program.body, Repeat)
    assert bound.program.body.count == 5
    assert bound.program.body.result_dimension_id == "round"
    [result] = declaration.results
    assert result.contract.dimensions[0].size is rounds


def test_static_result_free_repeat_keeps_the_existing_static_path() -> None:
    qubit = authoring.qubit("q0")
    declaration = authoring._close_program(
        "static-repeat",
        authoring.repeat(_correction(qubit), 2),
    )
    bound = authoring.bind(declaration)
    assert isinstance(bound.program.body, Repeat)
    assert bound.program.body.result_dimension_id is None

    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("static-repeat-pulses"),
    )
    pulses = materialize_quantum_pulse_program(plan)
    assert len(tuple(iter_pulse_leaves(pulses.body))) == 2
    target = prepare_quantum_target_entry(
        TargetCompileEntryId("point-0"),
        plan,
    ).program
    assert isinstance(target.body, ScheduledBlock)
    assert target.body.program == schedule(pulses)


def test_switch_and_repeat_reject_ambiguous_result_shapes() -> None:
    qubit = authoring.qubit("q0")
    integrated = authoring.measure(qubit, result="iq")
    with pytest.raises(ValueError, match="classified-state"):
        authoring.switch(integrated.result, {0: _correction(qubit)})

    state = _classified_acquire(qubit)
    with pytest.raises(TypeError, match="case states"):
        authoring.switch(state.result, {True: _correction(qubit)})
    with pytest.raises(ValueError, match="cannot produce acquisition"):
        authoring.switch(state.result, {0: _classified_acquire(qubit, result="next")})
    with pytest.raises(ValueError, match="require result_dimension"):
        authoring.repeat(state, 3)
    with pytest.raises(ValueError, match="must declare dimension 'round'"):
        authoring.repeat(state, 3, result_dimension="round")
    with pytest.raises(ValueError, match="result-free repeats"):
        authoring.repeat(_correction(qubit), 3, result_dimension="round")


def test_realtime_dataflow_requires_an_earlier_scalar_predicate() -> None:
    qubit = authoring.qubit("q0")
    state = _classified_acquire(qubit)
    feedback = authoring.switch(state.result, {1: _correction(qubit)})
    with pytest.raises(ValueError, match="produced earlier in the same sequence"):
        authoring._close_program(
            "feedback-before-measurement",
            authoring.sequence(feedback, state),
        )

    dimensioned_contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
        authoring.QuantumResultDimension("round", "round", 3)
    )
    dimensioned = _classified_acquire(qubit, contract=dimensioned_contract)
    with pytest.raises(ValueError, match="only scalar in the current iteration"):
        authoring._close_program(
            "array-feedback",
            authoring.sequence(
                dimensioned,
                authoring.switch(dimensioned.result, {1: _correction(qubit)}),
            ),
        )


def test_realtime_control_is_rejected_under_parallel_composition() -> None:
    qubit = authoring.qubit("q0")
    state = _classified_acquire(qubit)
    feedback = authoring.switch(state.result, {1: _correction(qubit)})
    with pytest.raises(ValueError, match="under parallel"):
        authoring.parallel(_correction(qubit), feedback)


def test_parallel_each_retains_entity_local_realtime_feedback() -> None:
    def feedback_body(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        state = _classified_acquire(qubit)
        return authoring.sequence(
            state,
            authoring.switch(state.result, {1: _correction(qubit)}),
        )

    qubits = authoring.QubitSet(
        _id="qubits",
        _item=authoring.qubit("qubits[]"),
    )
    declaration = authoring._close_program(
        "parallel-feedback",
        authoring.parallel_each(qubits, feedback_body),
        entity_sets=(qubits,),
    )
    bound = authoring.bind(declaration, {"qubits": ("q0", "q1")})
    assert isinstance(bound.program.body, ParallelEach)
    plan = plan_quantum_pulse_lowering(
        bound.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("parallel-feedback-pulses"),
    )
    target = prepare_quantum_target_entry(
        TargetCompileEntryId("parallel-feedback-point"),
        plan,
    ).program

    assert isinstance(target.body, RealtimeParallel)
    assert len(target.body.branches) == 2
    assert {
        next(segment for segment in slot.id.scope if segment in {"q0", "q1"})
        for slot in target.acquisition_slots
    } == {
        "q0",
        "q1",
    }


def test_result_producing_repeats_reject_mismatch_and_nesting() -> None:
    qubit = authoring.qubit("q0")
    wrong_size = _classified_acquire(
        qubit,
        contract=authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
            authoring.QuantumResultDimension("round", "round", 2)
        ),
    )
    with pytest.raises(ValueError, match="same count"):
        authoring.repeat(wrong_size, 3, result_dimension="round")

    nested_contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
        authoring.QuantumResultDimension("inner", "round", 2),
        authoring.QuantumResultDimension("outer", "round", 3),
    )
    nested_state = _classified_acquire(qubit, contract=nested_contract)
    inner = authoring.repeat(
        nested_state,
        2,
        result_dimension="inner",
    )
    with pytest.raises(ValueError, match="cannot be nested"):
        authoring.repeat(inner, 3, result_dimension="outer")


def test_draw_exposes_switch_cases_and_repeat_result_dimension() -> None:
    qubit = authoring.qubit("q0")
    contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
        authoring.QuantumResultDimension("round", "round", 2)
    )
    state = _classified_acquire(qubit, contract=contract)
    declaration = authoring._close_program(
        "draw-feedback",
        authoring.repeat(
            authoring.sequence(
                state,
                authoring.switch(
                    state.result,
                    {1: _correction(qubit)},
                    default=_correction(qubit),
                ),
            ),
            2,
            result_dimension="round",
        ),
    )

    rendered = declaration.draw()
    assert "repeat 2 result_dimension='round'" in rendered
    assert "switch $state" in rendered
    assert "case 1" in rendered
    assert "default" in rendered
    assert authoring.QUANTUM_PROGRAM_DIALECT_VERSION == "7"

    bound = authoring.bind(declaration)
    snapshot = build_quantum_program_inspection_snapshot(
        declaration,
        bound=bound,
    ).project()
    logical = next(layer for layer in snapshot.layers if layer.id == "logical")
    assert any(
        node.kind == "repeat" and node.label == "repeat x2 result_dimension='round'"
        for node in logical.nodes
    )
    switch_node = next(node for node in logical.nodes if node.kind == "switch")
    assert switch_node.label == "switch $state"
    assert {fact.id: fact.value for fact in switch_node.facts} == {
        "case_states": (1,),
        "has_default": True,
    }
