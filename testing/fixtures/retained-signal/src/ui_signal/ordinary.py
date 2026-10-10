# Quantity values are immutable and safe as declaration defaults.
# pyright: reportCallInDefaultInitializer=false

"""Synthetic response for ordinary author and retained-analysis journeys."""

from __future__ import annotations

from typing import Annotated, Literal, cast

import numpy as np

import scopecat as sc

from .signal import SignalParameters


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
    """Inspect a synthetic resonance using the configured center; no devices."""
    return cast(
        "sc.ValueRef[float]",
        experiment.compute(
            fn=response,
            frequency=frequency,
            center=sc.parameter_ref(
                SignalParameters.center,
                "signal",
            ),
            gain=gain,
            polarity=polarity,
        ),
    )


@sc.analysis_step(id="ui_signal.author_mean")
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
