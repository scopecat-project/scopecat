from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

import numpy as np
import pytest
from scopecat import Quantity

from scopecat_quantum._ids import PulseEventId, PulseProgramId, QubitId
from scopecat_quantum.pulses import (
    Constant,
    CosineFlatTop,
    Delay,
    DerivativeQuadrature,
    DriveSignal,
    FrequencyShift,
    Gaussian,
    Parallel,
    Play,
    PulseProgram,
    ReadoutSignal,
    Sequence,
    ShiftPhase,
    schedule,
)
from scopecat_quantum.waveforms import (
    CONTINUOUS_LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID,
    CONTINUOUS_MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID,
    LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID,
    MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID,
    CarrierPhaseReference,
    Float64ReferenceRenderer,
    IqMatrix,
    SampledOutputBinding,
    SampledWaveformPlan,
    SampleGrid,
    TimingQuantizationPolicy,
    WaveformPlanningError,
    factor_phase_parameterized_waveforms,
    plan_sampled_waveform_window,
    plan_sampled_waveforms,
    realize_event_timings,
    resolve_waveform_events,
)

Q0 = QubitId("q0")
Q1 = QubitId("q1")
DRIVE_Q0 = DriveSignal(Q0)
READOUT_Q0 = ReadoutSignal(Q0)
READOUT_Q1 = ReadoutSignal(Q1)
IDENTITY_IQ = IqMatrix(ii=1.0, iq=0.0, qi=0.0, qq=1.0)


def test_zero_duration_constant_preserves_scan_reference_without_output_or_delay() -> (
    None
):
    program = schedule(
        PulseProgram(
            PulseProgramId("zero-reference"),
            Sequence(
                (
                    Play(
                        PulseEventId("empty-drive"),
                        DRIVE_Q0,
                        Constant(Quantity(0, "ns"), Quantity(0.8, "arb")),
                    ),
                    Play(
                        PulseEventId("readout"),
                        READOUT_Q0,
                        Constant(Quantity(8, "ns"), Quantity(0.2, "arb")),
                    ),
                )
            ),
        )
    )
    plan = plan_sampled_waveforms(
        program,
        bindings=(
            SampledOutputBinding(DRIVE_Q0, 0, 1, 0, IDENTITY_IQ),
            SampledOutputBinding(READOUT_Q0, 2, 3, 0, IDENTITY_IQ),
        ),
        grid=SampleGrid(1_000_000_000),
    )
    rendered = Float64ReferenceRenderer().render(plan)
    np.testing.assert_array_equal(rendered.buffers[0], np.zeros(8))
    np.testing.assert_allclose(rendered.buffers[2], np.full(8, 0.2))
    assert program.duration_seconds == Decimal("8e-9")


@pytest.mark.parametrize("frequency_hz", [-125_000_000, 125_000_000])
def test_signed_if_preserves_i_and_reverses_q_in_rendered_buffers(
    frequency_hz: float,
) -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("signed-if"),
            Play(
                PulseEventId("drive"),
                DRIVE_Q0,
                Constant(Quantity(8, "ns"), Quantity(0.25, "arb")),
            ),
        )
    )
    plan = plan_sampled_waveforms(
        program,
        bindings=(SampledOutputBinding(DRIVE_Q0, 0, 1, frequency_hz, IDENTITY_IQ),),
        grid=SampleGrid(1_000_000_000, sample_location="left_edge"),
    )
    rendered = Float64ReferenceRenderer().render(plan)
    phase = 2 * np.pi * frequency_hz * np.arange(8) / 1e9
    np.testing.assert_allclose(rendered.buffers[0], 0.25 * np.cos(phase), atol=1e-15)
    np.testing.assert_allclose(rendered.buffers[1], 0.25 * np.sin(phase), atol=1e-15)


def _binding(signal: DriveSignal | ReadoutSignal) -> SampledOutputBinding:
    return SampledOutputBinding(
        signal=signal,
        i_lane=0,
        q_lane=1,
        intermediate_frequency_hz=0.0,
        mixer=IDENTITY_IQ,
    )


def _phase_plan(phase: float, *, amplitude: float = 0.25) -> SampledWaveformPlan:
    return plan_sampled_waveforms(
        schedule(
            PulseProgram(
                PulseProgramId(f"phase-{phase}-{amplitude}"),
                Play(
                    PulseEventId("play"),
                    DRIVE_Q0,
                    Constant(
                        Quantity(4, "ns"),
                        Quantity(amplitude, "arb"),
                        phase=Quantity(phase, "rad"),
                    ),
                ),
            )
        ),
        bindings=(_binding(DRIVE_Q0),),
        grid=SampleGrid(1_000_000_000),
    )


def test_phase_parameterization_factors_only_phase_rows() -> None:
    plans = (_phase_plan(0.0), _phase_plan(math.pi / 2))

    factored = factor_phase_parameterized_waveforms(plans)

    assert factored is not None
    assert factored.phase_rows == ((0.0,), (math.pi / 2,))
    assert factored.template.render_events[0].effective_phase_radians == 0.0
    assert (
        factor_phase_parameterized_waveforms(
            (plans[0], _phase_plan(0.0, amplitude=0.5))
        )
        is None
    )


