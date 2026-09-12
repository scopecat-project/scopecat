"""Four-qubit DC bias orchestration across two physical sources."""

from __future__ import annotations

from dataclasses import dataclass

import scopecat as sc
from scopecat.kernel.entity import EntityRef
from scopecat_instruments import (
    DCBiasReadbackProducts,
    DCSourceGroupTarget,
    dc_bias,
    dc_source,
    temperature_readout,
)

from reference_lab.parameters import BiasProfile, ChannelCalibration
from reference_lab.workflows.flux_spectroscopy import CRYOSTAT

Q0 = EntityRef(id="q0", kind="logical_qubit")
Q1 = EntityRef(id="q1", kind="logical_qubit")
Q2 = EntityRef(id="q2", kind="logical_qubit")
Q3 = EntityRef(id="q3", kind="logical_qubit")
QUBIT_SELECTION = sc.each(Q0, Q1, Q2, Q3)
PARKED_PROFILE = "parked"
OPERATE_PROFILE = "operate"


def _physical_bias_profile(
    profile: str,
) -> sc.PerEntity[sc.ValueRef[sc.Quantity]]:
    return sc.PerEntity(
        (
            qubit,
            sc.parameter_ref(BiasProfile.logical_bias, (profile, qubit))
            * sc.parameter_ref(ChannelCalibration.flux_gain, qubit)
            * sc.parameter_ref(ChannelCalibration.flux_polarity, qubit)
            + sc.parameter_ref(ChannelCalibration.flux_offset, qubit),
        )
        for qubit in QUBIT_SELECTION
    )


@dataclass(frozen=True, slots=True)
class MultiChannelBiasDataset:
    temperature: sc.ProductRef[float]
    physical_bias: sc.PerEntity[sc.ValueRef[sc.Quantity]]
    readback: sc.PerEntity[DCBiasReadbackProducts]


@sc.experiment(id="reference_lab.multichannel_dc_bias")
def multichannel_dc_bias(
    experiment: sc.ExperimentContext,
) -> MultiChannelBiasDataset:
    """Set four independent lines on two devices and always return them to off."""

    parked = _physical_bias_profile(PARKED_PROFILE)
    operate = _physical_bias_profile(OPERATE_PROFILE)
    biases = dc_source(experiment, for_=QUBIT_SELECTION)
    bias_ramps = dc_bias(experiment, for_=QUBIT_SELECTION)
    biases.ensure(
        current_protection=sc.Quantity(100.0, "uA"),
        output_enabled=False,
    )
    bias_ramps.ensure(
        target_voltage=parked,
        ramp_duration=sc.Quantity(100.0, "ms"),
        settle_tolerance=sc.Quantity(0.1, "mV"),
    )
    biases.ensure(output_enabled=True)
    bias_ramps.ensure(
        target_voltage=operate,
        ramp_duration=sc.Quantity(250.0, "ms"),
        settle_tolerance=sc.Quantity(0.1, "mV"),
    )
    thermometer = temperature_readout(
        experiment,
        for_=sc.one(CRYOSTAT),
    )
    sample = thermometer.sample()
    readback = bias_ramps.readback(id="operate-readback")
    physical_bias = sc.PerEntity(
        (
            qubit,
            operate[qubit],
        )
        for qubit in QUBIT_SELECTION
    )
    bias_ramps.ensure(
        target_voltage=parked,
        ramp_duration=sc.Quantity(250.0, "ms"),
        settle_tolerance=sc.Quantity(0.1, "mV"),
    )
    biases.ensure(output_enabled=False)
    experiment.on_success(biases, DCSourceGroupTarget(output_enabled=False))
    return MultiChannelBiasDataset(
        temperature=sample.temperature,
        physical_bias=physical_bias,
        readback=readback,
    )


MULTICHANNEL_DC_BIAS = multichannel_dc_bias()


__all__ = [
    "MULTICHANNEL_DC_BIAS",
    "OPERATE_PROFILE",
    "PARKED_PROFILE",
    "MultiChannelBiasDataset",
    "multichannel_dc_bias",
]
