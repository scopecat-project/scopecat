"""Author-owned small experiment and analysis; edit this file and rerun Python."""

from __future__ import annotations

from typing import Annotated, cast

import numpy as np
import scopecat as sc

from reference_lab.parameters import QubitParameters


def response(frequency: sc.Quantity, center: sc.Quantity, gain: float) -> float:
    """Synthetic resonance with a 50 MHz half-width; no physical acquisition."""
    detuning = (frequency.to("GHz").value - center.to("GHz").value) / 0.05
    return gain / (1 + detuning**2)


@sc.experiment(id="reference_lab.exploratory_signal")
def exploratory_signal(
    experiment: sc.ExperimentContext,
    gain: Annotated[sc.Input[float], sc.FloatType(minimum=0)] = 1.0,
) -> sc.ValueRef[float]:
    frequency = experiment.scan("frequency", (4.7, 4.8, 4.9, 5.0, 5.1), unit="GHz")
    signal = cast(
        "sc.ValueRef[float]",
        experiment.compute(
            fn=response,
            frequency=frequency,
            center=sc.parameter_ref(
                QubitParameters.drive_carrier_frequency,
                sc.EntityRef(id="q0", kind="logical_qubit"),
            ),
            gain=gain,
        ),
    )
    return signal


@sc.analysis_step(id="reference_lab.exploratory_mean")
def exploratory_mean(context: sc.AnalysisContext, *, minimum: float = 0) -> sc.Analysis:
    """Change the selection without acquiring or altering the original run."""
    values = np.asarray(context.measurements()["result"].require_values(), dtype=float)
    selected = values[values >= minimum]
    if not selected.size:
        raise ValueError("No retained values meet the selected minimum")
    return (
        context.result("Retained synthetic response")
        .fact("minimum", minimum)
        .fact("selected-points", int(selected.size))
        .fact("mean-response", float(selected.mean()))
    )