def test_nearest_quantizes_shared_absolute_boundaries_once() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("fractional-sequence"),
            Sequence(
                tuple(
                    Play(
                        PulseEventId(f"play-{index}"),
                        DRIVE_Q0,
                        Constant(Quantity(2.4, "ns"), Quantity(index + 1, "arb")),
                    )
                    for index in range(4)
                )
            ),
        )
    )

    plan = plan_sampled_waveforms(
        program,
        bindings=(_binding(DRIVE_Q0),),
        grid=SampleGrid(1_000_000_000),
    )

    assert plan.sample_count == 10
    assert tuple(timing.start_sample for timing in plan.event_timings) == (0, 2, 5, 7)
    assert tuple(timing.sample_count for timing in plan.event_timings) == (2, 3, 2, 3)
    assert tuple(timing.end_sample for timing in plan.event_timings) == (2, 5, 7, 10)


def test_timing_only_planner_preserves_requested_and_realized_boundaries() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("timing-only"),
            Sequence(
                (
                    Play(
                        PulseEventId("first"),
                        DRIVE_Q0,
                        Constant(Quantity(0.5, "ns"), Quantity(1, "arb")),
                    ),
                    Play(
                        PulseEventId("second"),
                        DRIVE_Q0,
                        Constant(Quantity(1.5, "ns"), Quantity(1, "arb")),
                    ),
                )
            ),
        )
    )

    timings = realize_event_timings(
        program.events,
        grid=SampleGrid(
            2_000_000_000,
            TimingQuantizationPolicy(mode="strict"),
        ),
    )

    assert tuple(timing.start_sample for timing in timings) == (0, 1)
    assert tuple(timing.sample_count for timing in timings) == (1, 3)
    assert all(timing.start_error_seconds == 0 for timing in timings)
    assert all(timing.duration_error_seconds == 0 for timing in timings)


def test_timing_only_planner_retains_nearest_grid_error() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("timing-nearest"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(2.4, "ns"), Quantity(1, "arb")),
            ),
        )
    )

    [timing] = realize_event_timings(
        program.events,
        grid=SampleGrid(1_000_000_000),
    )

    assert timing.sample_count == 2
    assert timing.requested_duration_seconds == Decimal("2.4e-9")
    assert timing.duration_error_seconds == Decimal("-4e-10")


def test_continuous_timing_selects_sample_locations_inside_requested_interval() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("timing-continuous"),
            Sequence(
                (
                    Delay(PulseEventId("initial"), DRIVE_Q0, Quantity(1.2, "ns")),
                    Play(
                        PulseEventId("play"),
                        DRIVE_Q0,
                        Constant(Quantity(4, "ns"), Quantity(1, "arb")),
                    ),
                )
            ),
        )
    )

    timings = realize_event_timings(
        program.events,
        grid=SampleGrid(
            1_000_000_000,
            TimingQuantizationPolicy(mode="continuous"),
        ),
    )

    initial, play = timings
    assert (initial.start_sample, initial.sample_count) == (0, 1)
    assert (play.start_sample, play.sample_count) == (1, 4)
    assert play.requested_start_seconds == Decimal("1.2e-9")
    assert play.start_error_seconds == Decimal("-2e-10")


def test_continuous_timing_allows_a_delay_with_no_sample_location() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("subsample-delay"),
            Sequence(
                (
                    Delay(PulseEventId("delay"), DRIVE_Q0, Quantity(0.2, "ns")),
                    Play(
                        PulseEventId("play"),
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(1, "arb")),
                    ),
                )
            ),
        )
    )

    delay, play = realize_event_timings(
        program.events,
        grid=SampleGrid(
            1_000_000_000,
            TimingQuantizationPolicy(mode="continuous"),
        ),
    )

    assert delay.sample_count == 0
    assert play.requested_start_seconds == Decimal("2e-10")
    assert play.sample_count == 2


def test_timing_only_planner_preserves_structured_strict_grid_errors() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("timing-strict"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(2.4, "ns"), Quantity(1, "arb")),
            ),
        )
    )

    with pytest.raises(WaveformPlanningError) as caught:
        realize_event_timings(
            program.events,
            grid=SampleGrid(
                1_000_000_000,
                TimingQuantizationPolicy(mode="strict"),
            ),
        )

    assert {issue.code for issue in caught.value.issues} == {
        "sampled_event_end_off_grid"
    }
    assert caught.value.issues[0].event_id == PulseEventId("play")


def test_timing_only_planner_rejects_nearest_grid_collapse() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("timing-collapsed"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(0.4, "ns"), Quantity(1, "arb")),
            ),
        )
    )

    with pytest.raises(WaveformPlanningError) as caught:
        realize_event_timings(
            program.events,
            grid=SampleGrid(1_000_000_000),
        )

    assert {issue.code for issue in caught.value.issues} == {"sampled_event_collapsed"}


