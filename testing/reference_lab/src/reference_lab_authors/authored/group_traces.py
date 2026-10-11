# pyright: reportAny=false, reportCallInDefaultInitializer=false
"""Copyable grouped-analysis examples; reference devices and synthetic T1 only."""

from dataclasses import dataclass, field
from typing import Annotated, cast

import numpy as np
import scopecat as sc
from scopecat.measurements.dataset import Dataset
from scopecat_instruments import NetworkSweepProducts, network_sweep


@dataclass(frozen=True)
class PowerTrace:
    trace: NetworkSweepProducts


@sc.experiment
def power_trace(
    experiment: sc.ExperimentContext,
    power: Annotated[
        sc.Input[sc.Quantity], sc.ControlSpec(scannable=True)
    ] = sc.Quantity(-40, "dBm"),
) -> PowerTrace:
    """Framework-stepped power with an instrument-returned inner frequency axis."""
    analyzer = network_sweep(experiment)
    analyzer.ensure(
        start_frequency=sc.Quantity(4.93, "GHz"),
        stop_frequency=sc.Quantity(5.08, "GHz"),
        points=151,
        if_bandwidth=sc.Quantity(1, "kHz"),
        source_power=power,
        s_parameter="S21",
    )
    return PowerTrace(analyzer.sweep())


def decay(delay: sc.Quantity, repeat: float) -> float:
    return float(np.exp(-delay.to("us").value / (20 + 5 * repeat)))


@sc.experiment
def synthetic_t1(
    experiment: sc.ExperimentContext,
    delay: Annotated[
        sc.Input[sc.Quantity], sc.ControlSpec(scannable=True)
    ] = sc.Quantity(0, "us"),
    repeat: Annotated[sc.Input[float], sc.ControlSpec(scannable=True)] = 0,
) -> sc.ValueRef[float]:
    """Known synthetic decay to exercise grouping; it does not operate a qubit."""
    return cast(
        "sc.ValueRef[float]", experiment.compute(fn=decay, delay=delay, repeat=repeat)
    )


@dataclass(frozen=True)
class ResonanceMinimum:
    frequency: sc.Quantity
    magnitude: float


@dataclass(frozen=True)
class SpectrumPoint:
    frequency: float = field(metadata={"unit": "GHz", "role": "coordinate"})
    magnitude: float


@sc.analysis_function
def locate_resonance(data: Dataset) -> sc.AnalysisProducts[ResonanceMinimum]:
    """Locate the sampled minimum; do not imply sub-grid fit precision."""
    arrays = data.project(
        {"frequency": "trace/frequency", "signal": "trace/s_parameter"},
        units={"frequency": "GHz"},
    ).to_xarray()
    frequency = np.asarray(arrays["frequency"].values, dtype=float).reshape(-1)
    magnitude = np.abs(np.asarray(arrays["signal"].values, dtype=complex)).reshape(-1)
    if len(data) != 1 or len(frequency) < 3 or not np.isfinite(magnitude).all():
        raise ValueError("one complete instrument trace is required")
    index = int(np.argmin(magnitude))
    return sc.AnalysisProducts(
        ResonanceMinimum(
            sc.Quantity(float(frequency[index]), "GHz"), float(magnitude[index])
        ),
        datasets={
            "spectrum": [
                SpectrumPoint(float(x), float(y))
                for x, y in zip(frequency, magnitude, strict=True)
            ]
        },
        plots=(sc.AnalysisPlot(dataset="spectrum", x="frequency", y="magnitude"),),
    )


@dataclass(frozen=True)
class DecayFit:
    lifetime: sc.Quantity


@dataclass(frozen=True)
class DecayPoint:
    delay: float = field(metadata={"unit": "us", "role": "coordinate"})
    probability: float


@sc.analysis_function
def fit_decay(data: Dataset) -> sc.AnalysisProducts[DecayFit]:
    """Zero-baseline exponential fit for this explicitly synthetic example."""
    arrays = data.project(
        {"delay": "delay", "probability": "result"}, units={"delay": "us"}
    ).to_xarray()
    x = np.asarray(arrays["delay"].values, dtype=float)
    y = np.asarray(arrays["probability"].values, dtype=float)
    if x.ndim != 1 or x.shape != y.shape or len(x) < 3 or not np.all(y > 0):
        raise ValueError("at least three aligned positive decay points are required")
    slope, _ = np.polyfit(x, np.log(y), 1)
    if slope >= 0:
        raise ValueError("trace does not decay")
    return sc.AnalysisProducts(
        DecayFit(sc.Quantity(float(-1 / slope), "us")),
        datasets={
            "decay": [DecayPoint(float(a), float(b)) for a, b in zip(x, y, strict=True)]
        },
        plots=(sc.AnalysisPlot(dataset="decay", x="delay", y="probability"),),
    )
