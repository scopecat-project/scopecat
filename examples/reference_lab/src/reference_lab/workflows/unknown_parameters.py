"""Small registered consumers for complete, partly unknown parameter tables."""

from dataclasses import dataclass
from typing import Annotated

import scopecat as sc


@dataclass
class ProbeParameters:
    id: str
    duration: Annotated[float, sc.ParameterSpec(unit="ns")]
    pi_amplitude: float | None = None


@sc.experiment(id="reference_lab.duration_probe")
def duration_probe(experiment: sc.ExperimentContext) -> sc.ValueRef[sc.Quantity]:
    del experiment
    return sc.parameter_lookup(
        "probes",
        key={"id": "q0"},
        column="duration",
        value_type=sc.QuantityType(unit="ns"),
    )


@sc.experiment(id="reference_lab.pi_probe")
def pi_probe(experiment: sc.ExperimentContext) -> sc.ValueRef[float]:
    del experiment
    return sc.parameter_lookup(
        "probes",
        key={"id": "q0"},
        column="pi_amplitude",
        value_type=sc.FloatType(),
    )