def test_timing_only_planner_accepts_empty_and_zero_duration_events() -> None:
    grid = SampleGrid(1_000_000_000)
    assert realize_event_timings((), grid=grid) == ()
    program = schedule(
        PulseProgram(
            PulseProgramId("timing-zero-duration"),
            ShiftPhase(
                PulseEventId("shift"),
                DRIVE_Q0,
                Quantity(0.25, "rad"),
            ),
        )
    )

    [timing] = realize_event_timings(program.events, grid=grid)

    assert timing.start_sample == 0
    assert timing.sample_count == 0
    assert timing.duration_error_seconds == 0


def test_strict_rejects_off_grid_boundaries() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("strict"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(2.4, "ns"), Quantity(1, "arb")),
            ),
        )
    )

    with pytest.raises(WaveformPlanningError) as caught:
        plan_sampled_waveforms(
            program,
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(
                1_000_000_000,
                TimingQuantizationPolicy(mode="strict"),
            ),
        )

    assert {issue.code for issue in caught.value.issues} == {
        "sampled_event_end_off_grid",
        "sampled_program_duration_off_grid",
    }


def test_nearest_rejects_a_positive_event_that_collapses() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("collapsed"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(0.4, "ns"), Quantity(1, "arb")),
            ),
        )
    )

    with pytest.raises(WaveformPlanningError) as caught:
        plan_sampled_waveforms(
            program,
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(1_000_000_000),
        )

    assert {issue.code for issue in caught.value.issues} == {
        "sampled_event_collapsed",
        "sampled_program_collapsed",
    }


def test_shift_phase_precedes_same_time_playback() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("phase"),
            Sequence(
                (
                    ShiftPhase(
                        PulseEventId("shift"),
                        DRIVE_Q0,
                        Quantity(math.pi / 2, "rad"),
                    ),
                    Play(
                        PulseEventId("play"),
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(0.25, "arb")),
                    ),
                )
            ),
        )
    )
    plan = plan_sampled_waveforms(
        program,
        bindings=(_binding(DRIVE_Q0),),
        grid=SampleGrid(1_000_000_000),
    )

    rendered = Float64ReferenceRenderer().render(plan)

    np.testing.assert_allclose(rendered.buffers[0], (0.0, 0.0), atol=1e-15)
    np.testing.assert_allclose(rendered.buffers[1], (0.25, 0.25))
    assert all(buffer.dtype == np.float64 for buffer in rendered.buffers)
    assert all(buffer.flags.c_contiguous for buffer in rendered.buffers)
    assert all(not buffer.flags.writeable for buffer in rendered.buffers)


def test_resolved_waveform_events_retain_continuous_intent_without_a_grid() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("continuous-waveform-intent"),
            Sequence(
                (
                    Delay(PulseEventId("initial"), DRIVE_Q0, Quantity(1.2, "ns")),
                    ShiftPhase(
                        PulseEventId("shift"),
                        DRIVE_Q0,
                        Quantity(0.5, "rad"),
                    ),
                    Play(
                        PulseEventId("play"),
                        DRIVE_Q0,
                        Constant(
                            Quantity(2.4, "ns"),
                            Quantity(0.25, "arb"),
                            phase=Quantity(0.125, "rad"),
                        ),
                    ),
                )
            ),
        )
    )

    [event] = resolve_waveform_events(program.events)

    assert event.event_id == PulseEventId("play")
    assert event.signal == DRIVE_Q0
    assert event.start_seconds == Decimal("1.2e-9")
    assert event.duration_seconds == Decimal("2.4e-9")
    assert event.frame_phase_radians == pytest.approx(0.5)
    assert event.effective_phase_radians == pytest.approx(0.625)
    rephased = replace(
        event,
        envelope=replace(event.envelope, phase=Quantity(0.25, "rad")),
    )
    assert rephased.effective_phase_radians == pytest.approx(0.75)


