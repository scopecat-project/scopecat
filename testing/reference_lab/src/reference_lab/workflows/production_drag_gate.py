"""Production X90 execution with config-bound program and calibration inputs."""

from __future__ import annotations

from typing import Annotated

import scopecat as sc
from scopecat import Quantity, QuantityType
from scopecat_quantum import authoring as quantum
from scopecat_quantum.measurement_computes import BinaryIqProbabilityProducts
from scopecat_quantum.standard_gates import X90, XM90

from reference_lab.parameters import QubitParameters
from reference_lab.quantum_runner import quantum_capture
from reference_lab.workflows.drag_beta_calibration import (
    drag_gate_pulse,
    drag_readout_pulse,
)

PRODUCTION_DRAG_GATE_SHOTS = 32


@quantum.implementation(of=X90, id="production-drag-x90.implementation")
def production_x90(
    qubit: quantum.Qubit,
    drag_beta: Annotated[Quantity, QuantityType(unit="ns")],
) -> quantum.QuantumFragment:
    return drag_gate_pulse(
        qubit,
        beta=drag_beta,
        phase=Quantity(0, "rad"),
    )


@quantum.program(id="production-drag-x90")
def production_drag_program(
    qubit: quantum.Qubit,
    drag_beta: Annotated[Quantity, QuantityType(unit="ns")],
) -> quantum.QuantumFragment:
    """Declare a production X90 followed by one accepted Xm90 calibration."""

    capture = quantum.acquire(
        qubit,
        duration=Quantity(8, "ns"),
        result="iq_shots",
    )
    return quantum.sequence(
        production_x90(qubit, drag_beta=drag_beta),
        XM90(qubit),
        quantum.parallel(drag_readout_pulse(qubit), capture),
    )


@sc.experiment
def production_drag_experiment(
    experiment: sc.ExperimentContext,
) -> BinaryIqProbabilityProducts:
    return experiment.use(
        quantum_capture(
            production_drag_program(
                qubit="q0",
                drag_beta=sc.parameter_ref(
                    QubitParameters.drag_beta,
                    sc.EntityRef(id="q0", kind="logical_qubit"),
                ),
            ).with_shots(PRODUCTION_DRAG_GATE_SHOTS)
        )
    )


__all__ = [
    "PRODUCTION_DRAG_GATE_SHOTS",
    "production_drag_experiment",
    "production_drag_program",
    "production_x90",
]
