"""Reusable single- and two-qubit Ramsey pulse programs."""

from __future__ import annotations

from typing import Annotated, cast

from scopecat import Quantity, QuantityType, ScalarType
from scopecat_quantum import authoring as q

from reference_lab.workflows.drag_beta_calibration import (
    drag_gate_pulse,
    drag_readout_pulse,
)

_READOUT_DURATION = Quantity(8, "ns")


def _ramsey_branch(
    qubit: q.Qubit,
    *,
    delay: q.QuantumQuantity,
    phase: q.QuantumQuantity,
    result: str,
) -> q.QuantumFragment:
    capture = q.acquire(
        qubit,
        duration=_READOUT_DURATION,
        result=result,
    )
    return q.sequence(
        drag_gate_pulse(
            qubit,
            beta=Quantity(0.5, "ns"),
            phase=Quantity(0.0, "rad"),
        ),
        q.delay(q.drive(qubit), delay),
        drag_gate_pulse(
            qubit,
            beta=Quantity(0.5, "ns"),
            phase=cast("Quantity", phase),
        ),
        q.parallel(drag_readout_pulse(qubit), capture),
    )


@q.program(id="reference-lab.ramsey")
def ramsey_program(
    qubit: q.Qubit,
    delay: Annotated[Quantity, ScalarType(QuantityType(unit="ns"))],
    phase: Annotated[Quantity, ScalarType(QuantityType(unit="rad"))],
) -> q.QuantumFragment:
    """Run one phase-advanced Ramsey sequence and integrated-IQ readout."""

    return _ramsey_branch(
        qubit,
        delay=delay,
        phase=phase,
        result="iq_shots",
    )


@q.program(id="reference-lab.parallel-two-qubit-ramsey")
def parallel_two_qubit_ramsey_program(
    q0: q.Qubit,
    q1: q.Qubit,
    delay: Annotated[Quantity, ScalarType(QuantityType(unit="ns"))],
    q0_phase: Annotated[Quantity, ScalarType(QuantityType(unit="rad"))],
    q1_phase: Annotated[Quantity, ScalarType(QuantityType(unit="rad"))],
) -> q.QuantumFragment:
    """Run Ramsey and readout concurrently on two independently routed qubits."""

    return q.parallel(
        _ramsey_branch(
            q0,
            delay=delay,
            phase=q0_phase,
            result="q0_iq_shots",
        ),
        _ramsey_branch(
            q1,
            delay=delay,
            phase=q1_phase,
            result="q1_iq_shots",
        ),
    )


@q.program(id="reference-lab.topology-scaled-ramsey")
def topology_scaled_ramsey_program(
    targets: q.QubitSet,
    delay: Annotated[Quantity, ScalarType(QuantityType(unit="ns"))],
    phase: Annotated[Quantity, ScalarType(QuantityType(unit="rad"))],
) -> q.QuantumFragment:
    """Run the same Ramsey branch over a topology-selected qubit set."""

    return q.parallel_each(
        targets,
        lambda qubit: _ramsey_branch(
            qubit,
            delay=delay,
            phase=phase,
            result="iq_shots",
        ),
    )


__all__ = [
    "parallel_two_qubit_ramsey_program",
    "ramsey_program",
    "topology_scaled_ramsey_program",
]
