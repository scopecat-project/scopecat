from dataclasses import dataclass
from typing import Annotated

import pytest
from scopecat import EntityRef, Quantity, QuantityType
from scopecat.kernel.content_identity import content_fingerprint

from scopecat_quantum import authoring as q
from scopecat_quantum._ids import QubitId, TargetCompileEntryId
from scopecat_quantum.acquisitions import CLASSIFIED_STATE_RESULT, AcquisitionKind
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.gates import GateCall
from scopecat_quantum.programs import estimate_quantum_program_workload
from scopecat_quantum.pulse_recipes import (
    PulseRecipeProfile,
    gate_pulse_recipe,
    map_qubit_pulse_recipes,
    measurement_pulse_recipe,
)
from scopecat_quantum.pulses import (
    Acquire,
    CosineFlatTop,
    DriveSignal,
    Play,
    PulseValidationError,
    ShiftPhase,
)
from scopecat_quantum.realtime import ScheduledBlock

X = q.single_qubit_gate("window.x")


@dataclass(frozen=True)
class Row:
    qubit: QubitId
    gate_ns: int
    readout_ns: int


CALLS: list[str] = []


@gate_pulse_recipe(of=X, id="window.x.recipe")
def x_recipe(row: Row, target: q.Qubit) -> q.QuantumFragment:
    CALLS.append("gate")
    return q.play(
        q.drive(target),
        q.constant(
            duration=Quantity(row.gate_ns, "ns"), amplitude=Quantity(0.2, "arb")
        ),
    )


@measurement_pulse_recipe(
    kind=AcquisitionKind.INTEGRATED_IQ, id="window.readout.recipe"
)
def readout_recipe(row: Row, target: q.Qubit) -> q.QuantumFragment:
    CALLS.append("readout")
    return q.parallel(
        q.play(
            q.readout(target),
            q.constant(
                duration=Quantity(row.readout_ns, "ns"), amplitude=Quantity(0.1, "arb")
            ),
        ),
        q.acquire(target, duration=Quantity(4, "ns"), result="result"),
    )


PROFILE = PulseRecipeProfile[tuple[Row, ...]](
    map_qubit_pulse_recipes(
        rows=lambda rows: rows,
        qubit=lambda row: row.qubit,
        gates=(x_recipe,),
        measurements=(readout_recipe,),
    )
)


@q.program
def window_program(
    target: q.Qubit, amplitude: Annotated[Quantity, QuantityType(unit="V")]
) -> q.QuantumFragment:
    return q.recipe_scope(
        "candidate",
        q.flat_top_window(
            q.flux(target),
            q.sequence(
                X(target),
                q.shift_phase(q.drive(target), Quantity(0.3, "rad")),
                q.measure(target, result="iq"),
            ),
            amplitude=amplitude,
            rise_duration=Quantity(8, "ns"),
            fall_duration=Quantity(12, "ns"),
            settle_duration=Quantity(3, "ns"),
        ),
    )


@pytest.mark.parametrize(("gate_ns", "readout_ns"), [(16, 40), (32, 80)])
def test_window_tracks_recipe_and_complete_measurement_duration(
    gate_ns: int, readout_ns: int
) -> None:
    CALLS.clear()
    compiler = RecipeTargetCompiler(
        PROFILE,
        (Row(QubitId("q0"), 1, readout_ns),),
        scoped_parameters={"candidate": (Row(QubitId("q0"), gate_ns, readout_ns),)},
    )
    result = compiler.compile(
        window_program,
        {
            "target": EntityRef(id="q0", kind="logical_qubit"),
            "amplitude": Quantity(0.4, "V"),
        },
        entry_id=TargetCompileEntryId("window"),
        inspect=True,
    )
    assert CALLS == ["gate", "readout"]
    assert isinstance(result.entry.program.body, ScheduledBlock)
    scheduled = result.entry.program.body.program
    [window] = [
        event
        for event in scheduled.events
        if isinstance(event.instruction, Play)
        and isinstance(event.instruction.envelope, CosineFlatTop)
    ]
    assert float(scheduled.duration_seconds) == pytest.approx(
        (23 + gate_ns + readout_ns) * 1e-9
    )
    assert window.start_seconds == 0
    assert float(window.duration_seconds) == pytest.approx(
        float(scheduled.duration_seconds)
    )
    [drive] = [
        event
        for event in scheduled.events
        if isinstance(event.instruction, Play)
        and isinstance(event.instruction.signal, DriveSignal)
    ]
    assert float(drive.start_seconds) == pytest.approx(11e-9)
    [acquire] = [
        event for event in scheduled.events if isinstance(event.instruction, Acquire)
    ]
    assert float(acquire.start_seconds) == pytest.approx((11 + gate_ns) * 1e-9)
    assert isinstance(acquire.instruction, Acquire)
    assert acquire.instruction.slot_id.local_id == "iq"
    [phase] = [
        event for event in scheduled.events if isinstance(event.instruction, ShiftPhase)
    ]
    assert float(phase.start_seconds) == pytest.approx((11 + gate_ns) * 1e-9)
    assert isinstance(phase.instruction, ShiftPhase)
    assert phase.instruction.phase.value == pytest.approx(0.3)
    [gate] = [
        operation
        for operation in result.bound.verified.operations
        if isinstance(operation, GateCall)
    ]
    assert gate.recipe_scope == "candidate"
    assert result.inspection is not None
    view = result.inspection.project()
    [logical_window] = [
        node
        for layer in view.layers
        if layer.id == "logical"
        for node in layer.nodes
        if node.kind == "flat_top_window"
    ]
    assert logical_window.entity_ids == ("q0",)
    assert any(
        link.source_node_id == logical_window.id
        and link.target_node_id == f"scheduled:event:{window.id.value}"
        for link in view.links
    )
    assert "flat_top_window" in q.draw(window_program)


