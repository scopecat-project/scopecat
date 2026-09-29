from __future__ import annotations

import math
from decimal import Decimal
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from scopecat import Quantity

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    PulseEventId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import INTEGRATED_IQ_RESULT
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    AcquisitionSlot,
    Constant,
    CosineFlatTop,
    Delay,
    DerivativeQuadrature,
    DriveSignal,
    EnvelopePhaseReference,
    FrequencyShift,
    Gaussian,
    Parallel,
    Play,
    PulseInstruction,
    PulseProgram,
    PulseValidationError,
    ReadoutSignal,
    Sequence,
    ShiftPhase,
    schedule,
)

Q0 = QubitId("q0")
Q1 = QubitId("q1")
DRIVE_Q0 = DriveSignal(Q0)
DRIVE_Q1 = DriveSignal(Q1)


def _constant(duration: float, unit: str = "ns") -> Constant:
    return Constant(
        duration=Quantity(duration, unit),
        amplitude=Quantity(0.5, "ratio"),
    )


def _play(event_id: str, signal: DriveSignal, duration_ns: int) -> Play:
    return Play(
        id=PulseEventId(event_id),
        signal=signal,
        envelope=_constant(duration_ns),
    )


def _program(body: PulseInstruction) -> PulseProgram:
    return PulseProgram(id=PulseProgramId("program"), body=body)


