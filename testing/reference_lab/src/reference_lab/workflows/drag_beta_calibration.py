"""DRAG-beta program, pulse candidate, and accepted gate calibrations."""

from __future__ import annotations

import math
from typing import Annotated

from scopecat import IntType, Quantity, QuantityType, ScalarType
from scopecat_quantum import authoring as q
from scopecat_quantum.standard_gates import X90, XM90

POSITIVE_CANDIDATE_ID = "x90.drag.plus"
NEGATIVE_CANDIDATE_ID = "x90.drag.minus"

_PULSE_DURATION = Quantity(16, "ns")
_PULSE_AMPLITUDE = Quantity(0.2, "arb")
_PULSE_SIGMA = Quantity(4, "ns")
_READOUT_DURATION = Quantity(8, "ns")


@q.pulse_template(id="drag-beta.gate-pulse")
def drag_gate_pulse(
    qubit: q.Qubit,
    beta: Annotated[Quantity, QuantityType(unit="ns")],
    phase: Annotated[Quantity, QuantityType(unit="rad")],
) -> q.QuantumFragment:
    return q.play(
        q.drive(qubit),
        q.drag(
            duration=_PULSE_DURATION,
            amplitude=_PULSE_AMPLITUDE,
            sigma=_PULSE_SIGMA,
            beta=beta,
            phase=phase,
        ),
    )


@q.pulse_template(id="drag-beta.readout-stimulus")
def drag_readout_pulse(qubit: q.Qubit) -> q.QuantumFragment:
    return q.play(
        q.readout(qubit),
        q.constant(
            duration=_READOUT_DURATION,
            amplitude=Quantity(0.25, "arb"),
        ),
    )


@q.implementation(
    of=X90,
    candidate=POSITIVE_CANDIDATE_ID,
    id="drag-beta.x90-candidate",
)
def candidate_x90(
    qubit: q.Qubit,
    beta: Annotated[Quantity, QuantityType(unit="ns")],
) -> q.QuantumFragment:
    return drag_gate_pulse(
        qubit,
        beta=beta,
        phase=Quantity(0, "rad"),
    )


@q.implementation(
    of=XM90,
    candidate=NEGATIVE_CANDIDATE_ID,
    id="drag-beta.xm90-candidate",
)
def candidate_xm90(
    qubit: q.Qubit,
    beta: Annotated[Quantity, QuantityType(unit="ns")],
) -> q.QuantumFragment:
    return drag_gate_pulse(
        qubit,
        beta=beta,
        phase=Quantity(math.pi, "rad"),
    )


@q.program(id="drag-beta-rough-calibration")
def drag_beta_program(
    qubit: q.Qubit,
    amplification: Annotated[int, IntType(minimum=1)],
    beta: Annotated[Quantity, ScalarType(QuantityType(unit="ns"))],
) -> q.QuantumFragment:
    """Amplify coherent DRAG error between accepted X90 and Xm90 references.

    Keeping beta and amplification as ports reuses one program across the scan;
    repeating the candidate pair makes the error population scale with ``N^2``.
    """

    candidate_pair = q.sequence(
        candidate_x90(qubit, beta=beta),
        candidate_xm90(qubit, beta=beta),
    )
    capture = q.acquire(
        qubit,
        duration=_READOUT_DURATION,
        result="iq_shots",
    )
    return q.sequence(
        X90(qubit),
        q.repeat(candidate_pair, amplification),
        XM90(qubit),
        q.parallel(
            drag_readout_pulse(qubit),
            capture,
        ),
    )


__all__ = [
    "NEGATIVE_CANDIDATE_ID",
    "POSITIVE_CANDIDATE_ID",
    "candidate_x90",
    "candidate_xm90",
    "drag_beta_program",
    "drag_gate_pulse",
    "drag_readout_pulse",
]
