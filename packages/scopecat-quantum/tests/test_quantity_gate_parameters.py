from dataclasses import dataclass
from typing import Annotated

import pytest
from pydantic import TypeAdapter
from scopecat import EntityRef, Quantity, QuantityType
from scopecat.kernel.content_identity import content_fingerprint

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import QubitId, TargetCompileEntryId
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.gates import GateDefinition
from scopecat_quantum.pulse_implementations import GatePulseImplementationArgument
from scopecat_quantum.pulse_recipes import (
    PulseRecipeProfile,
    gate_pulse_recipe,
    map_qubit_pulse_recipes,
)
from scopecat_quantum.realtime import ScheduledBlock

DURATION = QuantityType(unit="ns", minimum=0)
PROBE = q.single_qubit_gate(
    "test.drive-probe",
    parameters={"duration": DURATION, "amplitude": QuantityType(unit="arb")},
)


@dataclass(frozen=True)
class Row:
    qubit: QubitId


@gate_pulse_recipe(of=PROBE, id="test.drive-probe.recipe")
def probe_recipe(
    row: Row, target: q.Qubit, *, duration: Quantity, amplitude: Quantity
) -> q.QuantumFragment:
    return q.play(q.drive(target), q.constant(duration=duration, amplitude=amplitude))


@q.program(id="test.quantity-probe")
def probe(
    target: q.Qubit,
    duration: Annotated[Quantity, DURATION],
    amplitude: Annotated[Quantity, QuantityType(unit="arb")],
) -> q.QuantumFragment:
    return PROBE(target, duration=duration, amplitude=amplitude)


PROFILE = PulseRecipeProfile[tuple[Row, ...]](
    map_qubit_pulse_recipes(
        rows=lambda rows: rows, qubit=lambda row: row.qubit, gates=(probe_recipe,)
    )
)


def test_quantities_bind_and_reach_recipe_without_unit_stripping() -> None:
    compiler = RecipeTargetCompiler(PROFILE, (Row(QubitId("q0")),))
    results = [
        compiler.compile(
            probe,
            {
                "target": EntityRef(id="q0", kind="logical_qubit"),
                "duration": duration,
                "amplitude": Quantity(0.2, "arb"),
            },
            entry_id=TargetCompileEntryId("same-point"),
        )
        for duration in (Quantity(100, "ns"), Quantity(0.1, "us"))
    ]
    assert results[0].bound.verified.operations == results[1].bound.verified.operations
    assert content_fingerprint(results[0].entry) == content_fingerprint(
        results[1].entry
    )
    body = results[0].entry.program.body
    assert isinstance(body, ScheduledBlock)
    assert float(body.program.duration_seconds) == pytest.approx(1e-7)
    assert GatePulseImplementationArgument(
        "duration", Quantity(100, "ns")
    ) == GatePulseImplementationArgument("duration", Quantity(0.1, "us"))


@pytest.mark.parametrize("value", [Quantity(1, "MHz"), Quantity(-1, "ns"), 16.0])
def test_literal_gate_arguments_require_declared_dimension_and_range(
    value: Quantity | float,
) -> None:
    with pytest.raises(TypeError, match="duration"):
        PROBE(q.qubit("q0"), duration=value, amplitude=Quantity(0.2, "arb"))


def test_symbolic_frequency_cannot_feed_duration() -> None:
    with pytest.raises(TypeError, match="duration"):

        @q.program(id="test.wrong-dimension")
        def invalid(
            target: q.Qubit, frequency: Annotated[Quantity, QuantityType(unit="MHz")]
        ) -> q.QuantumFragment:
            return PROBE(target, duration=frequency, amplitude=Quantity(0.2, "arb"))


def test_voltage_implementation_keys_normalize_linear_units() -> None:
    assert GatePulseImplementationArgument(
        "voltage", Quantity(1, "V")
    ) == GatePulseImplementationArgument("voltage", Quantity(1000, "mV"))


def test_quantity_gate_definition_round_trips_current_contract() -> None:
    adapter = TypeAdapter(GateDefinition)
    definition = PROBE.definition
    assert adapter.validate_json(adapter.dump_json(definition)) == definition
