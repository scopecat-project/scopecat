"""Vendor-neutral resonator spectroscopy over a DC flux-bias scan."""

from __future__ import annotations

from dataclasses import dataclass

import scopecat as sc
from scopecat.kernel.entity import EntityRef
from scopecat_instruments import (
    DCSourceTarget,
    NetworkSweepProducts,
    dc_source,
    network_sweep,
    temperature_readout,
)

CRYOSTAT = EntityRef(id="cryostat", kind="cryostat")
Q0 = EntityRef(id="q0", kind="logical_qubit")

TRACE_POINTS = 751
BIAS_POINTS = 11
BIAS_START = sc.Quantity(-0.25, "V")
BIAS_STOP = sc.Quantity(0.25, "V")
SWEEP_START = sc.Quantity(4.93, "GHz")
SWEEP_STOP = sc.Quantity(5.08, "GHz")


@dataclass(frozen=True, slots=True)
class FluxSpectroscopyDataset:
    """Typed handles for the durable flux-spectroscopy dataset."""

    dc_bias: sc.CoordinateRef[sc.Quantity]
    trace: NetworkSweepProducts
    temperature: sc.ProductRef[float]


@sc.experiment
def flux_spectroscopy(
    experiment: sc.ExperimentContext,
) -> FluxSpectroscopyDataset:
    """Scan DC bias and persist one VNA trace plus temperature per point."""

    dc_bias = experiment.scan(
        "dc_bias",
        start=BIAS_START,
        stop=BIAS_STOP,
        points=BIAS_POINTS,
    )
    flux_source = dc_source(
        experiment,
        for_=sc.one(Q0),
    )
    temperature = temperature_readout(
        experiment,
        for_=sc.one(CRYOSTAT),
    )
    readout = network_sweep(experiment)

    flux_source.ensure(
        current_protection=sc.Quantity(100.0, "uA"),
        output_enabled=False,
    )
    flux_source.source_voltage(
        range=sc.Quantity(1.0, "V"),
        level=dc_bias,
    )
    flux_source.ensure(output_enabled=True)
    readout.ensure(
        start_frequency=SWEEP_START,
        stop_frequency=SWEEP_STOP,
        points=TRACE_POINTS,
        if_bandwidth=sc.Quantity(1.0, "kHz"),
        source_power=sc.Quantity(-35.0, "dBm"),
        s_parameter="S21",
    )
    trace = readout.sweep()
    sample = temperature.sample()
    experiment.on_success(flux_source, DCSourceTarget(output_enabled=False))

    return FluxSpectroscopyDataset(
        dc_bias=dc_bias,
        trace=trace,
        temperature=sample.temperature,
    )


FLUX_SPECTROSCOPY = flux_spectroscopy.build()


__all__ = [
    "BIAS_POINTS",
    "BIAS_START",
    "BIAS_STOP",
    "FLUX_SPECTROSCOPY",
    "TRACE_POINTS",
    "FluxSpectroscopyDataset",
    "flux_spectroscopy",
]
