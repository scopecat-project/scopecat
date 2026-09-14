"""A synthetic response to explore the framework; no hardware calibration claim."""

from dataclasses import dataclass
from typing import Annotated, cast

import scopecat as sc
from scopecat.measurements.dataset import Dataset


def response(position: float, center: float) -> float:
    return 1.0 / (1.0 + (position - center) ** 2)


@sc.experiment(id="shared_signal")
def signal(
    experiment: sc.ExperimentContext,
    center: float = 0.0,
    *,
    position: Annotated[
        sc.Input[float], sc.ControlSpec(title="Position", scannable=True)
    ] = 0.0,
) -> sc.ValueRef[float]:
    return cast(
        "sc.ValueRef[float]",
        experiment.compute(fn=response, position=position, center=center),
    )


@dataclass(frozen=True)
class Summary:
    mean: float
    points: int


@sc.analysis_function
def summarize(data: Dataset) -> Summary:
    values = cast("tuple[float, ...]", data["result"].require_values())
    return Summary(mean=sum(values) / len(values), points=len(values))
