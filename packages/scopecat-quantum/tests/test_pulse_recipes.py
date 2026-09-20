from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from scopecat import Quantity

from scopecat_quantum import authoring as quantum
from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CircuitOperationId,
    CouplerId,
    QuantumProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import (
    INTEGRATED_IQ_RESULT,
    AcquisitionKind,
)
from scopecat_quantum.circuits import Measure, verify_circuit_operations
from scopecat_quantum.gates import GateArgument, GateCall, GateParameterKind
from scopecat_quantum.programs import (
    ParallelEach,
    QuantumProgramIR,
    verify_quantum_program,
)
from scopecat_quantum.pulse_implementations import (
    GatePulseImplementationArgument,
    GatePulseImplementationKey,
    MeasurementPulseImplementationKey,
)
from scopecat_quantum.pulse_recipes import (
    PulseRecipeMap,
    PulseRecipeMaterializationCache,
    PulseRecipeProfile,
    gate_pulse_recipe,
    map_qubit_pulse_recipes,
    measurement_pulse_recipe,
)
from scopecat_quantum.pulses import Constant, Play, iter_pulse_leaves


@dataclass(frozen=True, slots=True)
class _QubitRow:
    qubit: QubitId
    drive_amplitude: Quantity
    readout_amplitude: Quantity


@dataclass(frozen=True, slots=True)
class _Parameters:
    qubits: tuple[_QubitRow, ...]


X = quantum.single_qubit_gate("x")
RX = quantum.single_qubit_gate(
    "rx",
    parameters={"theta": GateParameterKind.ANGLE},
)
CZ = quantum.two_qubit_gate("cz")


@gate_pulse_recipe(of=X, id="x.constant")
def _x_recipe(
    row: _QubitRow,
    target: quantum.Qubit,
) -> quantum.QuantumFragment:
    return quantum.play(
        quantum.drive(target),
        quantum.constant(
            duration=Quantity(16, "ns"),
            amplitude=row.drive_amplitude,
        ),
    )


@measurement_pulse_recipe(
    kind=AcquisitionKind.INTEGRATED_IQ,
    id="readout.integrated-iq",
)
def _readout_recipe(
    row: _QubitRow,
    target: quantum.Qubit,
) -> quantum.QuantumFragment:
    duration = Quantity(800, "ns")
    return quantum.parallel(
        quantum.play(
            quantum.readout(target),
            quantum.constant(
                duration=duration,
                amplitude=row.readout_amplitude,
            ),
        ),
        quantum.acquire(target, duration=duration, result="result"),
    )


@gate_pulse_recipe(of=RX, id="rx.constant")
def _rx_recipe(
    row: _QubitRow,
    target: quantum.Qubit,
    *,
    theta: Quantity,
) -> quantum.QuantumFragment:
    return quantum.play(
        quantum.drive(target),
        quantum.constant(
            duration=Quantity(16, "ns"),
            amplitude=row.drive_amplitude,
            phase=theta,
        ),
    )


_PROFILE = PulseRecipeProfile[_Parameters](
    map_qubit_pulse_recipes(
        rows=lambda parameters: parameters.qubits,
        qubit=lambda row: row.qubit,
        gates=(_x_recipe, _rx_recipe),
        measurements=(_readout_recipe,),
    )
)


def _parameters(*, q0_amplitude: float = 0.2) -> _Parameters:
    return _Parameters(
        (
            _QubitRow(
                QubitId("q1"),
                Quantity(0.3, "arb"),
                Quantity(0.4, "arb"),
            ),
            _QubitRow(
                QubitId("q0"),
                Quantity(q0_amplitude, "arb"),
                Quantity(0.35, "arb"),
            ),
        )
    )


def _x_call(operation_id: str, qubit_id: str) -> GateCall:
    return GateCall(
        CircuitOperationId(operation_id),
        X.definition.id,
        (QubitId(qubit_id),),
    )


def _measurement(operation_id: str, qubit_id: str) -> Measure:
    return Measure(
        CircuitOperationId(operation_id),
        QubitId(qubit_id),
        AcquisitionSlotId(operation_id),
        INTEGRATED_IQ_RESULT,
    )


