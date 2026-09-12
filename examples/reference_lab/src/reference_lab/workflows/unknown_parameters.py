"""Registered consumers use one declaration for editing and run references."""

import scopecat as sc


class ProbeParameters(sc.ParameterModel, table="probes"):
    id: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns")
    pi_amplitude: sc.Param[float | None] = sc.param(default=None)


@sc.experiment(id="reference_lab.duration_probe")
def duration_probe(experiment: sc.ExperimentContext) -> sc.ValueRef[sc.Quantity]:
    del experiment
    return sc.parameter_ref(ProbeParameters.duration, "q0")


@sc.experiment(id="reference_lab.pi_probe")
def pi_probe(experiment: sc.ExperimentContext) -> sc.ValueRef[float]:
    del experiment
    return sc.parameter_ref(ProbeParameters.pi_amplitude, "q0")
