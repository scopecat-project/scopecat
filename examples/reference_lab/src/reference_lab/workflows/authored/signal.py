# Quantity values are immutable and safe as declaration defaults.
# pyright: reportCallInDefaultInitializer=false

"""Copyable author experiments: controls, scientific helpers and retained analysis."""

from __future__ import annotations

from typing import Annotated, Literal, cast

import numpy as np
import scopecat as sc

from reference_lab.parameters import QubitParameters
from reference_lab.quantum_runner import quantum_capture
from reference_lab.workflows.ramsey import ramsey_program
from reference_lab.workflows.ramsey_experiments import RamseyDataset


def response(
    frequency: sc.Quantity, center: sc.Quantity, gain: float, polarity: str = "positive"
) -> float:
    """Edit this synthetic model without changing device code."""
    detuning = (frequency.to("GHz").value - center.to("GHz").value) / 0.05
    return gain / (1 + detuning**2) * (1 if polarity == "positive" else -1)


@sc.experiment(metadata={"title": "Exploratory signal"})
def signal(
    experiment: sc.ExperimentContext,
    gain: Annotated[sc.Input[float], sc.ControlSpec(title="Gain")] = 1.0,
    *,
    frequency: Annotated[
        sc.Input[sc.Quantity],
        sc.ControlSpec(minimum=4, maximum=6, title="Frequency", scannable=True),
    ] = sc.Quantity(4.8, "GHz"),
    polarity: Literal["positive", "negative"] = "positive",
) -> sc.ValueRef[float]:
    """Inspect a synthetic resonance using the configured q0 carrier; no devices."""
    return cast(
        "sc.ValueRef[float]",
        experiment.compute(
            fn=response,
            frequency=frequency,
            center=sc.parameter_ref(
                QubitParameters.drive_carrier_frequency,
                sc.EntityRef(id="q0", kind="logical_qubit"),
            ),
            gain=gain,
            polarity=polarity,
        ),
    )


@sc.analysis_step(id="reference_lab.author_mean")
def selected_mean(context: sc.AnalysisContext, minimum: float = 0) -> sc.Analysis:
    """Change this selection or fit and reanalyze an existing run."""
    values = np.asarray(context.measurements()["result"].require_values(), dtype=float)
    selected = values[values >= minimum]
    if not selected.size:
        raise ValueError("No retained values meet the selected minimum")
    return (
        context.result("Selected response")
        .fact("minimum", minimum)
        .fact("mean", float(selected.mean()))
    )


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
