"""Progressive Ramsey experiments over the reference lab channel map."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import scopecat as sc
from scopecat.kernel.entity import EntityRef
from scopecat.program.measurement_types import MeasurementArrayData
from scopecat_instruments import DCSourceTarget, dc_source, rf_source
from scopecat_quantum import authoring as q
from scopecat_quantum.measurement_computes import (
    BinaryIqProbabilityProducts,
    binary_iq_probabilities,
)

from reference_lab.parameters import LoGroupParameters, QubitParameters
from reference_lab.quantum_runner import (
    BINARY_IQ_DISCRIMINATOR,
    prepare_quantum_hardware,
    quantum_capture,
)
from reference_lab.workflows.ramsey import (
    conflicting_drive_program,
    parallel_two_qubit_ramsey_program,
    ramsey_program,
    topology_scaled_ramsey_program,
)

Q0 = EntityRef(id="q0", kind="logical_qubit")
Q1 = EntityRef(id="q1", kind="logical_qubit")
RAMSEY_SHOTS = 64
RAMSEY_DELAYS = tuple(sc.Quantity(value, "ns") for value in (8, 48, 88, 128, 168))
FLUX_BIASES = tuple(sc.Quantity(value, "V") for value in (-0.10, 0.0, 0.10))
Q0_LO_FREQUENCIES = tuple(sc.Quantity(value, "GHz") for value in (4.84, 4.85, 4.86))


@dataclass(frozen=True, slots=True)
class RamseyDataset:
    delay: sc.CoordinateRef[sc.Quantity]
    probabilities: BinaryIqProbabilityProducts


@dataclass(frozen=True, slots=True)
class FixedIfLoSweepDataset:
    lo_frequency: sc.CoordinateRef[sc.Quantity]
    signed_if_frequency: sc.ValueRef[sc.Quantity]
    carrier_frequency: sc.ValueRef[sc.Quantity]
    probabilities: BinaryIqProbabilityProducts


@sc.experiment(id="reference_lab.q0_fixed_if_lo_sweep")
def q0_fixed_if_lo_sweep(
    experiment: sc.ExperimentContext,
) -> FixedIfLoSweepDataset:
    """Sweep the host-controlled LO while the real-time program keeps one IF."""

    lo_frequency = experiment.scan("lo_frequency", Q0_LO_FREQUENCIES)
    drive_lo = rf_source(experiment, for_=sc.one(Q0), role="drive-lo")
    drive_lo.ensure(
        frequency=lo_frequency,
        power=sc.parameter_ref(LoGroupParameters.power, "drive-a"),
        output_enabled=True,
        reference_source="external",
    )
    readout_lo = rf_source(experiment, for_=sc.one(Q0), role="readout-lo")
    readout_lo.ensure(
        frequency=sc.parameter_ref(LoGroupParameters.frequency, "readout"),
        power=sc.parameter_ref(LoGroupParameters.power, "readout"),
        output_enabled=True,
        reference_source="external",
    )
    probabilities = experiment.use(
        quantum_capture(
            ramsey_program(
                qubit="q0",
                delay=sc.Quantity(88, "ns"),
                phase=sc.Quantity(0.0, "rad"),
            ).with_shots(RAMSEY_SHOTS),
            prepare_los=False,
        )
    )
    signed_if = sc.parameter_ref(
        QubitParameters.drive_carrier_frequency, "q0"
    ) - sc.parameter_ref(LoGroupParameters.frequency, "drive-a")
    return FixedIfLoSweepDataset(
        lo_frequency=lo_frequency,
        signed_if_frequency=signed_if,
        carrier_frequency=lo_frequency + signed_if,
        probabilities=probabilities,
    )


@sc.experiment(id="reference_lab.conflicting_drive")
def conflicting_drive(experiment: sc.ExperimentContext) -> None:
    """Author a deliberately invalid same-channel parallel pulse plan."""

    prepare_quantum_hardware(experiment)
    experiment.use(
        conflicting_drive_program(qubit="q0").with_compiler_inputs(
            qubits=sc.parameter_table_ref(QubitParameters)
        )
    )


@sc.experiment(id="reference_lab.q0_ramsey")
def q0_ramsey(experiment: sc.ExperimentContext) -> RamseyDataset:
    """Run a delay scan on q0 through its configured drive/readout channels."""

    delay = experiment.scan("delay", RAMSEY_DELAYS)
    probabilities = experiment.use(
        quantum_capture(
            ramsey_program(
                qubit="q0",
                delay=delay,
                phase=sc.Quantity(0.0, "rad"),
            ).with_shots(RAMSEY_SHOTS)
        )
    )
    return RamseyDataset(delay=delay, probabilities=probabilities)


@dataclass(frozen=True, slots=True)
class FluxRamseyDataset:
    dc_bias: sc.CoordinateRef[sc.Quantity]
    delay: sc.CoordinateRef[sc.Quantity]
    probabilities: BinaryIqProbabilityProducts


@sc.experiment(id="reference_lab.flux_ramsey")
def flux_ramsey(experiment: sc.ExperimentContext) -> FluxRamseyDataset:
    """Compose q0 flux bias with a two-dimensional Ramsey scan."""

    dc_bias = experiment.scan("dc_bias", FLUX_BIASES)
    delay = experiment.scan("delay", RAMSEY_DELAYS)
    source = dc_source(experiment, for_=sc.one(Q0))
    source.ensure(
        current_protection=sc.Quantity(100.0, "uA"),
        output_enabled=False,
    )
    source.source_voltage(range=sc.Quantity(1.0, "V"), level=dc_bias)
    source.ensure(output_enabled=True)
    probabilities = experiment.use(
        quantum_capture(
            ramsey_program(
                qubit="q0",
                delay=delay,
                phase=sc.Quantity(0.0, "rad"),
            ).with_shots(RAMSEY_SHOTS)
        )
    )
    experiment.on_success(source, DCSourceTarget(output_enabled=False))
    return FluxRamseyDataset(
        dc_bias=dc_bias,
        delay=delay,
        probabilities=probabilities,
    )


@dataclass(frozen=True, slots=True)
class EntityRamseyDataset:
    qubit: sc.CoordinateRef[EntityRef]
    delay: sc.CoordinateRef[sc.Quantity]
    probabilities: BinaryIqProbabilityProducts


@sc.experiment(id="reference_lab.entity_routed_ramsey")
def entity_routed_ramsey(experiment: sc.ExperimentContext) -> EntityRamseyDataset:
    """Reuse one Ramsey definition while point-locally selecting q0 and q1."""

    qubit = experiment.scan("qubit", (Q0, Q1))
    delay = experiment.scan("delay", RAMSEY_DELAYS[:3])
    probabilities = experiment.use(
        quantum_capture(
            ramsey_program(
                qubit=qubit,
                delay=delay,
                phase=sc.Quantity(0.0, "rad"),
            ).with_shots(RAMSEY_SHOTS)
        )
    )
    return EntityRamseyDataset(
        qubit=qubit,
        delay=delay,
        probabilities=probabilities,
    )


@dataclass(frozen=True, slots=True)
class ParallelRamseyDataset:
    delay: sc.CoordinateRef[sc.Quantity]
    q0: BinaryIqProbabilityProducts
    q1: BinaryIqProbabilityProducts


@dataclass(frozen=True, slots=True)
class ParallelRawRamseyDataset:
    delay: sc.CoordinateRef[sc.Quantity]
    iq_shots: sc.PerEntity[sc.ProductRef[MeasurementArrayData]]


@dataclass(frozen=True, slots=True)
class TopologyScaledRamseyDataset:
    delay: sc.CoordinateRef[sc.Quantity]
    iq_shots: sc.ProductRef[MeasurementArrayData]


@sc.experiment(id="reference_lab.topology_scaled_ramsey")
def topology_scaled_ramsey(
    experiment: sc.ExperimentContext,
) -> TopologyScaledRamseyDataset:
    """Resolve one chip-size-agnostic program onto three connected qubits."""

    delay = experiment.scan("delay", RAMSEY_DELAYS[:3])
    call = topology_scaled_ramsey_program(
        targets=q.select_qubits(
            3,
            connected=True,
            anchor="q1",
            connection_kind="nearest_neighbor",
        ),
        delay=delay,
        phase=sc.Quantity(0.0, "rad"),
    ).with_shots(RAMSEY_SHOTS)
    prepare_quantum_hardware(experiment)
    configured_call = call.with_compiler_inputs(
        qubits=sc.parameter_table_ref(QubitParameters)
    )
    experiment.use(configured_call)
    return TopologyScaledRamseyDataset(
        delay=delay,
        iq_shots=cast(
            "sc.ProductRef[MeasurementArrayData]",
            configured_call.results.iq_shots,
        ),
    )


@sc.experiment(id="reference_lab.parallel_raw_ramsey")
def parallel_raw_ramsey(experiment: sc.ExperimentContext) -> ParallelRawRamseyDataset:
    """Retain raw per-channel IQ so one unavailable demodulator stays visible."""

    delay = experiment.scan(
        "delay",
        (sc.Quantity(88, "ns"), sc.Quantity(128, "ns")),
    )
    call = parallel_two_qubit_ramsey_program(
        q0="q0",
        q1="q1",
        delay=delay,
        q0_phase=sc.Quantity(0.0, "rad"),
        q1_phase=sc.Quantity(0.4, "rad"),
    ).with_shots(RAMSEY_SHOTS)
    prepare_quantum_hardware(experiment)
    configured_call = call.with_compiler_inputs(
        qubits=sc.parameter_table_ref(QubitParameters)
    )
    experiment.use(configured_call)
    return ParallelRawRamseyDataset(
        delay=delay,
        iq_shots=cast(
            "sc.PerEntity[sc.ProductRef[MeasurementArrayData]]",
            configured_call.entity_results(),
        ),
    )


@sc.experiment(id="reference_lab.parallel_two_qubit_ramsey")
def parallel_two_qubit_ramsey(
    experiment: sc.ExperimentContext,
) -> ParallelRamseyDataset:
    """Compile synchronized q0/q1 Ramsey branches onto independent channels."""

    delay = experiment.scan("delay", RAMSEY_DELAYS[:3])
    call = parallel_two_qubit_ramsey_program(
        q0="q0",
        q1="q1",
        delay=delay,
        q0_phase=sc.Quantity(0.0, "rad"),
        q1_phase=sc.Quantity(0.4, "rad"),
    ).with_shots(RAMSEY_SHOTS)
    prepare_quantum_hardware(experiment)
    results = experiment.use(
        call.with_compiler_inputs(qubits=sc.parameter_table_ref(QubitParameters))
    )
    q0_products = binary_iq_probabilities(
        experiment,
        results.q0_iq_shots,
        discriminator=BINARY_IQ_DISCRIMINATOR,
        id="q0-discrimination",
    )
    q1_products = binary_iq_probabilities(
        experiment,
        results.q1_iq_shots,
        discriminator=BINARY_IQ_DISCRIMINATOR,
        id="q1-discrimination",
    )
    return ParallelRamseyDataset(
        delay=delay,
        q0=q0_products,
        q1=q1_products,
    )


__all__ = [
    "Q0_LO_FREQUENCIES",
    "RAMSEY_DELAYS",
    "RAMSEY_SHOTS",
    "EntityRamseyDataset",
    "FixedIfLoSweepDataset",
    "FluxRamseyDataset",
    "ParallelRamseyDataset",
    "ParallelRawRamseyDataset",
    "RamseyDataset",
    "TopologyScaledRamseyDataset",
    "conflicting_drive",
    "entity_routed_ramsey",
    "flux_ramsey",
    "parallel_raw_ramsey",
    "parallel_two_qubit_ramsey",
    "q0_fixed_if_lo_sweep",
    "q0_ramsey",
    "topology_scaled_ramsey",
]