@pytest.mark.parametrize(
    "carrier_phase_reference",
    ("schedule_origin", "signal_first_play", "event_origin"),
)
def test_window_render_matches_the_same_full_program_slice(
    carrier_phase_reference: CarrierPhaseReference,
) -> None:
    second_id = PulseEventId("second")
    program = schedule(
        PulseProgram(
            PulseProgramId(f"window-{carrier_phase_reference}"),
            Sequence(
                (
                    Delay(PulseEventId("initial"), DRIVE_Q0, Quantity(1.2, "ns")),
                    Play(
                        PulseEventId("first"),
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(0.4, "arb")),
                    ),
                    ShiftPhase(
                        PulseEventId("shift"),
                        DRIVE_Q0,
                        Quantity(0.25, "rad"),
                    ),
                    Delay(PulseEventId("gap"), DRIVE_Q0, Quantity(1.1, "ns")),
                    Play(
                        second_id,
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(0.6, "arb")),
                    ),
                )
            ),
        )
    )
    binding = SampledOutputBinding(
        signal=DRIVE_Q0,
        i_lane=0,
        q_lane=1,
        intermediate_frequency_hz=175_000_000,
        mixer=IDENTITY_IQ,
        carrier_phase_reference=carrier_phase_reference,
    )
    grid = SampleGrid(1_000_000_000)
    resolved = resolve_waveform_events(program.events)
    selected = tuple(event for event in resolved if event.event_id == second_id)

    full_plan = plan_sampled_waveforms(program, bindings=(binding,), grid=grid)
    window_plan = plan_sampled_waveform_window(
        resolved,
        program_id=program.id,
        selected_events=selected,
        bindings=(binding,),
        grid=grid,
    )
    full = Float64ReferenceRenderer().render(full_plan)
    window = Float64ReferenceRenderer().render(window_plan)
    full_timing = full_plan.timing_for(second_id)

    assert window_plan.time_origin_seconds == Decimal("4e-9")
    assert window.time_origin_seconds == window_plan.time_origin_seconds
    assert window_plan.timing_for(second_id).requested_start_seconds == Decimal(
        "0.3e-9"
    )
    assert window_plan.render_events[0].effective_phase_radians == pytest.approx(0.25)
    np.testing.assert_allclose(
        window.buffers[0],
        full.buffers[0][full_timing.start_sample : full_timing.end_sample],
        atol=1e-15,
    )
    np.testing.assert_allclose(
        window.buffers[1],
        full.buffers[1][full_timing.start_sample : full_timing.end_sample],
        atol=1e-15,
    )


def test_window_quantizes_only_its_selected_timing_domain() -> None:
    second_id = PulseEventId("second")
    program = schedule(
        PulseProgram(
            PulseProgramId("window-timing-domain"),
            Sequence(
                (
                    Delay(PulseEventId("initial"), DRIVE_Q0, Quantity(1.2, "ns")),
                    Play(
                        PulseEventId("off-grid"),
                        DRIVE_Q0,
                        Constant(Quantity(0.8, "ns"), Quantity(0.4, "arb")),
                    ),
                    Delay(PulseEventId("gap"), DRIVE_Q0, Quantity(1, "ns")),
                    Play(
                        second_id,
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(0.6, "arb")),
                    ),
                )
            ),
        )
    )
    grid = SampleGrid(
        1_000_000_000,
        TimingQuantizationPolicy(mode="strict"),
    )
    resolved = resolve_waveform_events(program.events)
    selected = tuple(event for event in resolved if event.event_id == second_id)

    plan = plan_sampled_waveform_window(
        resolved,
        program_id=program.id,
        selected_events=selected,
        bindings=(_binding(DRIVE_Q0),),
        grid=grid,
    )

    assert plan.time_origin_seconds == Decimal("3e-9")
    assert plan.sample_count == 2
    assert plan.timing_for(second_id).start_sample == 0
    with pytest.raises(WaveformPlanningError):
        plan_sampled_waveforms(program, bindings=(_binding(DRIVE_Q0),), grid=grid)


def test_window_selects_one_realtime_occurrence_with_a_repeated_authored_id() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("repeated-occurrence"),
            Play(
                PulseEventId("repeat-body-play"),
                DRIVE_Q0,
                Constant(Quantity(2, "ns"), Quantity(0.5, "arb")),
            ),
        )
    )
    [authored] = resolve_waveform_events(program.events)
    first = replace(authored, start_seconds=Decimal("2e-9"))
    second = replace(authored, start_seconds=Decimal("10e-9"))

    plan = plan_sampled_waveform_window(
        (first, second),
        program_id=program.id,
        selected_events=(second,),
        bindings=(
            SampledOutputBinding(
                signal=DRIVE_Q0,
                i_lane=0,
                q_lane=1,
                intermediate_frequency_hz=0.0,
                mixer=IDENTITY_IQ,
                carrier_phase_reference="signal_first_play",
            ),
        ),
        grid=SampleGrid(1_000_000_000),
    )

    assert plan.time_origin_seconds == Decimal("10e-9")
    assert plan.sample_count == 2
    assert plan.render_events[0].carrier_origin_seconds == Decimal("-8e-9")

    repeated_plan = plan_sampled_waveform_window(
        (first, second),
        program_id=program.id,
        selected_events=(first, second),
        bindings=(plan.render_events[0].binding,),
        grid=SampleGrid(1_000_000_000),
    )

    assert tuple(
        timing.start_sample
        for timing in repeated_plan.timings_for(PulseEventId("repeat-body-play"))
    ) == (0, 8)
    with pytest.raises(ValueError, match="repeated occurrences"):
        repeated_plan.timing_for(PulseEventId("repeat-body-play"))


def test_window_selection_requires_distinct_instances_from_its_context() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("window-event-instance"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(2, "ns"), Quantity(0.5, "arb")),
            ),
        )
    )
    [event] = resolve_waveform_events(program.events)

    with pytest.raises(ValueError, match="instances from the complete context"):
        plan_sampled_waveform_window(
            (event,),
            program_id=program.id,
            selected_events=(replace(event),),
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(1_000_000_000),
        )

    with pytest.raises(ValueError, match="select each event instance only once"):
        plan_sampled_waveform_window(
            (event,),
            program_id=program.id,
            selected_events=(event, event),
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(1_000_000_000),
        )


