"""Point-local coherent IQ mean using the reference lab's existing capture path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

import numpy as np
import scopecat as sc

from reference_lab.parameters import QUBITS
from reference_lab.quantum_runner import prepare_quantum_hardware
from reference_lab.workflows.ramsey import ramsey_program


def _iq_mean(
    values: object,
) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="ratio"))]:
    """Reduce the acquired shots to one complex value, without an aggregate axis."""
    return complex(np.mean(np.asarray(values, dtype=np.complex128)))


@dataclass(frozen=True, slots=True)
class CoherentRamseyDataset:
    delay: sc.CoordinateRef[sc.Quantity]
    phase: sc.CoordinateRef[sc.Quantity]
    iq_mean: sc.ProductRef[complex]


@sc.experiment(id="reference_lab.coherent_ramsey")
def coherent_ramsey(experiment: sc.ExperimentContext) -> CoherentRamseyDataset:
    """Record a complex scalar at each delay/phase grid point."""
    delay = experiment.scan("delay", (sc.Quantity(8, "ns"), sc.Quantity(88, "ns")))
    phase = experiment.scan("phase", (sc.Quantity(0, "rad"), sc.Quantity(1, "rad")))
    prepare_quantum_hardware(experiment)
    capture = experiment.use(
        ramsey_program(
            qubit="q0",
            delay=delay,
            phase=phase,
        )
        .with_shots(16)
        .with_compiler_inputs(qubits=QUBITS.ref)
    )
    mean = cast(
        "sc.ProductRef[complex]",
        experiment.compute("iq_mean", fn=_iq_mean, inputs={"values": capture.iq_shots}),
    )
    return CoherentRamseyDataset(delay=delay, phase=phase, iq_mean=mean)