def test_scanned_values_change_fingerprints_without_rebuilding_recipes() -> None:
    compiler = RecipeTargetCompiler(
        PROFILE,
        (Row(QubitId("q0"), 16, 40),),
        scoped_parameters={"candidate": (Row(QubitId("q0"), 16, 40),)},
    )
    CALLS.clear()
    results = [
        compiler.compile(
            window_program,
            {
                "target": EntityRef(id="q0", kind="logical_qubit"),
                "amplitude": Quantity(value, "V"),
            },
            entry_id=TargetCompileEntryId("point"),
        )
        for value in (0.1, 0.2, 0.1)
    ]
    assert CALLS == ["gate", "readout"]
    hashes = [content_fingerprint(result.entry) for result in results]
    assert hashes[0] == hashes[2] != hashes[1]


def test_window_retains_fragment_expansion_and_entity_mapping() -> None:
    @q.fragment(
        envelope=q.ProgramFamilyEnvelope(
            allowed_gates=(X,), max_operations=1, max_depth=1
        )
    )
    def body(target: q.Qubit) -> q.QuantumFragment:
        return X(target)

    @q.program
    def mapped(targets: q.QubitSet) -> q.QuantumFragment:
        return q.parallel_each(
            targets,
            lambda target: q.flat_top_window(
                q.flux(target),
                body(target),
                amplitude=Quantity(0.1, "V"),
                rise_duration=Quantity(2, "ns"),
                fall_duration=Quantity(3, "ns"),
            ),
        )

    result = RecipeTargetCompiler(
        PROFILE, (Row(QubitId("a"), 16, 20), Row(QubitId("b"), 24, 20))
    ).compile(mapped, {"targets": ("a", "b")}, entry_id=TargetCompileEntryId("mapped"))
    assert isinstance(result.entry.program.body, ScheduledBlock)
    assert float(result.entry.program.body.program.duration_seconds) == pytest.approx(
        29e-9
    )
    assert (
        estimate_quantum_program_workload(result.bound.verified).selected_entity_count
        == 2
    )
    assert {
        gate.qubits[0].value
        for gate in result.bound.verified.operations
        if isinstance(gate, GateCall)
    } == {"a", "b"}


def test_window_rejects_same_signal_body_writes() -> None:
    @q.program
    def overlapping(target: q.Qubit) -> q.QuantumFragment:
        return q.flat_top_window(
            q.drive(target),
            X(target),
            amplitude=Quantity(0.1, "arb"),
            rise_duration=Quantity(2, "ns"),
            fall_duration=Quantity(3, "ns"),
        )

    with pytest.raises(PulseValidationError, match="pulse_signal_overlap"):
        RecipeTargetCompiler(PROFILE, (Row(QubitId("q0"), 16, 20),)).compile(
            overlapping,
            {"target": EntityRef(id="q0", kind="logical_qubit")},
            entry_id=TargetCompileEntryId("bad"),
        )


def test_window_rejects_realtime_body() -> None:
    target = q.qubit("q0")
    measure = q.measure(target, result="state", contract=CLASSIFIED_STATE_RESULT)
    with pytest.raises(ValueError, match="static body"):
        q.flat_top_window(
            q.flux(target),
            q.sequence(measure, q.switch(measure.result, {0: X(target)})),
            amplitude=Quantity(0.1, "V"),
            rise_duration=Quantity(2, "ns"),
            fall_duration=Quantity(3, "ns"),
        )


