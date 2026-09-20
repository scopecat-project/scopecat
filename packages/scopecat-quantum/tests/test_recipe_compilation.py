from dataclasses import dataclass

import pytest
from scopecat import EntityRef, Quantity

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import QubitId, TargetCompileEntryId
from scopecat_quantum.compilation import RecipeTargetCompiler
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