def _circuit(*operations: GateCall | Measure):
    return verify_circuit_operations(
        operations,
        (X.definition, RX.definition, CZ.definition),
    )


def test_profile_maps_only_operations_present_in_the_bound_circuit() -> None:
    circuit = _circuit(
        _x_call("x-q1", "q1"),
        _measurement("measure-q0", "q0"),
    )

    resolved = _PROFILE.materialize(_parameters(), circuit)

    assert tuple(implementation.key for implementation in resolved.gates) == (
        GatePulseImplementationKey(X.definition.id, (QubitId("q1"),)),
    )
    assert tuple(implementation.id.value for implementation in resolved.gates) == (
        "x.constant[q1]",
    )
    assert tuple(implementation.key for implementation in resolved.measurements) == (
        MeasurementPulseImplementationKey(
            QubitId("q0"),
            INTEGRATED_IQ_RESULT,
        ),
    )
    assert resolved.measurements[0].pulse_template.acquisition_slots[0].id == (
        AcquisitionSlotId("result")
    )


def test_repeated_exact_gate_calls_materialize_one_reusable_implementation() -> None:
    circuit = _circuit(
        _x_call("first", "q0"),
        _x_call("second", "q0"),
    )

    resolved = _PROFILE.materialize(_parameters(), circuit)

    assert len(resolved.gates) == 1


def test_request_cache_reuses_exact_implementations_across_points() -> None:
    cache = PulseRecipeMaterializationCache()
    circuit = _circuit(
        _x_call("x-q0", "q0"),
        _measurement("measure-q0", "q0"),
    )

    first = _PROFILE.materialize(_parameters(), circuit, cache=cache)
    repeated = _PROFILE.materialize(_parameters(), circuit, cache=cache)
    changed = _PROFILE.materialize(
        _parameters(q0_amplitude=0.25),
        circuit,
        cache=cache,
    )

    assert repeated.gates[0] is first.gates[0]
    assert repeated.measurements[0] is first.measurements[0]
    assert changed.gates[0] is not first.gates[0]
    assert changed.measurements[0] is not first.measurements[0]


def test_request_cache_rejects_a_different_profile() -> None:
    cache = PulseRecipeMaterializationCache()
    circuit = _circuit(_x_call("x-q0", "q0"))
    _PROFILE.materialize(_parameters(), circuit, cache=cache)
    other_profile = PulseRecipeProfile[_Parameters](
        map_qubit_pulse_recipes(
            rows=lambda parameters: parameters.qubits,
            qubit=lambda row: row.qubit,
            gates=(_x_recipe,),
        )
    )

    with pytest.raises(ValueError, match="cannot be shared across profiles"):
        other_profile.materialize(_parameters(), circuit, cache=cache)


def test_profile_streams_retained_parallel_each_operations() -> None:
    item = QubitId("$qubit")
    verified = verify_quantum_program(
        QuantumProgramIR(
            QuantumProgramId("mapped-x"),
            ParallelEach(
                entity_set_id="qubits",
                item_id=item,
                entity_ids=(QubitId("q0"), QubitId("q1")),
                operation=GateCall(
                    CircuitOperationId("mapped-x"),
                    X.definition.id,
                    (item,),
                ),
            ),
        ),
        (X.definition,),
    )

    resolved = _PROFILE.materialize_quantum(
        _parameters(),
        verified,
        max_expanded_operations=2,
    )

    assert tuple(implementation.id.value for implementation in resolved.gates) == (
        "x.constant[q0]",
        "x.constant[q1]",
    )


def test_actual_gate_arguments_are_forwarded_into_the_authored_fragment() -> None:
    call = GateCall(
        CircuitOperationId("rx-q0"),
        RX.definition.id,
        (QubitId("q0"),),
        (GateArgument("theta", Quantity(90, "deg")),),
    )

    resolved = _PROFILE.materialize(_parameters(), _circuit(call))

    [implementation] = resolved.gates
    assert implementation.key.arguments == (
        GatePulseImplementationArgument("theta", Quantity(90, "deg")),
    )
    [leaf] = iter_pulse_leaves(implementation.pulse_template.body)
    assert isinstance(leaf, Play)
    assert isinstance(leaf.envelope, Constant)
    assert leaf.envelope.phase == Quantity(90, "deg").to("rad")


