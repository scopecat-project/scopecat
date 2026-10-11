from __future__ import annotations

from scopecat import Quantity
from scopecat.kernel.entity import EntityRef
from scopecat_quantum._ids import CircuitOperationId, QubitId
from scopecat_quantum.circuits import verify_circuit_operations
from scopecat_quantum.gates import GateCall
from scopecat_quantum.standard_gates import X90

from reference_lab.quantum_compilation.compiler_parameters import (
    QuantumCompilerParameters,
)
from reference_lab.quantum_compilation.pulse_profile import QUANTUM_PULSE_PROFILE


def _parameters(*, beta: float) -> QuantumCompilerParameters:
    return QuantumCompilerParameters.from_qubit_rows(
        (
            {
                "qubit": EntityRef(id="q0", kind="logical_qubit"),
                "quarter_turn_duration": Quantity(16, "ns"),
                "quarter_turn_amplitude": Quantity(0.2, "arb"),
                "quarter_turn_sigma": Quantity(4, "ns"),
                "drag_beta": Quantity(beta, "ns"),
            },
        )
    )


def test_drag_beta_overlay_changes_resolved_pulse_not_recipe_identity() -> None:
    baseline = _parameters(beta=0.5)
    overlaid = _parameters(beta=0.75)
    circuit = verify_circuit_operations(
        (
            GateCall(
                CircuitOperationId("x90-q0"),
                X90.definition.id,
                (QubitId("q0"),),
            ),
        ),
        (X90.definition,),
    )

    [baseline_x90] = QUANTUM_PULSE_PROFILE.materialize(baseline, circuit).gates
    [overlaid_x90] = QUANTUM_PULSE_PROFILE.materialize(overlaid, circuit).gates

    assert baseline_x90.id == overlaid_x90.id
    assert baseline_x90.fingerprint != overlaid_x90.fingerprint
    assert baseline.fingerprint != overlaid.fingerprint
