from collections.abc import Mapping
from dataclasses import replace

import pytest
from scopecat import Quantity

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import TargetCompileEntryId
from scopecat_quantum.acquisitions import INTEGRATED_IQ_RESULT, AcquisitionKind
from scopecat_quantum.circuits import Measure
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.pulse_recipes import PulseRecipeProfile
from scopecat_quantum.pulses import Acquire
from scopecat_quantum.realtime import ScheduledBlock
from scopecat_quantum.recipe_bindings import bind_measurement_pulse_recipe


def readout(target: q.Qubit, *, delay: Quantity) -> q.QuantumFragment:
    capture = q.acquire(target, duration=Quantity(16, "ns"), result="capture")
    return q.parallel(
        q.play(
            q.readout(target),
            q.constant(duration=Quantity(20, "ns"), amplitude=Quantity(0.1, "arb")),
        ),
        q.sequence(q.delay(capture.signal, delay), capture),
    )


@q.program
def twice(target: q.Qubit) -> q.QuantumFragment:
    return q.sequence(
        q.measure(target, result="first"),
        q.recipe_scope("candidate", q.measure(target, result="second")),
    )


def test_delayed_repeated_measurements_preserve_addresses_and_baseline() -> None:
    calls: list[str] = []

    def inputs(values: Mapping[str, int], measurement: Measure) -> Mapping[str, object]:
        calls.append(measurement.qubit.value)
        return {"delay": Quantity(values[measurement.qubit.value], "ns")}

    def unused(values: Mapping[str, int], measurement: Measure) -> Mapping[str, object]:
        raise AssertionError("unused acquisition kind must not read calibration")

    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_measurement_pulse_recipe(
            kind=AcquisitionKind.INTEGRATED_IQ, build=readout, inputs=inputs
        ),
        bind_measurement_pulse_recipe(
            kind=AcquisitionKind.CLASSIFIED_STATE, build=readout, inputs=unused
        ),
    )
    values = {"q0": 8}
    compiler = RecipeTargetCompiler(
        profile, values, scoped_parameters={"candidate": {"q0": 100}}
    )
    result = compiler.compile(
        twice, {"target": "q0"}, entry_id=TargetCompileEntryId("first")
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    program = result.entry.program.body.program
    captures = [
        event for event in program.events if isinstance(event.instruction, Acquire)
    ]
    assert [float(event.start_seconds) for event in captures] == pytest.approx(
        [8e-9, 32e-9]
    )
    assert (
        len(
            {
                event.instruction.slot_id
                for event in captures
                if isinstance(event.instruction, Acquire)
            }
        )
        == 2
    )
    assert float(program.duration_seconds) == pytest.approx(48e-9)
    assert set(calls) == {"q0"}
    values["q0"] = 12
    updated = compiler.compile(
        twice, {"target": "q0"}, entry_id=TargetCompileEntryId("updated")
    )
    assert isinstance(updated.entry.program.body, ScheduledBlock)
    assert float(updated.entry.program.body.program.duration_seconds) == pytest.approx(
        56e-9
    )
    assert float(program.duration_seconds) == pytest.approx(48e-9)


def test_missing_input_names_readout_and_object() -> None:
    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_measurement_pulse_recipe(
            kind=AcquisitionKind.INTEGRATED_IQ,
            build=readout,
            inputs=lambda _values, _measurement: {},
            id="readout",
        )
    )
    with pytest.raises(ValueError, match=r"readout.*q0.*input binding failed"):
        RecipeTargetCompiler[Mapping[str, int]](profile, {}).compile(
            twice, {"target": "q0"}, entry_id=TargetCompileEntryId("missing")
        )


def test_builder_must_honor_requested_contract() -> None:
    @q.program
    def experiment(target: q.Qubit) -> q.QuantumFragment:
        return q.measure(
            target, result="voltage", contract=replace(INTEGRATED_IQ_RESULT, unit="V")
        )

    profile = PulseRecipeProfile[Mapping[str, int]](
        bind_measurement_pulse_recipe(
            kind=AcquisitionKind.INTEGRATED_IQ,
            build=readout,
            inputs=lambda _values, _measurement: {"delay": Quantity(0, "ns")},
        )
    )
    with pytest.raises(ValueError, match="contract"):
        RecipeTargetCompiler[Mapping[str, int]](profile, {}).compile(
            experiment, {"target": "q0"}, entry_id=TargetCompileEntryId("contract")
        )