@pytest.mark.parametrize("capture", [False, True])
def test_empty_and_acquisition_only_bodies_need_no_extra_signal(capture: bool) -> None:
    target = q.qubit("q0")
    body = (
        q.acquire(target, duration=Quantity(4, "ns"), result="iq")
        if capture
        else q.repeat(q.delay(q.drive(target), Quantity(1, "ns")), 0)
    )
    program = q._close_program(
        "minimal",
        q.flat_top_window(
            q.flux(target),
            body,
            amplitude=Quantity(0.1, "V"),
            rise_duration=Quantity(2, "ns"),
            settle_duration=Quantity(1, "ns"),
            fall_duration=Quantity(3, "ns"),
        ),
    )
    result = RecipeTargetCompiler(PROFILE, ()).compile(
        program, {}, entry_id=TargetCompileEntryId("minimal")
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    scheduled = result.entry.program.body.program
    assert len(scheduled.events) == (2 if capture else 1)
    assert float(scheduled.duration_seconds) == pytest.approx(
        (10 if capture else 6) * 1e-9
    )
    if capture:
        [acquisition] = [
            e for e in scheduled.events if isinstance(e.instruction, Acquire)
        ]
        assert float(acquisition.start_seconds) == pytest.approx(3e-9)


def test_window_family_budget_counts_generated_control_play() -> None:
    @q.fragment(
        envelope=q.ProgramFamilyEnvelope(
            allowed_gates=(X,), max_operations=1, max_depth=1
        )
    )
    def body(target: q.Qubit) -> q.QuantumFragment:
        return q.flat_top_window(
            q.flux(target),
            X(target),
            amplitude=Quantity(0.1, "V"),
            rise_duration=Quantity(2, "ns"),
            fall_duration=Quantity(2, "ns"),
        )

    @q.program
    def program(target: q.Qubit) -> q.QuantumFragment:
        return body(target)

    with pytest.raises(ValueError, match="operation"):
        q.bind(program, {"target": "q0"})


def test_window_renders_smooth_edges_and_constant_plateau() -> None:
    import numpy as np

    from scopecat_quantum.pulses import FluxSignal
    from scopecat_quantum.waveforms import (
        Float64ReferenceRenderer,
        IqMatrix,
        SampledOutputBinding,
        SampleGrid,
        plan_sampled_waveforms,
    )

    target = q.qubit("q0")
    program = q._close_program(
        "waveform",
        q.flat_top_window(
            q.flux(target),
            q.delay(q.drive(target), Quantity(4, "ns")),
            amplitude=Quantity(0.4, "V"),
            rise_duration=Quantity(4, "ns"),
            fall_duration=Quantity(4, "ns"),
            settle_duration=Quantity(2, "ns"),
        ),
    )
    result = RecipeTargetCompiler(PROFILE, ()).compile(
        program, {}, entry_id=TargetCompileEntryId("waveform")
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    plan = plan_sampled_waveforms(
        result.entry.program.body.program,
        bindings=(
            SampledOutputBinding(
                signal=FluxSignal(QubitId("q0")),
                i_lane=0,
                q_lane=1,
                intermediate_frequency_hz=0,
                mixer=IqMatrix(ii=1, iq=0, qi=0, qq=1),
            ),
        ),
        grid=SampleGrid(1_000_000_000, sample_location="left_edge"),
    )
    actual = Float64ReferenceRenderer().render(plan).buffers[0]
    expected = np.concatenate(
        (
            0.2 * (1 - np.cos(np.pi * np.arange(4) / 4)),
            np.full(6, 0.4),
            0.2 * (1 + np.cos(np.pi * np.arange(4) / 4)),
        )
    )
    np.testing.assert_allclose(actual, expected, atol=1e-15)


@pytest.mark.parametrize("offset_ns", [0, 30])
def test_scanned_acquisition_delay_is_inside_the_window(offset_ns: int) -> None:
    @q.program
    def program(
        target: q.Qubit, offset: Annotated[Quantity, QuantityType(unit="ns")]
    ) -> q.QuantumFragment:
        acquisition = q.acquire(target, duration=Quantity(10, "ns"), result="iq")
        return q.flat_top_window(
            q.flux(target),
            q.parallel(
                q.play(
                    q.readout(target),
                    q.constant(
                        duration=Quantity(20, "ns"), amplitude=Quantity(0.1, "arb")
                    ),
                ),
                q.sequence(q.delay(acquisition.signal, offset), acquisition),
            ),
            amplitude=Quantity(0.1, "V"),
            rise_duration=Quantity(2, "ns"),
            fall_duration=Quantity(3, "ns"),
        )

    result = RecipeTargetCompiler(PROFILE, ()).compile(
        program,
        {"target": "q0", "offset": Quantity(offset_ns, "ns")},
        entry_id=TargetCompileEntryId("offset"),
    )
    assert isinstance(result.entry.program.body, ScheduledBlock)
    scheduled = result.entry.program.body.program
    [acquisition] = [
        event for event in scheduled.events if isinstance(event.instruction, Acquire)
    ]
    assert float(acquisition.start_seconds) == pytest.approx((2 + offset_ns) * 1e-9)
    assert float(scheduled.duration_seconds) == pytest.approx(
        (5 + max(20, offset_ns + 10)) * 1e-9
    )


def test_negative_acquisition_delay_is_rejected() -> None:
    target = q.qubit("q0")
    acquisition = q.acquire(target, duration=Quantity(10, "ns"), result="iq")
    program = q._close_program(
        "negative-offset",
        q.sequence(q.delay(acquisition.signal, Quantity(-1, "ns")), acquisition),
    )
    with pytest.raises(ValueError, match="delay duration must be non-negative"):
        RecipeTargetCompiler(PROFILE, ()).compile(
            program, {}, entry_id=TargetCompileEntryId("negative")
        )
