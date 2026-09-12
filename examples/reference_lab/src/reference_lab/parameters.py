"""Parameter declarations and reviewed initial values for the laboratory."""

from __future__ import annotations

import scopecat as sc
from scopecat.records.parameter import ParameterSnapshot


class QubitParameters(sc.ParameterModel, table="qubits"):
    "Reviewed per-qubit drive carrier and DRAG calibration values."

    qubit: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    drag_beta: sc.Magnitude[float] = sc.quantity(unit="ns")
    quarter_turn_duration: sc.Magnitude[float] = sc.quantity(unit="ns")
    quarter_turn_amplitude: sc.Magnitude[float] = sc.quantity(unit="arb")
    quarter_turn_sigma: sc.Magnitude[float] = sc.quantity(unit="ns")
    drive_carrier_frequency: sc.Magnitude[float] = sc.quantity(unit="Hz")


class IqChainParameters(sc.ParameterModel, table="iq_chains"):
    """Reviewed physical IQ-chain calibration. Logical signal membership and
    physical DAC routes live in infrastructure configuration."""

    chain: sc.Param[str] = sc.param(key=True)
    mixer_ii: sc.Param[float] = sc.param()
    mixer_iq: sc.Param[float] = sc.param()
    mixer_qi: sc.Param[float] = sc.param()
    mixer_qq: sc.Param[float] = sc.param()
    mixer_i_offset: sc.Magnitude[float] = sc.quantity(unit="V")
    mixer_q_offset: sc.Magnitude[float] = sc.quantity(unit="V")


class AwgOutputBaseline(sc.ParameterModel, table="awg_output_baselines"):
    "Reviewed state for lab policy slots that are not logical IQ chains."

    slot: sc.Param[str] = sc.param(key=True)
    offset: sc.Magnitude[float] = sc.quantity(unit="V")


class LoGroupParameters(sc.ParameterModel, table="lo_groups"):
    "Reviewed setpoints for statically wired LO distribution groups."

    group: sc.Param[str] = sc.param(key=True)
    frequency: sc.Magnitude[float] = sc.quantity(unit="Hz")
    power: sc.Magnitude[float] = sc.quantity(unit="dBm")


class ReadoutResonator(sc.ParameterModel, table="readout_resonators"):
    "Reviewed readout resonator calibration values."

    resonator: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    resonance_frequency: sc.Magnitude[float] = sc.quantity(unit="Hz")
    linewidth: sc.Magnitude[float] = sc.quantity(unit="Hz")
    flux_sweet_spot: sc.Magnitude[float] = sc.quantity(unit="V")


class ChannelCalibration(sc.ParameterModel, table="channel_calibrations"):
    "Reviewed per-qubit line calibration; physical routes live in config."

    qubit: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    channel_delay: sc.Magnitude[float] = sc.quantity(unit="ns")
    flux_gain: sc.Param[float] = sc.param()
    flux_polarity: sc.Param[int] = sc.param()
    flux_offset: sc.Magnitude[float] = sc.quantity(unit="V")


class BiasProfile(sc.ParameterModel, table="bias_profiles"):
    """Named logical operating planes; channel gain, polarity, and offset are
    applied separately."""

    profile: sc.Param[str] = sc.param(key=True)
    qubit: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
    logical_bias: sc.Magnitude[float] = sc.quantity(unit="V")


REFERENCE_PARAMETER_CATALOG = sc.parameter_catalog(
    "reference-lab-parameter-catalog",
    QubitParameters,
    IqChainParameters,
    AwgOutputBaseline,
    LoGroupParameters,
    ReadoutResonator,
    ChannelCalibration,
    BiasProfile,
)

DRIVE_AWG_OFFSET_GUARD_SLOT_ID = "drive-awg.offset-guard"


