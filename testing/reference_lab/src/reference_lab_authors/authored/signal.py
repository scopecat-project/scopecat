# Quantity values are immutable and safe as declaration defaults.
# pyright: reportCallInDefaultInitializer=false
"""Editable Ramsey declaration for physical manual-control integration."""

from __future__ import annotations

from typing import Annotated, cast

import scopecat as sc

from reference_lab.quantum_runner import quantum_capture
from reference_lab.workflows.ramsey import ramsey_program
from reference_lab.workflows.ramsey_experiments import RamseyDataset


@sc.experiment(metadata={"title": "Editable Ramsey"})
def ramsey(
    experiment: sc.ExperimentContext,
    delay: Annotated[
        sc.Input[sc.Quantity],
        sc.ControlSpec(minimum=8, maximum=168, title="Delay", scannable=True),
    ] = sc.Quantity(48, "ns"),
) -> RamseyDataset:
    """Change the supported delay, phase or shot count on the virtual reference lab."""
    probabilities = experiment.use(
        quantum_capture(
            ramsey_program(
                qubit="q0", delay=delay, phase=sc.Quantity(0, "rad")
            ).with_shots(8)
        )
    )
    return RamseyDataset(
        delay=cast("sc.CoordinateRef[sc.Quantity]", delay),
        probabilities=probabilities,
    )