def test_point_values_change_fingerprint_not_recipe_identity() -> None:
    circuit = _circuit(_x_call("x-q0", "q0"))
    baseline = _PROFILE.materialize(_parameters(), circuit)
    changed = _PROFILE.materialize(_parameters(q0_amplitude=0.25), circuit)

    [baseline_q0] = baseline.gates
    [changed_q0] = changed.gates
    assert (
        baseline_q0.id == changed_q0.id == _x_recipe.implementation_id((QubitId("q0"),))
    )
    assert baseline_q0.fingerprint != changed_q0.fingerprint


def test_gate_recipe_couplers_are_explicit_validated_resources() -> None:
    coupler_id = CouplerId("c01")

    @gate_pulse_recipe(of=CZ, id="cz.flux")
    def cz_recipe(
        row: _QubitRow,
        _control: quantum.Qubit,
        _target: quantum.Qubit,
        coupler: quantum.Coupler,
    ) -> quantum.QuantumFragment:
        return quantum.play(
            quantum.flux(coupler),
            quantum.constant(
                duration=Quantity(20, "ns"),
                amplitude=row.drive_amplitude,
            ),
        )

    mapping = PulseRecipeMap[_Parameters, _QubitRow](
        rows=lambda parameters: parameters.qubits[:1],
        operands=lambda _row: (QubitId("q0"), QubitId("q1")),
        resources=lambda _row: (coupler_id,),
        gates=(cz_recipe,),
    )
    call = GateCall(
        CircuitOperationId("cz"),
        CZ.definition.id,
        (QubitId("q0"), QubitId("q1")),
    )

    resolved = mapping.materialize(_parameters(), _circuit(call))

    assert resolved.gates[0].resources == (coupler_id,)


def test_profile_rejects_duplicate_recipe_identity() -> None:
    mapping: PulseRecipeMap[_Parameters, _QubitRow] = map_qubit_pulse_recipes(
        rows=lambda parameters: parameters.qubits,
        qubit=lambda row: row.qubit,
        gates=(_x_recipe,),
    )

    with pytest.raises(ValueError, match="recipe ids must be unique"):
        PulseRecipeProfile[_Parameters](mapping, mapping)


def test_scope_keys_separate_candidates_and_leave_readout_on_baseline() -> None:
    baseline = _parameters()
    candidate = _parameters(q0_amplitude=0.6)
    scoped = replace(_x_call("candidate", "q0"), recipe_scope="candidate")
    circuit = _circuit(_x_call("baseline", "q0"), scoped, _measurement("iq", "q0"))
    cache = PulseRecipeMaterializationCache()
    resolved = _PROFILE.materialize(
        baseline, circuit, cache=cache, scoped_parameters={"candidate": candidate}
    )
    by_scope = {entry.key.recipe_scope: entry for entry in resolved.gates}
    assert set(by_scope) == {None, "candidate"}
    amplitudes = {}
    for scope, entry in by_scope.items():
        leaf = next(iter_pulse_leaves(entry.pulse_template.body))
        assert isinstance(leaf, Play)
        assert isinstance(leaf.envelope, Constant)
        amplitudes[scope] = leaf.envelope.amplitude
    assert amplitudes == {None: Quantity(0.2, "arb"), "candidate": Quantity(0.6, "arb")}
    reference = _PROFILE.materialize(
        baseline, circuit=_circuit(_measurement("iq", "q0"))
    )
    assert resolved.measurements == reference.measurements
    changed = _PROFILE.materialize(
        baseline,
        circuit,
        cache=cache,
        scoped_parameters={"candidate": _parameters(q0_amplitude=0.8)},
    )
    changed_by_scope = {entry.key.recipe_scope: entry for entry in changed.gates}
    assert changed_by_scope[None] is by_scope[None]
    assert (
        changed_by_scope["candidate"].fingerprint != by_scope["candidate"].fingerprint
    )
    assert _parameters() == baseline
