"""Static pulse recipes mapped over point-effective compiler parameters."""

from __future__ import annotations

import math

from scopecat import Quantity
from scopecat_quantum import authoring as quantum
from scopecat_quantum.pulse_recipes import (
    PulseRecipeProfile,
    gate_pulse_recipe,
    map_qubit_pulse_recipes,
)
from scopecat_quantum.standard_gates import X90, XM90

from reference_lab.quantum_compilation.compiler_parameters import (
    QuantumCompilerParameters,
    QubitPulseParameters,
)


def _drag_gate_fragment(
    row: QubitPulseParameters,
    target: quantum.Qubit,
    *,
    phase: Quantity,
) -> quantum.QuantumFragment:
    return quantum.play(
        quantum.drive(target),
        quantum.drag(
            duration=row.quarter_turn_duration,
            amplitude=row.quarter_turn_amplitude,
            sigma=row.quarter_turn_sigma,
            beta=row.drag_beta,
            phase=phase,
        ),
    )


@gate_pulse_recipe(of=X90, id="x90.drag")
def x90_pulse_recipe(
    row: QubitPulseParameters,
    target: quantum.Qubit,
) -> quantum.QuantumFragment:
    return _drag_gate_fragment(row, target, phase=Quantity(0, "rad"))


@gate_pulse_recipe(of=XM90, id="xm90.drag")
def xm90_pulse_recipe(
    row: QubitPulseParameters,
    target: quantum.Qubit,
) -> quantum.QuantumFragment:
    return _drag_gate_fragment(row, target, phase=Quantity(math.pi, "rad"))


QUANTUM_PULSE_PROFILE = PulseRecipeProfile[QuantumCompilerParameters](
    map_qubit_pulse_recipes(
        rows=lambda parameters: parameters.qubits,
        qubit=lambda row: row.qubit,
        gates=(
            x90_pulse_recipe,
            xm90_pulse_recipe,
        ),
    )
)


__all__ = [
    "QUANTUM_PULSE_PROFILE",
    "x90_pulse_recipe",
    "xm90_pulse_recipe",
]