def _issue_codes(error: PulseValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def test_schedule_normalizes_quantities_and_flattens_authoring_tree() -> None:
    program = _program(
        Sequence(
            (
                Play(
                    id=PulseEventId("readout"),
                    signal=ReadoutSignal(Q0),
                    envelope=Gaussian(
                        duration=Quantity(2, "us"),
                        amplitude=Quantity(500, "mV"),
                        sigma=Quantity(250, "ns"),
                        phase=Quantity(180, "deg"),
                    ),
                ),
                Delay(
                    id=PulseEventId("wait"),
                    signal=DRIVE_Q0,
                    duration=Quantity(20, "ns"),
                ),
            )
        )
    )

    scheduled = schedule(program)

    assert scheduled.duration_seconds == Decimal("2.02e-6")
    assert [event.id.value for event in scheduled.events] == ["readout", "wait"]
    assert scheduled.events[1].start_seconds == Decimal("2e-6")
    envelope = cast("Play", scheduled.events[0].instruction).envelope
    assert isinstance(envelope, Gaussian)
    assert envelope.duration == Quantity(2e-6, "s")
    assert envelope.sigma == Quantity(2.5e-7, "s")
    assert envelope.amplitude == Quantity(0.5, "V")
    assert envelope.phase == Quantity(math.pi, "rad")


def test_shift_phase_is_a_normalized_zero_duration_frame_event() -> None:
    shift = ShiftPhase(
        id=PulseEventId("z-shift"),
        signal=DRIVE_Q0,
        phase=Quantity(180, "deg"),
    )
    scheduled = schedule(
        _program(
            Sequence(
                (
                    shift,
                    _play("a-play", DRIVE_Q0, 10),
                )
            )
        )
    )

    assert scheduled.duration_seconds == Decimal("1e-8")
    assert [event.id.value for event in scheduled.events] == ["z-shift", "a-play"]
    assert [event.start_seconds for event in scheduled.events] == [
        Decimal(0),
        Decimal(0),
    ]
    assert [event.duration_seconds for event in scheduled.events] == [
        Decimal(0),
        Decimal("1e-8"),
    ]
    normalized = cast("ShiftPhase", scheduled.events[0].instruction)
    assert normalized.signal is DRIVE_Q0
    assert normalized.phase == Quantity(math.pi, "rad")


def test_zero_duration_sequence_causality_survives_reassociation() -> None:
    first = ShiftPhase(
        PulseEventId("z-first"),
        DRIVE_Q1,
        Quantity(90, "deg"),
    )
    second = ShiftPhase(
        PulseEventId("a-second"),
        DRIVE_Q0,
        Quantity(-0.25, "rad"),
    )
    play = _play("a-play", DRIVE_Q0, 5)

    left = schedule(_program(Sequence((Sequence((first, second)), play))))
    right = schedule(_program(Sequence((first, Sequence((second, play))))))

    assert left == right
    assert [event.id.value for event in left.events] == [
        "z-first",
        "a-second",
        "a-play",
    ]
    assert all(event.start_seconds == 0 for event in left.events)


def test_parallel_phase_shifts_are_canonical_and_preserve_signal_identity() -> None:
    drive_shift = ShiftPhase(
        PulseEventId("drive"),
        DRIVE_Q0,
        Quantity(0.5, "rad"),
    )
    readout_signal = ReadoutSignal(Q1)
    readout_shift = ShiftPhase(
        PulseEventId("readout"),
        readout_signal,
        Quantity(-90, "deg"),
    )

    first = schedule(_program(Parallel((readout_shift, drive_shift))))
    second = schedule(_program(Parallel((drive_shift, readout_shift))))

    assert first == second
    assert first.duration_seconds == 0
    assert [cast("ShiftPhase", event.instruction).signal for event in first.events] == [
        DRIVE_Q0,
        readout_signal,
    ]


def test_same_time_frame_chain_precedes_parallel_waveform_sampling() -> None:
    first_shift = ShiftPhase(
        PulseEventId("z-first-shift"),
        DRIVE_Q0,
        Quantity(0.25, "rad"),
    )
    second_shift = ShiftPhase(
        PulseEventId("z-shift"),
        DRIVE_Q0,
        Quantity(0.5, "rad"),
    )
    play = _play("a-play", DRIVE_Q0, 5)

    scheduled = schedule(
        _program(
            Parallel(
                (
                    Sequence((first_shift, second_shift)),
                    play,
                )
            )
        )
    )

    assert [event.id.value for event in scheduled.events] == [
        "z-first-shift",
        "z-shift",
        "a-play",
    ]
    assert [event.duration_seconds for event in scheduled.events] == [
        Decimal(0),
        Decimal(0),
        Decimal("5e-9"),
    ]


def test_parallel_shift_at_play_start_is_applied_before_the_play() -> None:
    scheduled = schedule(
        _program(
            Parallel(
                (
                    _play("a-play", DRIVE_Q0, 20),
                    ShiftPhase(
                        PulseEventId("z-shift"),
                        DRIVE_Q0,
                        Quantity(0.25, "rad"),
                    ),
                )
            )
        )
    )

    assert [event.id.value for event in scheduled.events] == ["z-shift", "a-play"]
    assert {event.start_seconds for event in scheduled.events} == {Decimal(0)}


def test_shift_phase_cannot_occur_inside_an_active_parallel_play() -> None:
    program = _program(
        Parallel(
            (
                _play("active-play", DRIVE_Q0, 20),
                Sequence(
                    (
                        Delay(
                            PulseEventId("offset"),
                            DRIVE_Q1,
                            Quantity(10, "ns"),
                        ),
                        ShiftPhase(
                            PulseEventId("mid-play-shift"),
                            DRIVE_Q0,
                            Quantity(0.25, "rad"),
                        ),
                    )
                ),
            )
        )
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(program)

    assert _issue_codes(raised.value) == {"pulse_frame_shift_during_play"}


def test_shift_phase_rejects_invalid_phases() -> None:
    program = _program(
        ShiftPhase(
            PulseEventId("nonfinite"),
            DRIVE_Q0,
            Quantity(float("inf"), "rad"),
        )
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(program)

    assert {
        "pulse_quantity_nonfinite",
    } <= _issue_codes(raised.value)


def test_canonical_order_uses_structural_identity_not_rendered_text() -> None:
    structurally_first_event = PulseEventId("event", scope=("a", "b"))
    rendered_first_event = PulseEventId("event", scope=("a/b",))
    structurally_first_slot = AcquisitionSlotId("slot", scope=("a", "b"))
    rendered_first_slot = AcquisitionSlotId("slot", scope=("a/b",))
    assert rendered_first_event.value < structurally_first_event.value
    assert rendered_first_slot.value < structurally_first_slot.value

    q0_acquire = AcquireSignal(Q0)
    q1_acquire = AcquireSignal(Q1)
    slots = (
        AcquisitionSlot(
            rendered_first_slot,
            INTEGRATED_IQ_RESULT,
            q1_acquire,
        ),
        AcquisitionSlot(
            structurally_first_slot,
            INTEGRATED_IQ_RESULT,
            q0_acquire,
        ),
    )
    scheduled = schedule(
        PulseProgram(
            id=PulseProgramId("structural-order"),
            body=Parallel(
                (
                    ShiftPhase(
                        rendered_first_event,
                        DRIVE_Q0,
                        Quantity(0.25, "rad"),
                    ),
                    ShiftPhase(
                        structurally_first_event,
                        DRIVE_Q0,
                        Quantity(0.5, "rad"),
                    ),
                    Acquire(
                        PulseEventId("acquire-q1"),
                        q1_acquire,
                        rendered_first_slot,
                        Quantity(1, "ns"),
                    ),
                    Acquire(
                        PulseEventId("acquire-q0"),
                        q0_acquire,
                        structurally_first_slot,
                        Quantity(1, "ns"),
                    ),
                )
            ),
            acquisition_slots=slots,
        )
    )

    shift_ids = tuple(
        event.id
        for event in scheduled.events
        if isinstance(event.instruction, ShiftPhase)
    )
    assert shift_ids == (structurally_first_event, rendered_first_event)
    assert tuple(slot.id for slot in scheduled.acquisition_slots) == (
        structurally_first_slot,
        rendered_first_slot,
    )


@given(duration_us=st.integers(min_value=1, max_value=1_000_000))
def test_time_unit_normalization_is_canonical(duration_us: int) -> None:
    in_us = _program(
        Play(PulseEventId("pulse"), DRIVE_Q0, _constant(duration_us, "us"))
    )
    in_ns = _program(
        Play(PulseEventId("pulse"), DRIVE_Q0, _constant(duration_us * 1000, "ns"))
    )

    assert schedule(in_us) == schedule(in_ns)


def test_acquisition_slots_are_closed_exactly_once() -> None:
    signal = AcquireSignal(Q0)
    slot = AcquisitionSlot(
        id=AcquisitionSlotId("iq", scope=("measure-q0",)),
        contract=INTEGRATED_IQ_RESULT,
        signal=signal,
    )
    program = PulseProgram(
        id=PulseProgramId("measurement"),
        body=Acquire(
            id=PulseEventId("acquire"),
            signal=signal,
            slot_id=slot.id,
            duration=Quantity(1, "us"),
        ),
        acquisition_slots=(slot,),
    )

    scheduled = schedule(program)

    assert scheduled.acquisition_slots == (slot,)
    assert cast("Acquire", scheduled.events[0].instruction).duration == Quantity(
        1e-6, "s"
    )


@given(use_count=st.integers(min_value=0, max_value=3))
def test_acquisition_closure_holds_for_every_use_count(use_count: int) -> None:
    signal = AcquireSignal(Q0)
    slot = AcquisitionSlot(AcquisitionSlotId("slot"), INTEGRATED_IQ_RESULT, signal)
    program = PulseProgram(
        id=PulseProgramId("closure"),
        body=Sequence(
            tuple(
                Acquire(
                    PulseEventId(f"acquire-{index}"),
                    signal,
                    slot.id,
                    Quantity(10, "ns"),
                )
                for index in range(use_count)
            )
        ),
        acquisition_slots=(slot,),
    )

    if use_count == 1:
        assert schedule(program).acquisition_slots == (slot,)
        return
    with pytest.raises(PulseValidationError) as raised:
        schedule(program)
    expected = (
        "pulse_acquisition_slot_missing"
        if use_count == 0
        else "pulse_acquisition_slot_multiple"
    )
    assert expected in _issue_codes(raised.value)


def test_acquisition_closure_reports_missing_undeclared_and_multiple_uses() -> None:
    signal = AcquireSignal(Q0)
    declared = AcquisitionSlot(
        AcquisitionSlotId("declared"), INTEGRATED_IQ_RESULT, signal
    )
    used_twice = AcquisitionSlot(
        AcquisitionSlotId("twice"), INTEGRATED_IQ_RESULT, signal
    )
    program = PulseProgram(
        id=PulseProgramId("bad-acquisitions"),
        body=Sequence(
            (
                Acquire(
                    PulseEventId("first"), signal, used_twice.id, Quantity(1, "us")
                ),
                Acquire(
                    PulseEventId("second"), signal, used_twice.id, Quantity(1, "us")
                ),
                Acquire(
                    PulseEventId("unknown"),
                    signal,
                    AcquisitionSlotId("unknown"),
                    Quantity(1, "us"),
                ),
            )
        ),
        acquisition_slots=(declared, used_twice),
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(program)

    assert {
        "pulse_acquisition_slot_missing",
        "pulse_acquisition_slot_multiple",
        "pulse_acquisition_slot_undeclared",
    } <= _issue_codes(raised.value)


def test_parallel_intervals_cannot_overlap_on_one_logical_signal() -> None:
    program = _program(
        Parallel(
            (
                _play("first", DRIVE_Q0, 20),
                Sequence(
                    (
                        Delay(PulseEventId("offset"), DRIVE_Q1, Quantity(5, "ns")),
                        _play("second", DRIVE_Q0, 10),
                    )
                ),
            )
        )
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(program)

    assert "pulse_signal_overlap" in _issue_codes(raised.value)


@given(
    data=st.data(),
    first_duration=st.integers(min_value=2, max_value=10_000),
    second_duration=st.integers(min_value=1, max_value=10_000),
)
def test_any_strictly_early_start_on_one_signal_is_an_overlap(
    data: st.DataObject, first_duration: int, second_duration: int
) -> None:
    offset = data.draw(st.integers(min_value=1, max_value=first_duration - 1))
    program = _program(
        Parallel(
            (
                _play("first", DRIVE_Q0, first_duration),
                Sequence(
                    (
                        Delay(
                            PulseEventId("offset"),
                            DRIVE_Q1,
                            Quantity(offset, "ns"),
                        ),
                        _play("second", DRIVE_Q0, second_duration),
                    )
                ),
            )
        )
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(program)
    assert "pulse_signal_overlap" in _issue_codes(raised.value)


def test_touching_intervals_on_one_signal_are_legal() -> None:
    scheduled = schedule(
        _program(
            Sequence(
                (
                    _play("first", DRIVE_Q0, 20),
                    _play("second", DRIVE_Q0, 10),
                )
            )
        )
    )

    assert [event.start_seconds for event in scheduled.events] == [
        Decimal(0),
        Decimal("2e-8"),
    ]


def test_duplicate_instruction_ids_are_rejected_across_composites() -> None:
    duplicate = PulseEventId("duplicate")
    program = _program(
        Parallel(
            (
                Play(duplicate, DRIVE_Q0, _constant(10)),
                Play(duplicate, DRIVE_Q1, _constant(10)),
            )
        )
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(program)

    assert "pulse_instruction_duplicate" in _issue_codes(raised.value)


def test_invalid_units_and_durations_are_aggregated() -> None:
    invalid_play = Play(
        id=PulseEventId("play"),
        signal=DRIVE_Q0,
        envelope=Constant(
            duration=Quantity(-1, "ns"),
            amplitude=Quantity(1, "Hz"),
            phase=Quantity(1, "V"),
        ),
    )
    invalid_delay = Delay(
        id=PulseEventId("delay"),
        signal=DRIVE_Q1,
        duration=Quantity(1, "GHz"),
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(_program(Parallel((invalid_play, invalid_delay))))

    assert {
        "pulse_amplitude_unit_invalid",
        "pulse_duration_nonpositive",
        "pulse_phase_unit_invalid",
        "pulse_time_unit_invalid",
    } <= _issue_codes(raised.value)


def test_gaussian_and_derivative_shape_parameters_are_validated() -> None:
    gaussian = Play(
        PulseEventId("gaussian"),
        DRIVE_Q0,
        Gaussian(
            duration=Quantity(10, "ns"),
            amplitude=Quantity(0.2, "arb"),
            sigma=Quantity(20, "ns"),
        ),
    )
    drag = Play(
        PulseEventId("drag"),
        DRIVE_Q1,
        DerivativeQuadrature(
            envelope=Gaussian(
                duration=Quantity(20, "ns"),
                amplitude=Quantity(0.2, "arb"),
                sigma=Quantity(5, "ns"),
            ),
            beta=Quantity(2, "MHz"),
        ),
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(_program(Parallel((gaussian, drag))))

    assert {
        "pulse_sigma_exceeds_duration",
        "pulse_time_unit_invalid",
    } <= _issue_codes(raised.value)


def test_frequency_shift_normalizes_offset_and_validates_its_reference() -> None:
    scheduled = schedule(
        _program(
            Play(
                PulseEventId("shifted"),
                DRIVE_Q0,
                FrequencyShift(
                    envelope=Constant(
                        duration=Quantity(20, "ns"),
                        amplitude=Quantity(0.2, "arb"),
                    ),
                    frequency_offset=Quantity(-1.5, "MHz"),
                    phase_reference="start",
                ),
            )
        )
    )

    envelope = cast("Play", scheduled.events[0].instruction).envelope
    assert isinstance(envelope, FrequencyShift)
    assert envelope.frequency_offset == Quantity(-1.5e6, "Hz")
    assert envelope.phase_reference == "start"

    with pytest.raises(PulseValidationError) as raised:
        schedule(
            _program(
                Play(
                    PulseEventId("invalid-shift"),
                    DRIVE_Q0,
                    FrequencyShift(
                        envelope=Constant(
                            duration=Quantity(20, "ns"),
                            amplitude=Quantity(0.2, "arb"),
                        ),
                        frequency_offset=Quantity(1, "ns"),
                        phase_reference=cast(
                            "EnvelopePhaseReference",
                            cast("object", "end"),
                        ),
                    ),
                )
            )
        )

    assert "pulse_frequency_reference_invalid" in _issue_codes(raised.value)


def test_cosine_flat_top_normalizes_explicit_edge_durations() -> None:
    scheduled = schedule(
        _program(
            Play(
                PulseEventId("cosine"),
                DRIVE_Q0,
                CosineFlatTop(
                    duration=Quantity(0.2, "us"),
                    amplitude=Quantity(200, "mV"),
                    rise_duration=Quantity(1, "ns"),
                    fall_duration=Quantity(2, "ns"),
                    phase=Quantity(90, "deg"),
                ),
            )
        )
    )

    envelope = cast("Play", scheduled.events[0].instruction).envelope
    assert isinstance(envelope, CosineFlatTop)
    assert envelope.duration == Quantity(2e-7, "s")
    assert envelope.amplitude == Quantity(0.2, "V")
    assert envelope.rise_duration == Quantity(1e-9, "s")
    assert envelope.fall_duration == Quantity(2e-9, "s")
    assert envelope.phase == Quantity(math.pi / 2, "rad")


def test_cosine_flat_top_rejects_negative_or_overlapping_edges() -> None:
    negative = Play(
        PulseEventId("negative"),
        DRIVE_Q0,
        CosineFlatTop(
            duration=Quantity(10, "ns"),
            amplitude=Quantity(1, "arb"),
            rise_duration=Quantity(-1, "ns"),
            fall_duration=Quantity(1, "ns"),
        ),
    )
    overlapping = Play(
        PulseEventId("overlapping"),
        DRIVE_Q1,
        CosineFlatTop(
            duration=Quantity(10, "ns"),
            amplitude=Quantity(1, "arb"),
            rise_duration=Quantity(6, "ns"),
            fall_duration=Quantity(6, "ns"),
        ),
    )

    with pytest.raises(PulseValidationError) as raised:
        schedule(_program(Parallel((negative, overlapping))))

    assert {
        "pulse_edge_duration_negative",
        "pulse_edge_durations_exceed_duration",
    } <= _issue_codes(raised.value)
