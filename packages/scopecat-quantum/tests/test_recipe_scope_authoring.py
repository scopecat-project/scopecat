from __future__ import annotations

import pytest
from scopecat import Quantity

from scopecat_quantum import authoring as q
from scopecat_quantum.circuits import Measure
from scopecat_quantum.gates import GateCall
from scopecat_quantum.programs import ImplementedGate, ParallelEach


def test_recipe_scope_is_local_and_preserves_explicit_implementations() -> None:
    x = q.single_qubit_gate("x")
    a, b = q.qubit("a"), q.qubit("b")

    @q.implementation(of=x, candidate="explicit")
    def explicit(qubit: q.Qubit) -> q.QuantumFragment:
        return q.play(
            q.drive(qubit),
            q.constant(duration=Quantity(10, "ns"), amplitude=Quantity(0.1, "arb")),
        )

    program = q._close_program(
        "scopes",
        q.sequence(
            x(a),
            q.recipe_scope(
                "outer",
                q.sequence(
                    x(a),
                    q.repeat(q.recipe_scope("inner", x(a)), 2),
                    q.parallel(x(a), x(b)),
                    explicit(a),
                    q.measure(a, result="iq"),
                ),
            ),
            x(a),
        ),
    )
    bound = q.bind(program)
    operations = tuple(bound.verified.iter_expanded_unresolved_operations())
    assert [o.recipe_scope for o in operations if isinstance(o, GateCall)] == [
        None,
        "outer",
        "inner",
        "inner",
        "outer",
        "outer",
        None,
    ]
    [implemented] = [
        o for o in bound.verified.operations if isinstance(o, ImplementedGate)
    ]
    assert implemented.call.recipe_scope is None
    assert implemented.candidate_id == "explicit"
    [measurement] = [o for o in operations if isinstance(o, Measure)]
    assert measurement.acquisition_slot_id.local_id == "iq"
    assert "recipe_scope 'inner'" in q.draw(program)


def test_recipe_scope_survives_fragment_expansion_and_retained_entity_mapping() -> None:
    x = q.single_qubit_gate("x")

    @q.fragment(
        envelope=q.ProgramFamilyEnvelope(
            allowed_gates=(x,),
            max_operations=2,
            max_depth=2,
        ),
    )
    def pair(qubit: q.Qubit) -> q.QuantumFragment:
        return q.sequence(x(qubit), q.recipe_scope("insert", x(qubit)))

    @q.program
    def program(qubits: q.QubitSet) -> q.QuantumFragment:
        return q.recipe_scope(
            "whole",
            q.parallel_each(qubits, lambda qubit: pair(qubit)),
        )

    bound = q.bind(program, {"qubits": ("q0", "q1")})
    assert isinstance(bound.program.body, ParallelEach)
    gates = tuple(bound.verified.iter_expanded_unresolved_operations())
    assert [
        (g.qubits[0].value, g.recipe_scope) for g in gates if isinstance(g, GateCall)
    ] == [
        ("q0", "whole"),
        ("q0", "insert"),
        ("q1", "whole"),
        ("q1", "insert"),
    ]


def test_recipe_scope_rejects_blank_names() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        q.recipe_scope(" ", q.single_qubit_gate("x")(q.qubit("q")))
