"""Lab-owned experiment policy for directly runnable quantum programs."""

from __future__ import annotations

import scopecat as sc
from scopecat.kernel.entity import EntityRef
from scopecat_instruments import rf_source
from scopecat_quantum import authoring as quantum
from scopecat_quantum.measurement_computes import (
    BinaryIqDiscriminator,
    BinaryIqProbabilityProducts,
    IqCentroid,
    binary_iq_probabilities,
)

from reference_lab.parameters import LoGroupParameters, QubitParameters
from reference_lab.physical_policies import ensure_grouped_iq_offsets

Q0 = EntityRef(id="q0", kind="logical_qubit")
Q1 = EntityRef(id="q1", kind="logical_qubit")
Q2 = EntityRef(id="q2", kind="logical_qubit")
Q3 = EntityRef(id="q3", kind="logical_qubit")
QUANTUM_QUBITS = sc.each(Q0, Q1, Q2, Q3)
DRIVE_IQ_CHAINS = (
    (Q0, "drive-q0"),
    (Q1, "drive-q1"),
    (Q2, "drive-q2"),
    (Q3, "drive-q3"),
)

BINARY_IQ_DISCRIMINATOR = BinaryIqDiscriminator(
    state_0_centroid=IqCentroid(real=-1.0, imag=0.0),
    state_1_centroid=IqCentroid(real=1.0, imag=0.0),
    tie_policy="state_0",
)


@sc.module(id="quantum-lab.capture")
def quantum_capture(
    module: sc.ModuleContext,
    call: quantum.QuantumProgramCall,
    *,
    prepare_los: bool = True,
) -> BinaryIqProbabilityProducts:
    """Apply reviewed lab preparation and capture policy to a quantum call.

    Most experiments use the reviewed LO setpoints below. A host-side LO sweep
    opts out and authors its changing state explicitly around this module.
    """

    prepare_quantum_hardware(module, prepare_los=prepare_los)
    configured = call.with_compiler_inputs(
        qubits=sc.parameter_table_ref(QubitParameters)
    )
    results = module.use(configured)
    return binary_iq_probabilities(
        module,
        results.iq_shots,
        discriminator=BINARY_IQ_DISCRIMINATOR,
    )


def prepare_quantum_hardware(
    context: sc.ExperimentContext | sc.ModuleContext,
    *,
    prepare_los: bool = True,
) -> None:
    """Apply lab-owned physical state required by a direct quantum call."""

    ensure_grouped_iq_offsets(
        context,
        qubits=QUANTUM_QUBITS,
        drive_iq_chains=DRIVE_IQ_CHAINS,
    )
    if prepare_los:
        _prepare_reviewed_los(context)


def _prepare_reviewed_los(
    context: sc.ExperimentContext | sc.ModuleContext,
) -> None:
    drive_los = rf_source(context, for_=QUANTUM_QUBITS, role="drive-lo")
    drive_los.ensure(
        frequency=sc.PerEntity(
            (
                (Q0, sc.parameter_ref(LoGroupParameters.frequency, "drive-a")),
                (Q1, sc.parameter_ref(LoGroupParameters.frequency, "drive-a")),
                (Q2, sc.parameter_ref(LoGroupParameters.frequency, "drive-b")),
                (Q3, sc.parameter_ref(LoGroupParameters.frequency, "drive-b")),
            )
        ),
        power=sc.PerEntity(
            (
                (Q0, sc.parameter_ref(LoGroupParameters.power, "drive-a")),
                (Q1, sc.parameter_ref(LoGroupParameters.power, "drive-a")),
                (Q2, sc.parameter_ref(LoGroupParameters.power, "drive-b")),
                (Q3, sc.parameter_ref(LoGroupParameters.power, "drive-b")),
            )
        ),
        output_enabled=True,
        reference_source="external",
    )
    readout_lo = rf_source(context, for_=QUANTUM_QUBITS, role="readout-lo")
    readout_lo.ensure(
        frequency=sc.parameter_ref(LoGroupParameters.frequency, "readout"),
        power=sc.parameter_ref(LoGroupParameters.power, "readout"),
        output_enabled=True,
        reference_source="external",
    )


@sc.experiment
def run_quantum(
    experiment: sc.ExperimentContext,
    call: quantum.QuantumProgramCall,
) -> BinaryIqProbabilityProducts:
    """Run an integrated-IQ program through the lab-owned experiment skeleton.

    Independent local-device work, including external LO preparation, belongs
    here. Only hardware that participates in the prepared real-time program is
    part of the quantum target/compiler contract.
    """

    return experiment.use(quantum_capture(call))


__all__ = [
    "BINARY_IQ_DISCRIMINATOR",
    "prepare_quantum_hardware",
    "quantum_capture",
    "run_quantum",
]
