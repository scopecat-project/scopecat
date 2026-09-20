from dataclasses import dataclass

import pytest
from scopecat import EntityRef, Quantity

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import QubitId, TargetCompileEntryId
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.gates import GateCall
from scopecat_quantum.pulse_recipes import (
    PulseRecipeProfile,
    gate_pulse_recipe,
    map_qubit_pulse_recipes,
)
from scopecat_quantum.realtime import ScheduledBlock

X = q.single_qubit_gate("test.x")


@dataclass(frozen=True)
class Row:
    qubit: QubitId
    duration: Quantity


@gate_pulse_recipe(of=X, id="test.x.duration")
def pulse(row: Row, target: q.Qubit) -> q.QuantumFragment:
    return q.play(
        q.drive(target),
        q.constant(duration=row.duration, amplitude=Quantity(0.2, "arb")),
    )


PROFILE = PulseRecipeProfile[tuple[Row, ...]](
    map_qubit_pulse_recipes(
        rows=lambda rows: rows, qubit=lambda row: row.qubit, gates=(pulse,)
    )
)


@q.program(id="test.recipe-sequence")
def sequence(target: q.Qubit) -> q.QuantumFragment:
    return q.sequence(X(target), X(target))


def test_compiler_keeps_snapshot_and_point_evidence_separate() -> None:
    first = RecipeTargetCompiler(PROFILE, (Row(QubitId("q0"), Quantity(16, "ns")),))
    candidate = RecipeTargetCompiler(PROFILE, (Row(QubitId("q0"), Quantity(24, "ns")),))
    for compiler, duration in ((first, 32), (candidate, 48), (first, 32)):
        result = compiler.compile(
            sequence,
            {"target": EntityRef(id="q0", kind="logical_qubit")},
            entry_id=TargetCompileEntryId(f"point-{duration}"),
            inspect=True,
        )
        assert isinstance(result.entry.program.body, ScheduledBlock)
        assert float(
            result.entry.program.body.program.duration_seconds
        ) == pytest.approx(duration * 1e-9)
        assert result.inspection is not None
        assert result.inspection.snapshot_id == result.entry.id.value
    plain = first.compile(
        sequence,
        {"target": EntityRef(id="q0", kind="logical_qubit")},
        entry_id=TargetCompileEntryId("plain"),
    )
    assert plain.inspection is None


def test_budget_rejects_work_before_recipe_invocation() -> None:
    compiler = RecipeTargetCompiler(PROFILE, (), max_expanded_operations=1)
    with pytest.raises(ValueError, match=r"(?i)(expan|budget|limit)"):
        compiler.compile(
            sequence,
            {"target": EntityRef(id="q0", kind="logical_qubit")},
            entry_id=TargetCompileEntryId("too-large"),
        )


def test_candidate_scope_selects_only_inserted_gates_and_preserves_inspection() -> None:
    @q.program
    def interleaved(target: q.Qubit) -> q.QuantumFragment:
        return q.sequence(X(target), q.recipe_scope("candidate", X(target)), X(target))

    compiler = RecipeTargetCompiler(
        PROFILE,
        (Row(QubitId("q0"), Quantity(16, "ns")),),
        scoped_parameters={"candidate": (Row(QubitId("q0"), Quantity(24, "ns")),)},
    )
    result = compiler.compile(
        interleaved,
        {"target": EntityRef(id="q0", kind="logical_qubit")},
        entry_id=TargetCompileEntryId("interleaved"),
        inspect=True,
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    assert float(result.entry.program.body.program.duration_seconds) == pytest.approx(
        56e-9
    )
    assert [
        op.recipe_scope
        for op in result.bound.verified.logical_operations
        if isinstance(op, GateCall)
    ] == [
        None,
        "candidate",
        None,
    ]
    assert result.inspection is not None
    logical = next(layer for layer in result.inspection.layers if layer.id == "logical")
    assert [
        fact.value
        for ordinal in range(logical.nodes.node_count)
        for fact in logical.nodes.node_at(ordinal, None).facts
        if fact.id == "recipe_scope"
    ] == ["candidate"]
    # The same compiler/cache must still compile an entirely baseline point.
    reference = compiler.compile(
        sequence,
        {"target": EntityRef(id="q0", kind="logical_qubit")},
        entry_id=TargetCompileEntryId("reference"),
    )
    assert isinstance(reference.entry.program.body, ScheduledBlock)
    assert float(
        reference.entry.program.body.program.duration_seconds
    ) == pytest.approx(32e-9)


def test_missing_candidate_scope_fails_instead_of_using_baseline() -> None:
    @q.program
    def candidate(target: q.Qubit) -> q.QuantumFragment:
        return q.recipe_scope("candidate", X(target))

    compiler = RecipeTargetCompiler(PROFILE, (Row(QubitId("q0"), Quantity(16, "ns")),))
    with pytest.raises(
        ValueError, match="no pulse recipe parameters for scope 'candidate'"
    ):
        compiler.compile(
            candidate,
            {"target": EntityRef(id="q0", kind="logical_qubit")},
            entry_id=TargetCompileEntryId("missing"),
        )


def test_point_scopes_reuse_compiler_without_leaking_into_later_points() -> None:
    @q.program
    def candidate(target: q.Qubit) -> q.QuantumFragment:
        return q.sequence(X(target), q.recipe_scope("candidate", X(target)))

    compiler = RecipeTargetCompiler(
        PROFILE,
        (Row(QubitId("q0"), Quantity(16, "ns")),),
        scoped_parameters={"candidate": (Row(QubitId("q0"), Quantity(20, "ns")),)},
    )
    for duration in (24, 32, None):
        result = compiler.compile(
            candidate,
            {"target": EntityRef(id="q0", kind="logical_qubit")},
            entry_id=TargetCompileEntryId(f"point-{duration}"),
            scoped_parameters=None
            if duration is None
            else {"candidate": (Row(QubitId("q0"), Quantity(duration, "ns")),)},
        )
        assert isinstance(result.entry.program.body, ScheduledBlock)
        assert float(
            result.entry.program.body.program.duration_seconds
        ) == pytest.approx((16 + (20 if duration is None else duration)) * 1e-9)