def test_gaussian_uses_midpoints_over_the_realized_span() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("gaussian"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Gaussian(
                    duration=Quantity(4, "ns"),
                    amplitude=Quantity(0.2, "arb"),
                    sigma=Quantity(1, "ns"),
                ),
            ),
        )
    )
    rendered = Float64ReferenceRenderer().render(
        plan_sampled_waveforms(
            program,
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(1_000_000_000),
        )
    )
    expected = tuple(
        0.2 * math.exp(-(offset * offset) / 2.0) for offset in (-1.5, -0.5, 0.5, 1.5)
    )

    np.testing.assert_allclose(rendered.buffers[0], expected)
    np.testing.assert_allclose(rendered.buffers[1], (0.0,) * 4)


def test_continuous_sampling_makes_fractional_envelope_origins_observable() -> None:
    def render(initial_delay_ns: float) -> tuple[SampledWaveformPlan, np.ndarray]:
        program = schedule(
            PulseProgram(
                PulseProgramId(f"continuous-gaussian-{initial_delay_ns}"),
                Sequence(
                    (
                        Delay(
                            PulseEventId("initial"),
                            DRIVE_Q0,
                            Quantity(initial_delay_ns, "ns"),
                        ),
                        Play(
                            PulseEventId("play"),
                            DRIVE_Q0,
                            Gaussian(
                                duration=Quantity(4, "ns"),
                                amplitude=Quantity(0.2, "arb"),
                                sigma=Quantity(1, "ns"),
                            ),
                        ),
                    )
                ),
            )
        )
        plan = plan_sampled_waveforms(
            program,
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(
                1_000_000_000,
                TimingQuantizationPolicy(mode="continuous"),
            ),
        )
        return plan, Float64ReferenceRenderer().render(plan).buffers[0]

    aligned_plan, aligned = render(1.0)
    shifted_plan, shifted = render(1.2)
    aligned_offsets = np.array((-1.5, -0.5, 0.5, 1.5))
    shifted_offsets = np.array((-1.7, -0.7, 0.3, 1.3))

    assert aligned_plan.semantics_id == (
        CONTINUOUS_MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID
    )
    assert shifted_plan.timing_for(PulseEventId("play")).start_sample == 1
    np.testing.assert_allclose(
        aligned[1:5],
        0.2 * np.exp(-(aligned_offsets * aligned_offsets) / 2.0),
    )
    np.testing.assert_allclose(
        shifted[1:5],
        0.2 * np.exp(-(shifted_offsets * shifted_offsets) / 2.0),
    )
    assert not np.array_equal(aligned, shifted)
    assert factor_phase_parameterized_waveforms((aligned_plan, shifted_plan)) is None


def test_nearest_phase_factorization_ignores_non_rendered_requested_origins() -> None:
    def plan(initial_delay_ns: float, phase: float) -> SampledWaveformPlan:
        return plan_sampled_waveforms(
            schedule(
                PulseProgram(
                    PulseProgramId(f"nearest-factor-{initial_delay_ns}-{phase}"),
                    Sequence(
                        (
                            Delay(
                                PulseEventId("initial"),
                                DRIVE_Q0,
                                Quantity(initial_delay_ns, "ns"),
                            ),
                            Play(
                                PulseEventId("play"),
                                DRIVE_Q0,
                                Gaussian(
                                    duration=Quantity(4, "ns"),
                                    amplitude=Quantity(0.2, "arb"),
                                    sigma=Quantity(1, "ns"),
                                    phase=Quantity(phase, "rad"),
                                ),
                            ),
                        )
                    ),
                )
            ),
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(
                1_000_000_000,
                TimingQuantizationPolicy(mode="nearest"),
            ),
        )

    factored = factor_phase_parameterized_waveforms(
        (plan(0.1, 0.0), plan(0.2, math.pi / 2))
    )

    assert factored is not None
    assert factored.phase_rows == ((0.0,), (math.pi / 2,))


def test_continuous_window_records_the_first_in_interval_left_edge_sample() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("continuous-left-edge-window"),
            Sequence(
                (
                    Delay(PulseEventId("initial"), DRIVE_Q0, Quantity(0.25, "ns")),
                    Play(
                        PulseEventId("play"),
                        DRIVE_Q0,
                        Gaussian(
                            duration=Quantity(2, "ns"),
                            amplitude=Quantity(0.2, "arb"),
                            sigma=Quantity(0.5, "ns"),
                        ),
                    ),
                )
            ),
        )
    )
    [play] = resolve_waveform_events(program.events)
    plan = plan_sampled_waveform_window(
        (play,),
        program_id=program.id,
        selected_events=(play,),
        bindings=(_binding(DRIVE_Q0),),
        grid=SampleGrid(
            2_000_000_000,
            TimingQuantizationPolicy(mode="continuous"),
            sample_location="left_edge",
        ),
    )

    [timing] = plan.event_timings
    assert plan.semantics_id == CONTINUOUS_LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID
    assert plan.time_origin_seconds == Decimal("5e-10")
    assert timing.requested_start_seconds == Decimal("-2.5e-10")
    assert timing.start_sample == 0
    assert timing.sample_count == 4