def reference_lab_parameter_snapshot() -> ParameterSnapshot:
    return sc.parameter_snapshot(
        "reference-lab-parameter-snapshot",
        tables={
            QubitParameters: [
                QubitParameters(
                    qubit=sc.EntityRef(id="q0", kind="logical_qubit"),
                    drag_beta=0.5,
                    quarter_turn_duration=16.0,
                    quarter_turn_amplitude=0.2,
                    quarter_turn_sigma=4.0,
                    drive_carrier_frequency=4800000000.0,
                ),
                QubitParameters(
                    qubit=sc.EntityRef(id="q1", kind="logical_qubit"),
                    drag_beta=0.45,
                    quarter_turn_duration=18.0,
                    quarter_turn_amplitude=0.18,
                    quarter_turn_sigma=4.5,
                    drive_carrier_frequency=4900000000.0,
                ),
                QubitParameters(
                    qubit=sc.EntityRef(id="q2", kind="logical_qubit"),
                    drag_beta=0.4,
                    quarter_turn_duration=20.0,
                    quarter_turn_amplitude=0.17,
                    quarter_turn_sigma=5.0,
                    drive_carrier_frequency=5000000000.0,
                ),
                QubitParameters(
                    qubit=sc.EntityRef(id="q3", kind="logical_qubit"),
                    drag_beta=0.35,
                    quarter_turn_duration=22.0,
                    quarter_turn_amplitude=0.16,
                    quarter_turn_sigma=5.5,
                    drive_carrier_frequency=5100000000.0,
                ),
            ],
            ReadoutResonator: [
                ReadoutResonator(
                    resonator=sc.EntityRef(id="q0", kind="logical_qubit"),
                    resonance_frequency=5000000000.0,
                    linewidth=2000000.0,
                    flux_sweet_spot=0.0,
                ),
                ReadoutResonator(
                    resonator=sc.EntityRef(id="q1", kind="logical_qubit"),
                    resonance_frequency=5200000000.0,
                    linewidth=2200000.0,
                    flux_sweet_spot=0.02,
                ),
                ReadoutResonator(
                    resonator=sc.EntityRef(id="q2", kind="logical_qubit"),
                    resonance_frequency=5400000000.0,
                    linewidth=2400000.0,
                    flux_sweet_spot=-0.01,
                ),
                ReadoutResonator(
                    resonator=sc.EntityRef(id="q3", kind="logical_qubit"),
                    resonance_frequency=5600000000.0,
                    linewidth=2600000.0,
                    flux_sweet_spot=0.03,
                ),
            ],
            IqChainParameters: [
                IqChainParameters(
                    chain="drive-q0",
                    mixer_ii=1.0,
                    mixer_iq=0.0,
                    mixer_qi=0.0,
                    mixer_qq=1.0,
                    mixer_i_offset=0.0,
                    mixer_q_offset=0.0,
                ),
                IqChainParameters(
                    chain="drive-q1",
                    mixer_ii=1.0,
                    mixer_iq=0.0,
                    mixer_qi=0.0,
                    mixer_qq=1.0,
                    mixer_i_offset=0.0,
                    mixer_q_offset=0.0,
                ),
                IqChainParameters(
                    chain="drive-q2",
                    mixer_ii=1.0,
                    mixer_iq=0.0,
                    mixer_qi=0.0,
                    mixer_qq=1.0,
                    mixer_i_offset=0.0,
                    mixer_q_offset=0.0,
                ),
                IqChainParameters(
                    chain="drive-q3",
                    mixer_ii=1.0,
                    mixer_iq=0.0,
                    mixer_qi=0.0,
                    mixer_qq=1.0,
                    mixer_i_offset=0.0,
                    mixer_q_offset=0.0,
                ),
                IqChainParameters(
                    chain="readout",
                    mixer_ii=1.0,
                    mixer_iq=0.0,
                    mixer_qi=0.0,
                    mixer_qq=1.0,
                    mixer_i_offset=0.0,
                    mixer_q_offset=0.0,
                ),
            ],
            AwgOutputBaseline: [
                AwgOutputBaseline(
                    slot="drive-awg.offset-guard",
                    offset=0.007,
                ),
            ],
            LoGroupParameters: [
                LoGroupParameters(
                    group="drive-a",
                    frequency=4850000000.0,
                    power=-10.0,
                ),
                LoGroupParameters(
                    group="drive-b",
                    frequency=5050000000.0,
                    power=-10.0,
                ),
                LoGroupParameters(
                    group="readout",
                    frequency=5300000000.0,
                    power=-5.0,
                ),
            ],
            ChannelCalibration: [
                ChannelCalibration(
                    qubit=sc.EntityRef(id="q0", kind="logical_qubit"),
                    channel_delay=0.0,
                    flux_gain=0.98,
                    flux_polarity=1,
                    flux_offset=0.0,
                ),
                ChannelCalibration(
                    qubit=sc.EntityRef(id="q1", kind="logical_qubit"),
                    channel_delay=1.5,
                    flux_gain=1.02,
                    flux_polarity=-1,
                    flux_offset=0.002,
                ),
                ChannelCalibration(
                    qubit=sc.EntityRef(id="q2", kind="logical_qubit"),
                    channel_delay=-0.5,
                    flux_gain=1.01,
                    flux_polarity=1,
                    flux_offset=-0.001,
                ),
                ChannelCalibration(
                    qubit=sc.EntityRef(id="q3", kind="logical_qubit"),
                    channel_delay=0.75,
                    flux_gain=0.99,
                    flux_polarity=-1,
                    flux_offset=0.003,
                ),
            ],
            BiasProfile: [
                BiasProfile(
                    profile="parked",
                    qubit=sc.EntityRef(id="q0", kind="logical_qubit"),
                    logical_bias=0.0,
                ),
                BiasProfile(
                    profile="parked",
                    qubit=sc.EntityRef(id="q1", kind="logical_qubit"),
                    logical_bias=0.0,
                ),
                BiasProfile(
                    profile="parked",
                    qubit=sc.EntityRef(id="q2", kind="logical_qubit"),
                    logical_bias=0.0,
                ),
                BiasProfile(
                    profile="parked",
                    qubit=sc.EntityRef(id="q3", kind="logical_qubit"),
                    logical_bias=0.0,
                ),
                BiasProfile(
                    profile="operate",
                    qubit=sc.EntityRef(id="q0", kind="logical_qubit"),
                    logical_bias=-0.08,
                ),
                BiasProfile(
                    profile="operate",
                    qubit=sc.EntityRef(id="q1", kind="logical_qubit"),
                    logical_bias=-0.02,
                ),
                BiasProfile(
                    profile="operate",
                    qubit=sc.EntityRef(id="q2", kind="logical_qubit"),
                    logical_bias=0.04,
                ),
                BiasProfile(
                    profile="operate",
                    qubit=sc.EntityRef(id="q3", kind="logical_qubit"),
                    logical_bias=0.1,
                ),
            ],
        },
        scalars={},
    )
