"""Run with ``uv run python packages/scopecat-quantum/examples/quantity_recipe.py``."""

from typing import Annotated

from scopecat import Quantity, QuantityType

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import PulseProgramId
from scopecat_quantum.programs import (
    materialize_quantum_pulse_program,
    plan_quantum_pulse_lowering,
)
from scopecat_quantum.pulse_implementations import ResolvedPulseImplementations
from scopecat_quantum.pulses import schedule
from scopecat_quantum.standard_gates import X90


@q.implementation(of=X90, id="my-lab.x90")
def x90(
    qubit: q.Qubit,
    half_pi_amplitude: Annotated[q.QuantumQuantity, QuantityType(unit="arb")],
    duration: Annotated[q.QuantumQuantity, QuantityType(unit="ns")],
    phase: Annotated[q.QuantumQuantity, QuantityType(unit="rad")],
) -> q.QuantumFragment:
    # half_pi_amplitude is independently calibrated, not pi_amplitude / 2.
    return q.play(
        q.drive(qubit),
        q.gaussian(
            duration=duration,
            amplitude=half_pi_amplitude,
            sigma=duration / 4,
            phase=phase + Quantity(90, "deg"),
        ),
    )


@q.program(id="my-lab.recipe-preview")
def experiment(
    qubit: q.Qubit,
    duration: Annotated[q.QuantumQuantity, QuantityType(unit="ns")],
) -> q.QuantumFragment:
    return x90(qubit, Quantity(0.18, "arb"), duration, Quantity(0, "rad"))


if __name__ == "__main__":
    print(experiment.draw())
    for duration in (16, 24):
        bound = q.bind(
            experiment, {"qubit": "q0", "duration": Quantity(duration, "ns")}
        )
        lowered = plan_quantum_pulse_lowering(
            bound.verified,
            ResolvedPulseImplementations(),
            output_id=PulseProgramId("preview"),
        )
        print(schedule(materialize_quantum_pulse_program(lowered)))