def test_left_edge_cosine_flat_top_matches_legacy_periodic_hann() -> None:
    sample_count = 50
    amplitude = 0.3512
    program = schedule(
        PulseProgram(
            PulseProgramId("legacy-cosine"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                CosineFlatTop(
                    duration=Quantity(25, "ns"),
                    amplitude=Quantity(amplitude, "arb"),
                    rise_duration=Quantity(12.5, "ns"),
                    fall_duration=Quantity(12.5, "ns"),
                ),
            ),
        )
    )
    plan = plan_sampled_waveforms(
        program,
        bindings=(_binding(DRIVE_Q0),),
        grid=SampleGrid(2_000_000_000, sample_location="left_edge"),
    )

    rendered = Float64ReferenceRenderer().render(plan)
    expected = (
        amplitude
        * 0.5
        * (1.0 - np.cos(math.tau * np.arange(sample_count) / sample_count))
    )

    assert plan.semantics_id == LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID
    assert rendered.semantics_id == LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID
    np.testing.assert_allclose(rendered.buffers[0], expected, atol=1e-15)
    np.testing.assert_allclose(rendered.buffers[1], np.zeros(sample_count))


def test_derivative_quadrature_composes_with_legacy_periodic_hann() -> None:
    sample_count = 50
    duration_seconds = 25e-9
    amplitude = 0.3512
    beta_seconds = -0.44e-9
    positions = np.arange(sample_count) / 2_000_000_000
    envelope = DerivativeQuadrature(
        envelope=CosineFlatTop(
            duration=Quantity(25, "ns"),
            amplitude=Quantity(amplitude, "arb"),
            rise_duration=Quantity(12.5, "ns"),
            fall_duration=Quantity(12.5, "ns"),
        ),
        beta=Quantity(-0.44, "ns"),
    )
    rendered = Float64ReferenceRenderer().render(
        plan_sampled_waveforms(
            schedule(
                PulseProgram(
                    PulseProgramId("legacy-cosine-derivative"),
                    Play(PulseEventId("play"), DRIVE_Q0, envelope),
                )
            ),
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(2_000_000_000, sample_location="left_edge"),
        )
    )

    expected_i = (
        amplitude * 0.5 * (1.0 - np.cos(math.tau * positions / duration_seconds))
    )
    expected_q = (
        beta_seconds
        * amplitude
        * math.pi
        / duration_seconds
        * np.sin(math.tau * positions / duration_seconds)
    )
    np.testing.assert_allclose(rendered.buffers[0], expected_i, atol=1e-15)
    np.testing.assert_allclose(rendered.buffers[1], expected_q, atol=1e-15)


def test_frequency_shift_uses_a_center_referenced_pulse_local_phase_ramp() -> None:
    envelope = FrequencyShift(
        envelope=Constant(
            duration=Quantity(4, "ns"),
            amplitude=Quantity(0.2, "arb"),
        ),
        frequency_offset=Quantity(250, "MHz"),
    )
    rendered = Float64ReferenceRenderer().render(
        plan_sampled_waveforms(
            schedule(
                PulseProgram(
                    PulseProgramId("local-frequency-shift"),
                    Play(PulseEventId("play"), DRIVE_Q0, envelope),
                )
            ),
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(1_000_000_000),
        )
    )
    phases = math.tau * 250e6 * (np.arange(4) + 0.5 - 2.0) * 1e-9

    np.testing.assert_allclose(rendered.buffers[0], 0.2 * np.cos(phases), atol=1e-15)
    np.testing.assert_allclose(rendered.buffers[1], 0.2 * np.sin(phases), atol=1e-15)


def test_frequency_shift_uses_start_reference_and_resets_after_an_idle() -> None:
    shifted = FrequencyShift(
        envelope=Constant(
            duration=Quantity(2, "ns"),
            amplitude=Quantity(0.2, "arb"),
        ),
        frequency_offset=Quantity(250, "MHz"),
        phase_reference="start",
    )
    rendered = Float64ReferenceRenderer().render(
        plan_sampled_waveforms(
            schedule(
                PulseProgram(
                    PulseProgramId("start-referenced-local-frequency-shift"),
                    Sequence(
                        (
                            Play(PulseEventId("first"), DRIVE_Q0, shifted),
                            Delay(PulseEventId("idle"), DRIVE_Q0, Quantity(3, "ns")),
                            Play(PulseEventId("second"), DRIVE_Q0, shifted),
                        )
                    ),
                )
            ),
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(1_000_000_000),
        )
    )
    phases = math.tau * 250e6 * (np.arange(2) + 0.5) * 1e-9
    pulse = 0.2 * np.exp(1j * phases)
    expected = np.concatenate((pulse, np.zeros(3), pulse))

    np.testing.assert_allclose(
        rendered.buffers[0] + 1j * rendered.buffers[1],
        expected,
        atol=1e-15,
    )


