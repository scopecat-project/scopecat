from dataclasses import replace
from decimal import Decimal

import numpy as np
import pytest
from pydantic import ValidationError
from scopecat import Quantity

from scopecat_quantum._ids import PulseEventId, PulseProgramId, QubitId
from scopecat_quantum.pulses import (
    Constant,
    Delay,
    DriveSignal,
    Play,
    PulseProgram,
    Sequence,
    schedule,
)
from scopecat_quantum.timing_corrections import (
    SignalTimeShift,
    TimingCorrectionSnapshot,
    apply_timing_corrections,
)
from scopecat_quantum.waveforms import (
    Float64ReferenceRenderer,
    IqMatrix,
    SampledOutputBinding,
    SampleGrid,
    TimingQuantizationPolicy,
    plan_sampled_waveforms,
)

SIGNAL = DriveSignal(QubitId("q0"))


def snapshot(seconds: str) -> TimingCorrectionSnapshot:
    return TimingCorrectionSnapshot(
        sample_rate_hz=1_000_000_000,
        shifts=(
            SignalTimeShift(signal=("drive", "qubit", "q0"), seconds=Decimal(seconds)),
        ),
    )


def fixture_plan():
    program = schedule(
        PulseProgram(
            PulseProgramId("timing"),
            Sequence(
                (
                    Delay(PulseEventId("before"), SIGNAL, Quantity(4, "ns")),
                    Play(
                        PulseEventId("play"),
                        SIGNAL,
                        Constant(Quantity(4, "ns"), Quantity(0.2, "arb")),
                    ),
                    Delay(PulseEventId("after"), SIGNAL, Quantity(4, "ns")),
                )
            ),
        )
    )
    plan = plan_sampled_waveforms(
        program,
        bindings=(
            SampledOutputBinding(
                SIGNAL, 0, 1, 37_000_000, IqMatrix(1.0, 0.0, 0.0, 1.0)
            ),
        ),
        grid=SampleGrid(1_000_000_000, TimingQuantizationPolicy("strict")),
    )
    return program, plan


@pytest.mark.parametrize("samples", [-4, -2, 0, 2, 4])
def test_shift_preserves_samples_phase_and_requested_coordinates(samples: int):
    program, plan = fixture_plan()
    corrected = apply_timing_corrections(
        plan, program, snapshot(str(Decimal(samples) / 10**9))
    )
    baseline = Float64ReferenceRenderer().render(plan)
    actual = Float64ReferenceRenderer().render(corrected)
    for before, after in zip(baseline.buffers, actual.buffers, strict=True):
        np.testing.assert_array_equal(after[4 + samples : 8 + samples], before[4:8])
    timing = corrected.timing_for(PulseEventId("play"))
    assert timing.requested_start_seconds == Decimal("4e-9")
    assert timing.start_sample == 4 + samples
    assert timing.correction_samples == samples
    assert timing.start_error_seconds == 0
    if samples == 0:
        assert corrected == plan


@pytest.mark.parametrize(
    "seconds",
    [
        "0.5e-9",
        "-0.5e-9",
        "NaN",
        "Infinity",
        "-Infinity",
        "1.00000000000000000000000000001e-9",
    ],
)
def test_invalid_shift_rejected(seconds: str):
    with pytest.raises((ValueError, ValidationError)):
        snapshot(seconds)


@pytest.mark.parametrize("seconds", ["-5e-9", "5e-9"])
def test_shift_outside_record_rejected(seconds: str):
    program, plan = fixture_plan()
    with pytest.raises(ValueError, match="outside record"):
        apply_timing_corrections(plan, program, snapshot(seconds))


def test_snapshot_roundtrip_and_duplicate_signal_rejection():
    selected = snapshot("1e-9")
    assert (
        TimingCorrectionSnapshot.model_validate_json(selected.model_dump_json())
        == selected
    )
    with pytest.raises(ValueError, match="unique"):
        TimingCorrectionSnapshot(
            sample_rate_hz=selected.sample_rate_hz, shifts=selected.shifts * 2
        )


def test_reject_window_wrong_grid_and_double_application():
    program, plan = fixture_plan()
    selected = snapshot("1e-9")
    for invalid in (
        replace(plan, sample_count=10),
        replace(plan, time_origin_seconds=Decimal("1e-9")),
        replace(plan, grid=SampleGrid(1_000_000_000)),
        replace(plan, grid=SampleGrid(2_000_000_000)),
    ):
        with pytest.raises(ValueError, match="timing correction"):
            apply_timing_corrections(invalid, program, selected)
    with pytest.raises(ValueError, match="uncorrected"):
        apply_timing_corrections(
            apply_timing_corrections(plan, program, selected), program, selected
        )


def test_repeated_event_ids_keep_occurrences():
    program, plan = fixture_plan()
    # A repeated occurrence can be emitted by retained control-flow lowering.
    repeated_timing = replace(
        plan.event_timings[1], start_sample=8, requested_start_seconds=Decimal("8e-9")
    )
    repeated_event = replace(program.events[1], start_seconds=Decimal("8e-9"))
    object.__setattr__(
        program, "events", (*program.events[:2], repeated_event, *program.events[2:])
    )
    plan = replace(
        plan,
        event_timings=(
            *plan.event_timings[:2],
            repeated_timing,
            *plan.event_timings[2:],
        ),
        render_events=(
            *plan.render_events,
            replace(plan.render_events[0], timing=repeated_timing),
        ),
    )
    plan = replace(plan, source_events=program.events)
    corrected = apply_timing_corrections(plan, program, snapshot("-1e-9"))
    assert [t.start_sample for t in corrected.timings_for(PulseEventId("play"))] == [
        3,
        7,
    ]
    assert [e.timing.start_sample for e in corrected.render_events] == [3, 7]


def test_phase_factoring_rejects_different_translation_coordinates():
    from scopecat_quantum.waveforms import factor_phase_parameterized_waveforms

    program, plan = fixture_plan()
    corrected = apply_timing_corrections(plan, program, snapshot("1e-9"))
    # Same realized output interval, different pre-correction carrier positions.
    shifted = corrected.render_events[0]
    other_timing = replace(shifted.timing, correction_samples=0)
    other = replace(corrected, render_events=(replace(shifted, timing=other_timing),))
    assert factor_phase_parameterized_waveforms((corrected, other)) is None


def test_reject_mismatched_source_coordinates():
    program, plan = fixture_plan()
    wrong = replace(plan.event_timings[1], requested_start_seconds=Decimal("3e-9"))
    plan = replace(
        plan, event_timings=(plan.event_timings[0], wrong, plan.event_timings[2])
    )
    with pytest.raises(ValueError, match="occurrence order"):
        apply_timing_corrections(plan, program, snapshot("1e-9"))


def test_same_ids_and_times_with_different_signal_rejected():
    program, plan = fixture_plan()
    events = tuple(
        replace(
            event,
            instruction=replace(
                event.instruction, signal=DriveSignal(QubitId("other"))
            ),
        )
        for event in program.events
    )
    object.__setattr__(program, "events", events)
    with pytest.raises(ValueError, match="complete record"):
        apply_timing_corrections(plan, program, snapshot("1e-9"))
