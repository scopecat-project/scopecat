"""Explicit per-acquisition IQ retention in a single-qubit Ramsey experiment."""

from __future__ import annotations

from typing import Literal

import scopecat as sc
from reference_lab.parameters import QubitParameters
from reference_lab.quantum_runner import prepare_quantum_hardware
from reference_lab.workflows.ramsey import ramsey_program
from scopecat_quantum.measurement_computes import iq_shot_statistics


@sc.experiment(id="reference_lab.retained_ramsey")
def retained_ramsey(
    experiment: sc.ExperimentContext,
    *,
    retain: Literal["raw", "stats", "both"],
    shots: int = 16,
) -> dict[str, object]:
    """Choose durable leaves before acquisition; never delete earlier run data.

    Stats-only retains a count and coherent mean at each delay/phase point.
    It cannot recover distributions, classify shots again, or estimate variance.
    The reference instrument is synthetic, not hardware qualification.
    """
    if retain not in {"raw", "stats", "both"}:
        raise ValueError("retain must be raw, stats, or both")
    delay = experiment.scan("delay", (sc.Quantity(8, "ns"), sc.Quantity(88, "ns")))
    phase = experiment.scan("phase", (sc.Quantity(0, "rad"), sc.Quantity(1, "rad")))
    prepare_quantum_hardware(experiment)
    capture = experiment.use(
        ramsey_program(qubit="q0", delay=delay, phase=phase)
        .with_shots(shots)
        .with_compiler_inputs(qubits=sc.parameter_table_ref(QubitParameters))
    )
    result: dict[str, object] = {"delay": delay, "phase": phase}
    if retain in {"raw", "both"}:
        result["raw"] = capture.iq_shots
    if retain in {"stats", "both"}:
        result["stats"] = iq_shot_statistics(experiment, capture.iq_shots)
    return result