def test_frequency_shift_preserves_fractional_continuous_local_time() -> None:
    rendered = Float64ReferenceRenderer().render(
        plan_sampled_waveforms(
            schedule(
                PulseProgram(
                    PulseProgramId("fractional-local-frequency-shift"),
                    Sequence(
                        (
                            Delay(
                                PulseEventId("fractional-idle"),
                                DRIVE_Q0,
                                Quantity(0.2, "ns"),
                            ),
                            Play(
                                PulseEventId("shifted"),
                                DRIVE_Q0,
                                FrequencyShift(
                                    envelope=Constant(
                                        duration=Quantity(2.4, "ns"),
                                        amplitude=Quantity(0.2, "arb"),
                                    ),
                                    frequency_offset=Quantity(250, "MHz"),
                                ),
                            ),
                        )
                    ),
                )
            ),
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(
                1_000_000_000,
                TimingQuantizationPolicy(mode="continuous"),
            ),
        )
    )
    local_positions_ns = np.asarray((0.3, 1.3, 2.3))
    phases = math.tau * 250e6 * (local_positions_ns - 1.2) * 1e-9

    np.testing.assert_allclose(
        rendered.buffers[0] + 1j * rendered.buffers[1],
        0.2 * np.exp(1j * phases),
        atol=1e-15,
    )


def test_frequency_shift_modulates_the_complete_derivative_envelope() -> None:
    base = DerivativeQuadrature(
        envelope=Gaussian(
            duration=Quantity(4, "ns"),
            amplitude=Quantity(0.2, "arb"),
            sigma=Quantity(1, "ns"),
        ),
        beta=Quantity(-0.4, "ns"),
    )

    def render(envelope: DerivativeQuadrature | FrequencyShift):
        return Float64ReferenceRenderer().render(
            plan_sampled_waveforms(
                schedule(
                    PulseProgram(
                        PulseProgramId("composed-frequency-shift"),
                        Play(PulseEventId("play"), DRIVE_Q0, envelope),
                    )
                ),
                bindings=(_binding(DRIVE_Q0),),
                grid=SampleGrid(1_000_000_000),
            )
        )

    baseline = render(base)
    shifted = render(
        FrequencyShift(
            envelope=base,
            frequency_offset=Quantity(125, "MHz"),
        )
    )
    baseline_complex = baseline.buffers[0] + 1j * baseline.buffers[1]
    shifted_complex = shifted.buffers[0] + 1j * shifted.buffers[1]
    phases = math.tau * 125e6 * (np.arange(4) + 0.5 - 2.0) * 1e-9

    np.testing.assert_allclose(
        shifted_complex,
        baseline_complex * np.exp(1j * phases),
        atol=1e-15,
    )


def test_left_edge_cosine_flat_top_preserves_legacy_readout_edges() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("legacy-readout"),
            Play(
                PulseEventId("play"),
                READOUT_Q0,
                CosineFlatTop(
                    duration=Quantity(200, "ns"),
                    amplitude=Quantity(1, "arb"),
                    rise_duration=Quantity(1, "ns"),
                    fall_duration=Quantity(1, "ns"),
                ),
            ),
        )
    )
    rendered = Float64ReferenceRenderer().render(
        plan_sampled_waveforms(
            program,
            bindings=(_binding(READOUT_Q0),),
            grid=SampleGrid(2_000_000_000, sample_location="left_edge"),
        )
    )

    assert rendered.buffers[0].size == 400
    np.testing.assert_allclose(rendered.buffers[0][:4], (0.0, 0.5, 1.0, 1.0))
    np.testing.assert_allclose(rendered.buffers[0][-4:], (1.0, 1.0, 1.0, 0.5))


def test_carrier_phase_reference_selects_schedule_signal_or_event_origin() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("carrier-origins"),
            Sequence(
                (
                    Delay(PulseEventId("initial"), DRIVE_Q0, Quantity(1, "ns")),
                    Play(
                        PulseEventId("first"),
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(1, "arb")),
                    ),
                    Delay(PulseEventId("gap"), DRIVE_Q0, Quantity(1, "ns")),
                    Play(
                        PulseEventId("second"),
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(1, "arb")),
                    ),
                )
            ),
        )
    )

    def render(
        reference: CarrierPhaseReference,
    ) -> tuple[np.ndarray, np.ndarray]:
        binding = SampledOutputBinding(
            signal=DRIVE_Q0,
            i_lane=0,
            q_lane=1,
            intermediate_frequency_hz=250_000_000,
            mixer=IDENTITY_IQ,
            carrier_phase_reference=reference,
        )
        rendered = Float64ReferenceRenderer().render(
            plan_sampled_waveforms(
                program,
                bindings=(binding,),
                grid=SampleGrid(1_000_000_000, sample_location="left_edge"),
            )
        )
        return rendered.buffers[0], rendered.buffers[1]

    schedule_i, schedule_q = render("schedule_origin")
    signal_i, signal_q = render("signal_first_play")
    event_i, event_q = render("event_origin")

    np.testing.assert_allclose(
        schedule_i[[1, 2, 4, 5]], (0.0, -1.0, 1.0, 0.0), atol=1e-15
    )
    np.testing.assert_allclose(
        schedule_q[[1, 2, 4, 5]], (1.0, 0.0, 0.0, 1.0), atol=1e-15
    )
    np.testing.assert_allclose(signal_i[[1, 2, 4, 5]], (1.0, 0.0, 0.0, 1.0), atol=1e-15)
    np.testing.assert_allclose(
        signal_q[[1, 2, 4, 5]], (0.0, 1.0, -1.0, 0.0), atol=1e-15
    )
    np.testing.assert_allclose(event_i[[1, 2, 4, 5]], (1.0, 0.0, 1.0, 0.0), atol=1e-15)
    np.testing.assert_allclose(event_q[[1, 2, 4, 5]], (0.0, 1.0, 0.0, 1.0), atol=1e-15)


