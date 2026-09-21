from collections.abc import Mapping

import pytest
from scopecat import Quantity, QuantityType

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import CouplerId, TargetCompileEntryId
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.gates import GateCall
from scopecat_quantum.pulse_recipes import PulseRecipeProfile
from scopecat_quantum.realtime import ScheduledBlock
from scopecat_quantum.recipe_bindings import bind_gate_pulse_recipe
from scopecat_quantum.standard_gates import CZ, X90, X


def pulse(target: q.Qubit, *, duration: Quantity) -> q.QuantumFragment:
    return q.play(
        q.drive(target), q.constant(duration=duration, amplitude=Quantity(0.2, "arb"))
    )


def inputs(values: Mapping[str, int], call: GateCall) -> Mapping[str, object]:
    return {"duration": Quantity(values[call.qubits[0].value], "ns")}


@q.program
def half(target: q.Qubit) -> q.QuantumFragment:
    return X90(target)


def duration[T](compiler: RecipeTargetCompiler[T], program: q.Program = half) -> float:
    result = compiler.compile(
        program, {"target": "q0"}, entry_id=TargetCompileEntryId("point")
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    return float(result.entry.program.body.program.duration_seconds)


def test_only_matching_operation_and_object_resolve_inputs() -> None:
    def unused(values: Mapping[str, int], call: GateCall) -> Mapping[str, object]:
        raise AssertionError("unused X calibration was read")

    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_gate_pulse_recipe(of=X90, build=pulse, inputs=inputs),
        bind_gate_pulse_recipe(of=X, build=pulse, inputs=unused),
    )
    assert duration(RecipeTargetCompiler(profile, {"q0": 24})) == pytest.approx(24e-9)


def test_source_changes_use_content_and_do_not_rewrite_prepared_results() -> None:
    values: dict[str, int] = {"q0": 24}
    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_gate_pulse_recipe(of=X90, build=pulse, inputs=inputs),
    )
    compiler = RecipeTargetCompiler(profile, values)
    first = compiler.compile(
        half, {"target": "q0"}, entry_id=TargetCompileEntryId("first")
    )
    values["q0"] = 32
    assert duration(compiler) == pytest.approx(32e-9)
    assert isinstance(first.entry.program.body, ScheduledBlock)
    assert float(first.entry.program.body.program.duration_seconds) == pytest.approx(
        24e-9
    )


def test_candidate_scope_selects_inputs_without_changing_baseline() -> None:
    @q.program
    def candidate(target: q.Qubit) -> q.QuantumFragment:
        return q.sequence(X90(target), q.recipe_scope("candidate", X90(target)))

    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_gate_pulse_recipe(of=X90, build=pulse, inputs=inputs),
    )
    compiler = RecipeTargetCompiler(
        profile, {"q0": 24}, scoped_parameters={"candidate": {"q0": 32}}
    )
    assert duration(compiler, candidate) == pytest.approx(56e-9)
    assert duration(compiler) == pytest.approx(24e-9)
    with pytest.raises(ValueError, match="no parameters for scope 'candidate'"):
        duration(RecipeTargetCompiler(profile, {"q0": 24}), candidate)


def test_binding_failure_names_implementation_and_operand() -> None:
    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_gate_pulse_recipe(of=X90, build=pulse, inputs=inputs, id="half-turn"),
    )
    with pytest.raises(ValueError, match=r"half-turn.*q0.*input binding failed"):
        duration(RecipeTargetCompiler[Mapping[str, int]](profile, {}))


def test_gate_arguments_are_distinct_from_calibration_inputs() -> None:
    probe = q.single_qubit_gate(
        "probe", parameters={"duration": QuantityType(unit="ns")}
    )

    def build(
        target: q.Qubit, *, duration: Quantity, amplitude: Quantity
    ) -> q.QuantumFragment:
        return q.play(
            q.drive(target), q.constant(duration=duration, amplitude=amplitude)
        )

    @q.program
    def experiment(target: q.Qubit) -> q.QuantumFragment:
        return probe(target, duration=Quantity(16, "ns"))

    def resolve(values: Mapping[str, int], call: GateCall) -> Mapping[str, object]:
        return {"amplitude": Quantity(0.1, "arb")}

    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_gate_pulse_recipe(of=probe, build=build, inputs=resolve),
    )
    assert duration(
        RecipeTargetCompiler[Mapping[str, int]](profile, {}), experiment
    ) == pytest.approx(16e-9)
    conflicting = PulseRecipeProfile[Mapping[str, int]](
        bind_gate_pulse_recipe(of=probe, build=build, inputs=inputs),
    )
    with pytest.raises(ValueError, match="cannot override gate arguments"):
        duration(RecipeTargetCompiler(conflicting, {"q0": 32}), experiment)


def test_resources_are_selected_separately_from_calibration() -> None:
    def build(
        left: q.Qubit, right: q.Qubit, link: q.Coupler, *, duration: Quantity
    ) -> q.QuantumFragment:
        return q.play(
            q.flux(link), q.constant(duration=duration, amplitude=Quantity(0.1, "V"))
        )

    @q.program
    def experiment(target: q.Qubit, other: q.Qubit) -> q.QuantumFragment:
        return CZ(target, other)

    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_gate_pulse_recipe(
            of=CZ,
            build=build,
            inputs=inputs,
            resources=lambda _values, _call: (CouplerId("c01"),),
        ),
    )
    result = RecipeTargetCompiler(profile, {"q0": 40}).compile(
        experiment,
        {"target": "q0", "other": "q1"},
        entry_id=TargetCompileEntryId("coupled"),
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    assert float(result.entry.program.body.program.duration_seconds) == pytest.approx(
        40e-9
    )
