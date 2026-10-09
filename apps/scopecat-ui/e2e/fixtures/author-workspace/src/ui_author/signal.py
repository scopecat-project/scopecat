# Quantity values are immutable and safe as declaration defaults.
# pyright: reportCallInDefaultInitializer=false

"""Analytic experiments for browser state and source-revision checks."""

from __future__ import annotations

from typing import Annotated, Literal, cast

import scopecat as sc


class SignalParameters(sc.ParameterModel, table="signals"):
    signal: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="signal")
    center: sc.Magnitude[float] = sc.quantity(unit="GHz")


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
    """Inspect a synthetic resonance using the configured signal center; no devices."""
    return cast(
        "sc.ValueRef[float]",
        experiment.compute(
            fn=response,
            frequency=frequency,
            center=sc.parameter_ref(
                SignalParameters.center,
                sc.EntityRef(id="a", kind="signal"),
            ),
            gain=gain,
            polarity=polarity,
        ),
    )


def baseline() -> float:
    return 1.0


@sc.experiment
def constant(experiment: sc.ExperimentContext) -> sc.ValueRef[float]:
    """A second author entry for switching away from a retained signal draft."""
    return cast("sc.ValueRef[float]", experiment.compute(fn=baseline))
