from collections.abc import Mapping

import pytest
import scopecat as sc
from scopecat.kernel.content_identity import content_fingerprint
from scopecat.records.parameter import ParameterSnapshot

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import TargetCompileEntryId
from scopecat_quantum._recipe_definition_identity import recipe_definition_identity
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.gates import GateCall
from scopecat_quantum.pulse_recipes import PulseRecipeProfile
from scopecat_quantum.realtime import ScheduledBlock
from scopecat_quantum.recipe_bindings import bind_gate_pulse_recipe
from scopecat_quantum.standard_gates import X90


def pulse(target: q.Qubit, *, duration: sc.Quantity) -> q.QuantumFragment:
    return q.play(
        q.drive(target),
        q.constant(duration=duration, amplitude=sc.Quantity(0.1, "arb")),
    )


def profile(duration: float) -> PulseRecipeProfile[ParameterSnapshot]:
    def inputs(_parameters: ParameterSnapshot, _call: GateCall) -> Mapping[str, object]:
        return {"duration": sc.Quantity(duration, "ns")}

    return PulseRecipeProfile(
        bind_gate_pulse_recipe(of=X90, build=pulse, inputs=inputs)
    )


@q.program
def sequence(target: q.Qubit) -> q.QuantumFragment:
    return q.sequence(
        X90(target), q.acquire(target, duration=sc.Quantity(8, "ns"), result="iq")
    )


def test_call_selection_changes_identity_and_compilation() -> None:
    first = sequence("q0").with_recipes(profile(24)).with_shots(4)
    second = sequence("q0").with_shots(4).with_recipes(profile(32))
    assert sequence.recipes is None
    assert first.program.recipes is not None
    assert content_fingerprint(first.program) != content_fingerprint(second.program)
    assert content_fingerprint(first.program.recipes) == content_fingerprint(
        profile_call(24).program.recipes
    )
    compiler = RecipeTargetCompiler[ParameterSnapshot](
        None, sc.parameter_snapshot("empty", tables={})
    )
    durations: list[float] = []
    for call in (first, second, first):
        compiled = compiler.compile(
            call.program, dict(call.arguments), entry_id=TargetCompileEntryId("entry")
        )
        assert isinstance(compiled.entry.program.body, ScheduledBlock)
        durations.append(float(compiled.entry.program.body.program.duration_seconds))
    assert durations == pytest.approx([32e-9, 40e-9, 32e-9])
    assert first.with_compiler_inputs().program.recipes is first.program.recipes


def profile_call(duration: float) -> q.QuantumProgramCall:
    return sequence("q0").with_recipes(profile(duration))


def test_missing_profile_has_author_facing_error() -> None:
    compiler = RecipeTargetCompiler[ParameterSnapshot](
        None, sc.parameter_snapshot("empty", tables={})
    )
    with pytest.raises(ValueError, match="implementation"):
        _ = compiler.compile(
            sequence, {"target": "q0"}, entry_id=TargetCompileEntryId("entry")
        )


def test_direct_pulse_program_needs_no_recipe_profile() -> None:
    @q.program
    def direct(target: q.Qubit) -> q.QuantumFragment:
        return q.sequence(
            pulse(target, duration=sc.Quantity(24, "ns")),
            q.acquire(target, duration=sc.Quantity(8, "ns"), result="iq"),
        )

    compiler = RecipeTargetCompiler[ParameterSnapshot](
        None, sc.parameter_snapshot("empty", tables={})
    )
    result = compiler.compile(
        direct, {"target": "q0"}, entry_id=TargetCompileEntryId("direct")
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    assert float(result.entry.program.body.program.duration_seconds) == pytest.approx(
        32e-9
    )


def test_captured_sequence_type_is_part_of_recipe_identity() -> None:
    assert recipe_definition_identity([24]) != recipe_definition_identity((24,))