def test_default_sample_location_retains_midpoint_semantics() -> None:
    plan = _phase_plan(0.0)

    assert plan.grid.sample_location == "midpoint"
    assert plan.semantics_id == MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID


def test_event_origin_is_the_continuous_boundary_not_the_first_sample() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("midpoint-event-origin"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(2, "ns"), Quantity(1, "arb")),
            ),
        )
    )
    binding = SampledOutputBinding(
        signal=DRIVE_Q0,
        i_lane=0,
        q_lane=1,
        intermediate_frequency_hz=250_000_000,
        mixer=IDENTITY_IQ,
        carrier_phase_reference="event_origin",
    )

    rendered = Float64ReferenceRenderer().render(
        plan_sampled_waveforms(
            program,
            bindings=(binding,),
            grid=SampleGrid(1_000_000_000),
        )
    )

    half_sqrt_two = math.sqrt(0.5)
    np.testing.assert_allclose(
        rendered.buffers[0],
        (half_sqrt_two, -half_sqrt_two),
    )
    np.testing.assert_allclose(
        rendered.buffers[1],
        (half_sqrt_two, half_sqrt_two),
    )


def test_event_origin_preserves_a_subsample_continuous_start() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("subsample-event-origin"),
            Sequence(
                (
                    Delay(PulseEventId("initial"), DRIVE_Q0, Quantity(1.2, "ns")),
                    Play(
                        PulseEventId("play"),
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(1, "arb")),
                    ),
                )
            ),
        )
    )
    binding = SampledOutputBinding(
        signal=DRIVE_Q0,
        i_lane=0,
        q_lane=1,
        intermediate_frequency_hz=250_000_000,
        mixer=IDENTITY_IQ,
        carrier_phase_reference="event_origin",
    )

    plan = plan_sampled_waveforms(
        program,
        bindings=(binding,),
        grid=SampleGrid(1_000_000_000),
    )
    rendered = Float64ReferenceRenderer().render(plan)
    [event] = plan.render_events
    relative_positions = np.array((0.3e-9, 1.3e-9))
    phases = math.tau * binding.intermediate_frequency_hz * relative_positions

    assert event.timing.start_sample == 1
    assert event.carrier_origin_seconds == Decimal("1.2e-9")
    np.testing.assert_allclose(rendered.buffers[0][1:3], np.cos(phases))
    np.testing.assert_allclose(rendered.buffers[1][1:3], np.sin(phases))


def test_nearest_rejects_cosine_edges_longer_than_realized_duration() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("collapsed-cosine-plateau"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                CosineFlatTop(
                    duration=Quantity(2.4, "ns"),
                    amplitude=Quantity(1, "arb"),
                    rise_duration=Quantity(1.2, "ns"),
                    fall_duration=Quantity(1.2, "ns"),
                ),
            ),
        )
    )

    with pytest.raises(WaveformPlanningError) as caught:
        plan_sampled_waveforms(
            program,
            bindings=(_binding(DRIVE_Q0),),
            grid=SampleGrid(1_000_000_000),
        )

    assert {issue.code for issue in caught.value.issues} == {
        "sampled_cosine_edges_exceed_realized_duration"
    }


def test_final_lane_peak_is_measured_after_additive_accumulation() -> None:
    program = schedule(
        PulseProgram(
            PulseProgramId("multiplexed"),
            Parallel(
                (
                    Play(
                        PulseEventId("q0"),
                        READOUT_Q0,
                        Constant(Quantity(2, "ns"), Quantity(0.6, "arb")),
                    ),
                    Play(
                        PulseEventId("q1"),
                        READOUT_Q1,
                        Constant(Quantity(2, "ns"), Quantity(0.6, "arb")),
                    ),
                )
            ),
        )
    )
    plan = plan_sampled_waveforms(
        program,
        bindings=(_binding(READOUT_Q0), _binding(READOUT_Q1)),
        grid=SampleGrid(1_000_000_000),
    )

    rendered = Float64ReferenceRenderer().render(plan)

    np.testing.assert_allclose(rendered.buffers[0], (1.2, 1.2))
    assert rendered.lane_peaks == pytest.approx((1.2, 0.0))
