"""Continuous waveform resolution, sampled planning, and reference rendering.

The pulse scheduler retains exact continuous time.  This module first resolves
logical phase frames without choosing a target representation, then owns the
portable sampled-output semantics after a laboratory selects numeric output
lanes and before device-specific quantization or encoding.  One sampled plan
belongs to one grid; targets with heterogeneous clocks build one window per
physical timing domain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal, cast

import numpy as np
from numpy.typing import NDArray
from scopecat import Quantity

from scopecat_quantum._ids import PulseEventId, PulseProgramId
from scopecat_quantum.pulses import (
    AnalyticEnvelope,
    Constant,
    CosineFlatTop,
    DerivativeQuadrature,
    DriveSignal,
    FrameSignal,
    FrequencyShift,
    Gaussian,
    Play,
    PlaySignal,
    ReadoutSignal,
    ScheduledPulseEvent,
    ScheduledPulseProgram,
    ShiftPhase,
)

MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID = "scopecat.sampled.midpoint.v1"
LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID = "scopecat.sampled.left-edge.v1"
CONTINUOUS_MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID = (
    "scopecat.sampled.continuous-time.midpoint.v1"
)
CONTINUOUS_LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID = (
    "scopecat.sampled.continuous-time.left-edge.v1"
)
SAMPLED_WAVEFORM_SEMANTICS_ID = MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID

type TimingQuantizationMode = Literal["strict", "nearest", "continuous"]
type SampleLocation = Literal["midpoint", "left_edge"]
type CarrierPhaseReference = Literal[
    "schedule_origin",
    "signal_first_play",
    "event_origin",
]
type SampledWaveformSemanticsId = Literal[
    "scopecat.sampled.midpoint.v1",
    "scopecat.sampled.left-edge.v1",
    "scopecat.sampled.continuous-time.midpoint.v1",
    "scopecat.sampled.continuous-time.left-edge.v1",
]


@dataclass(frozen=True, slots=True)
class TimingQuantizationPolicy:
    """How exact continuous boundaries are realized on one sample grid.

    ``strict`` requires exact boundary samples. ``nearest`` moves an
    instruction and its local envelope to half-even rounded boundaries.
    ``continuous`` retains the requested analytic origin and selects the sample
    locations inside its half-open continuous interval.
    """

    mode: TimingQuantizationMode = "nearest"
    boundary_rounding: Literal["half_even"] = "half_even"


@dataclass(frozen=True, slots=True)
class SampleGrid:
    """One fixed sampled-output clock and its boundary policy."""

    sample_rate_hz: int
    timing: TimingQuantizationPolicy = field(default_factory=TimingQuantizationPolicy)
    sample_location: SampleLocation = "midpoint"

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample rate must be positive")


@dataclass(frozen=True, slots=True)
class IqMatrix:
    """Linear map from one ideal complex sample to two physical lanes."""

    ii: float
    iq: float
    qi: float
    qq: float


@dataclass(frozen=True, slots=True)
class SampledOutputBinding:
    """Numeric realization of one logical output on two renderer lanes."""

    signal: PlaySignal
    i_lane: int
    q_lane: int
    intermediate_frequency_hz: float
    mixer: IqMatrix
    carrier_phase_reference: CarrierPhaseReference = "schedule_origin"


@dataclass(frozen=True, slots=True)
class ResolvedWaveformEvent:
    """Continuous-time analytic output after logical frame resolution."""

    event_id: PulseEventId
    signal: PlaySignal
    envelope: AnalyticEnvelope
    start_seconds: Decimal
    duration_seconds: Decimal
    frame_phase_radians: float

    @property
    def effective_phase_radians(self) -> float:
        """Return the complete authored-envelope plus logical-frame phase."""

        return float(self.envelope.phase.value) + self.frame_phase_radians


@dataclass(frozen=True, slots=True)
class RealizedEventTiming:
    """Requested continuous timing and its deterministic sampled realization."""

    event_id: PulseEventId
    requested_start_seconds: Decimal
    requested_duration_seconds: Decimal
    sample_rate_hz: int
    start_sample: int
    sample_count: int

    @property
    def end_sample(self) -> int:
        return self.start_sample + self.sample_count

    @property
    def realized_start_seconds(self) -> Decimal:
        return _sample_seconds(self.start_sample, self.sample_rate_hz)

    @property
    def realized_duration_seconds(self) -> Decimal:
        return _sample_seconds(self.sample_count, self.sample_rate_hz)

    @property
    def start_error_seconds(self) -> Decimal:
        return self.realized_start_seconds - self.requested_start_seconds

    @property
    def duration_error_seconds(self) -> Decimal:
        return self.realized_duration_seconds - self.requested_duration_seconds


@dataclass(frozen=True, slots=True)
class SampledRenderEvent:
    """One fully timed and frame-resolved analytic output event."""

    event_id: PulseEventId
    envelope: AnalyticEnvelope
    timing: RealizedEventTiming
    binding: SampledOutputBinding
    effective_phase_radians: float
    carrier_origin_seconds: Decimal


@dataclass(frozen=True, slots=True)
class SampledWaveformPlan:
    """Pure numeric work for one scheduled program and one sample-grid window.

    Timing and carrier coordinates are local to ``time_origin_seconds``.  A
    full-program plan uses zero; a window plan records the absolute realized
    boundary represented by its sample zero.
    """

    program_id: PulseProgramId
    semantics_id: SampledWaveformSemanticsId
    grid: SampleGrid
    time_origin_seconds: Decimal
    sample_count: int
    lane_count: int
    event_timings: tuple[RealizedEventTiming, ...]
    render_events: tuple[SampledRenderEvent, ...]

    def timing_for(self, event_id: PulseEventId) -> RealizedEventTiming:
        timings = self.timings_for(event_id)
        if not timings:
            raise KeyError(event_id)
        if len(timings) != 1:
            raise ValueError(f"event {event_id.value!r} has repeated occurrences")
        return timings[0]

    def timings_for(
        self,
        event_id: PulseEventId,
    ) -> tuple[RealizedEventTiming, ...]:
        """Return every realized occurrence of one authored event id."""

        return tuple(
            timing for timing in self.event_timings if timing.event_id == event_id
        )


@dataclass(frozen=True, slots=True)
class PhaseParameterizedSampledWaveforms:
    """One sampled-waveform structure plus a phase row for each entry."""

    template: SampledWaveformPlan
    phase_rows: tuple[tuple[float, ...], ...]


def factor_phase_parameterized_waveforms(
    plans: tuple[SampledWaveformPlan, ...],
) -> PhaseParameterizedSampledWaveforms | None:
    """Factor a complete batch when phase is its only varying render input.

    Return ``None`` unless at least two plans share their grid, shape, timing,
    bindings, and envelope structure so callers can retain ordinary per-plan
    rendering.
    """

    if len(plans) < 2:
        return None
    reference = plans[0]
    if not reference.render_events:
        return None
    for candidate in plans[1:]:
        if (
            candidate.semantics_id != reference.semantics_id
            or candidate.grid != reference.grid
            or candidate.time_origin_seconds != reference.time_origin_seconds
            or candidate.sample_count != reference.sample_count
            or candidate.lane_count != reference.lane_count
            or not _same_render_structure(
                reference.render_events,
                candidate.render_events,
                requested_timing_affects_render=(
                    reference.grid.timing.mode == "continuous"
                ),
            )
        ):
            return None
    return PhaseParameterizedSampledWaveforms(
        template=replace(
            reference,
            render_events=tuple(
                replace(event, effective_phase_radians=0.0)
                for event in reference.render_events
            ),
        ),
        phase_rows=tuple(
            tuple(event.effective_phase_radians for event in plan.render_events)
            for plan in plans
        ),
    )


def _same_render_structure(
    reference: tuple[SampledRenderEvent, ...],
    candidate: tuple[SampledRenderEvent, ...],
    *,
    requested_timing_affects_render: bool,
) -> bool:
    return len(reference) == len(candidate) and all(
        reference_event.binding == candidate_event.binding
        and (
            not requested_timing_affects_render
            or (
                reference_event.timing.requested_start_seconds
                == candidate_event.timing.requested_start_seconds
                and reference_event.timing.requested_duration_seconds
                == candidate_event.timing.requested_duration_seconds
            )
        )
        and reference_event.timing.start_sample == candidate_event.timing.start_sample
        and reference_event.timing.sample_count == candidate_event.timing.sample_count
        and reference_event.carrier_origin_seconds
        == candidate_event.carrier_origin_seconds
        and _replace_envelope_phase(
            candidate_event.envelope,
            reference_event.envelope.phase,
        )
        == reference_event.envelope
        for reference_event, candidate_event in zip(
            reference,
            candidate,
            strict=True,
        )
    )


def _replace_envelope_phase(
    envelope: AnalyticEnvelope,
    phase: Quantity,
) -> AnalyticEnvelope:
    if isinstance(envelope, DerivativeQuadrature | FrequencyShift):
        return replace(
            envelope,
            envelope=_replace_envelope_phase(envelope.envelope, phase),
        )
    return replace(envelope, phase=phase)


@dataclass(frozen=True, slots=True, eq=False)
class RenderedWaveforms:
    """Read-only normalized physical-lane buffers and their final peaks."""

    semantics_id: SampledWaveformSemanticsId
    sample_rate_hz: int
    time_origin_seconds: Decimal
    buffers: tuple[NDArray[np.float64], ...]
    lane_peaks: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class WaveformPlanningIssue:
    """One deterministic failure while realizing a continuous schedule."""

    code: str
    message: str
    event_id: PulseEventId | None = None


class WaveformPlanningError(ValueError):
    """Aggregate of independently discoverable sampled-output failures."""

    __slots__ = ("issues",)

    def __init__(self, issues: tuple[WaveformPlanningIssue, ...]) -> None:
        if not issues:
            raise ValueError("waveform planning errors require at least one issue")
        self.issues = tuple(sorted(set(issues), key=_issue_sort_key))
        super().__init__("; ".join(issue.message for issue in self.issues))


def realize_event_timings(
    events: tuple[ScheduledPulseEvent, ...],
    *,
    grid: SampleGrid,
) -> tuple[RealizedEventTiming, ...]:
    """Project exact event boundaries onto one sampled-output clock.

    This timing-only entry point lets device targets reuse the same continuous
    schedule semantics even when their waveform encoding or renderer remains
    device-specific.  Every shared boundary is realized once. A non-output
    delay may contain no sample location; a positive play may not collapse.
    """

    boundaries = {
        boundary
        for event in events
        for boundary in (
            event.start_seconds,
            event.start_seconds + event.duration_seconds,
        )
    }
    boundary_samples = {
        boundary: _quantize_boundary(boundary, grid) for boundary in boundaries
    }
    issues: list[WaveformPlanningIssue] = []
    timings = _realize_event_timings(
        events,
        grid=grid,
        boundary_samples=boundary_samples,
        issues=issues,
    )
    if issues:
        raise WaveformPlanningError(tuple(issues))
    return timings


def resolve_waveform_events(
    events: tuple[ScheduledPulseEvent, ...],
) -> tuple[ResolvedWaveformEvent, ...]:
    """Resolve logical phase frames without choosing a sampled target.

    The returned events retain exact continuous boundaries and analytic
    envelopes.  Targets with native envelope or NCO support can consume this
    layer without first pretending that their implementation is a dense DAC
    renderer.
    """

    resolved: list[ResolvedWaveformEvent] = []
    frame_phases: dict[FrameSignal, float] = {}
    for event in events:
        instruction = event.instruction
        if isinstance(instruction, ShiftPhase):
            frame_phases[instruction.signal] = frame_phases.get(
                instruction.signal, 0.0
            ) + float(instruction.phase.value)
            continue
        if not isinstance(instruction, Play):
            continue
        frame_phase = (
            frame_phases.get(instruction.signal, 0.0)
            if isinstance(instruction.signal, DriveSignal | ReadoutSignal)
            else 0.0
        )
        resolved.append(
            ResolvedWaveformEvent(
                event_id=event.id,
                signal=instruction.signal,
                envelope=instruction.envelope,
                start_seconds=event.start_seconds,
                duration_seconds=event.duration_seconds,
                frame_phase_radians=frame_phase,
            )
        )
    return tuple(resolved)


def plan_sampled_waveforms(
    program: ScheduledPulseProgram,
    *,
    bindings: tuple[SampledOutputBinding, ...],
    grid: SampleGrid,
    output_signals: frozenset[PlaySignal] | None = None,
) -> SampledWaveformPlan:
    """Resolve exact boundaries, output bindings, and phase frames.

    Every unique absolute boundary is quantized once.  Adjacent events therefore
    share the same integer boundary and cannot acquire a rounding gap or overlap.
    An explicit output selection projects rendering only: all original event
    boundaries, event timings, record origin and duration remain authoritative.
    """

    resolved_events = tuple(
        event
        for event in resolve_waveform_events(program.events)
        if output_signals is None or event.signal in output_signals
    )
    issues: list[WaveformPlanningIssue] = []
    binding_by_signal = _resolve_bindings(bindings, resolved_events, issues=issues)

    boundaries = {Decimal(0), program.duration_seconds}
    for event in program.events:
        boundaries.add(event.start_seconds)
        boundaries.add(event.start_seconds + event.duration_seconds)
    boundary_samples = {
        boundary: _quantize_boundary(boundary, grid) for boundary in boundaries
    }

    program_end_sample = boundary_samples[program.duration_seconds]
    if program_end_sample is None:
        issues.append(
            WaveformPlanningIssue(
                code="sampled_program_duration_off_grid",
                message=(
                    f"program {program.id.value!r} duration is not on the strict "
                    "sample grid"
                ),
            )
        )
    elif program.duration_seconds > 0 and program_end_sample <= 0:
        issues.append(
            WaveformPlanningIssue(
                code="sampled_program_collapsed",
                message=f"program {program.id.value!r} collapses to zero samples",
            )
        )

    timings = _realize_event_timings(
        program.events,
        grid=grid,
        boundary_samples=boundary_samples,
        issues=issues,
    )
    render_events = _plan_render_events(
        resolved_events,
        grid=grid,
        timings=timings,
        binding_by_signal=binding_by_signal,
        time_origin_seconds=Decimal(0),
        first_play_start_by_signal=_first_play_starts(resolved_events),
        issues=issues,
    )

    if issues:
        raise WaveformPlanningError(tuple(issues))
    assert program_end_sample is not None
    return SampledWaveformPlan(
        program_id=program.id,
        semantics_id=_sampled_waveform_semantics_id(grid),
        grid=grid,
        time_origin_seconds=Decimal(0),
        sample_count=program_end_sample,
        lane_count=_lane_count(render_events),
        event_timings=timings,
        render_events=render_events,
    )


def plan_sampled_waveform_window(
    context_events: tuple[ResolvedWaveformEvent, ...],
    *,
    program_id: PulseProgramId,
    selected_events: tuple[ResolvedWaveformEvent, ...],
    bindings: tuple[SampledOutputBinding, ...],
    grid: SampleGrid,
) -> SampledWaveformPlan:
    """Plan selected plays in one minimal sample-grid window.

    ``context_events`` supplies the complete continuous context for
    frame-resolved plays and signal-first carrier references.  Each selected
    event must be an instance from that context, which keeps repeated authored
    ids unambiguous.  Only the selected plays are quantized and rendered, so an
    unrelated timing domain cannot impose its grid on this window.
    """

    if not selected_events:
        raise ValueError("a sampled waveform window requires at least one event")
    context_identities = {id(event) for event in context_events}
    if len(context_identities) != len(context_events):
        raise ValueError(
            "the complete waveform context must contain distinct event instances"
        )
    if any(id(selected) not in context_identities for selected in selected_events):
        raise ValueError(
            "selected waveform events must be instances from the complete context"
        )
    if len({id(event) for event in selected_events}) != len(selected_events):
        raise ValueError("a waveform window may select each event instance only once")
    issues: list[WaveformPlanningIssue] = []
    binding_by_signal = _resolve_bindings(bindings, selected_events, issues=issues)
    boundaries = {
        boundary
        for event in selected_events
        for boundary in (
            event.start_seconds,
            event.start_seconds + event.duration_seconds,
        )
    }
    boundary_samples = {
        boundary: _quantize_boundary(boundary, grid) for boundary in boundaries
    }
    absolute_timings = _realize_resolved_event_timings(
        selected_events,
        grid=grid,
        boundary_samples=boundary_samples,
        issues=issues,
    )
    if not absolute_timings:
        raise WaveformPlanningError(tuple(issues))

    start_sample = min(timing.start_sample for timing in absolute_timings)
    end_sample = max(timing.end_sample for timing in absolute_timings)
    time_origin_seconds = _sample_seconds(start_sample, grid.sample_rate_hz)
    timings = tuple(
        replace(
            timing,
            requested_start_seconds=(
                timing.requested_start_seconds - time_origin_seconds
            ),
            start_sample=timing.start_sample - start_sample,
        )
        for timing in absolute_timings
    )
    render_events = _plan_render_events(
        selected_events,
        grid=grid,
        timings=timings,
        binding_by_signal=binding_by_signal,
        time_origin_seconds=time_origin_seconds,
        first_play_start_by_signal=_first_play_starts(context_events),
        issues=issues,
    )
    if issues:
        raise WaveformPlanningError(tuple(issues))
    return SampledWaveformPlan(
        program_id=program_id,
        semantics_id=_sampled_waveform_semantics_id(grid),
        grid=grid,
        time_origin_seconds=time_origin_seconds,
        sample_count=end_sample - start_sample,
        lane_count=_lane_count(render_events),
        event_timings=timings,
        render_events=render_events,
    )


def _resolve_bindings(
    bindings: tuple[SampledOutputBinding, ...],
    events: tuple[ResolvedWaveformEvent, ...],
    *,
    issues: list[WaveformPlanningIssue],
) -> dict[PlaySignal, SampledOutputBinding]:
    binding_by_signal: dict[PlaySignal, SampledOutputBinding] = {}
    for binding in bindings:
        if binding.signal in binding_by_signal:
            issues.append(
                WaveformPlanningIssue(
                    code="sampled_output_binding_duplicate",
                    message=(
                        f"logical output {binding.signal!r} is bound more than once"
                    ),
                )
            )
        else:
            binding_by_signal[binding.signal] = binding
    for event in events:
        if event.signal not in binding_by_signal:
            issues.append(
                WaveformPlanningIssue(
                    code="sampled_output_signal_unbound",
                    message=(
                        f"event {event.event_id.value!r} output {event.signal!r} "
                        "has no sampled-output binding"
                    ),
                    event_id=event.event_id,
                )
            )
    return binding_by_signal


def _first_play_starts(
    events: tuple[ResolvedWaveformEvent, ...],
) -> dict[PlaySignal, Decimal]:
    starts: dict[PlaySignal, Decimal] = {}
    for event in events:
        current = starts.get(event.signal)
        if current is None or event.start_seconds < current:
            starts[event.signal] = event.start_seconds
    return starts


def _plan_render_events(
    events: tuple[ResolvedWaveformEvent, ...],
    *,
    grid: SampleGrid,
    timings: tuple[RealizedEventTiming, ...],
    binding_by_signal: dict[PlaySignal, SampledOutputBinding],
    time_origin_seconds: Decimal,
    first_play_start_by_signal: dict[PlaySignal, Decimal],
    issues: list[WaveformPlanningIssue],
) -> tuple[SampledRenderEvent, ...]:
    timings_by_id: dict[PulseEventId, list[RealizedEventTiming]] = {}
    for timing in timings:
        timings_by_id.setdefault(timing.event_id, []).append(timing)
    timing_occurrences: dict[PulseEventId, int] = {}
    render_events: list[SampledRenderEvent] = []
    for event in events:
        binding = binding_by_signal.get(event.signal)
        occurrence = timing_occurrences.get(event.event_id, 0)
        candidates = timings_by_id.get(event.event_id, [])
        timing = candidates[occurrence] if occurrence < len(candidates) else None
        timing_occurrences[event.event_id] = occurrence + 1
        if binding is None or timing is None or timing.sample_count <= 0:
            continue
        base_envelope = event.envelope
        while isinstance(base_envelope, DerivativeQuadrature | FrequencyShift):
            base_envelope = base_envelope.envelope
        if isinstance(base_envelope, CosineFlatTop):
            edge_duration_seconds = Decimal(
                str(base_envelope.rise_duration.to("s").value)
            ) + Decimal(str(base_envelope.fall_duration.to("s").value))
            if edge_duration_seconds > _envelope_duration_seconds(timing, grid):
                issues.append(
                    WaveformPlanningIssue(
                        code="sampled_cosine_edges_exceed_realized_duration",
                        message=(
                            f"event {event.event_id.value!r} cosine-flat-top edges "
                            "exceed its envelope duration"
                        ),
                        event_id=event.event_id,
                    )
                )
        carrier_phase_reference = binding.carrier_phase_reference
        if carrier_phase_reference == "schedule_origin":
            absolute_carrier_origin_seconds = Decimal(0)
        elif carrier_phase_reference == "signal_first_play":
            absolute_carrier_origin_seconds = first_play_start_by_signal[event.signal]
        else:
            absolute_carrier_origin_seconds = event.start_seconds
        render_events.append(
            SampledRenderEvent(
                event_id=event.event_id,
                envelope=event.envelope,
                timing=timing,
                binding=binding,
                effective_phase_radians=event.effective_phase_radians,
                carrier_origin_seconds=(
                    absolute_carrier_origin_seconds - time_origin_seconds
                ),
            )
        )
    return tuple(render_events)


def _lane_count(events: tuple[SampledRenderEvent, ...]) -> int:
    return (
        max(
            (
                lane
                for event in events
                for lane in (event.binding.i_lane, event.binding.q_lane)
            ),
            default=-1,
        )
        + 1
    )


def _realize_event_timings(
    events: tuple[ScheduledPulseEvent, ...],
    *,
    grid: SampleGrid,
    boundary_samples: dict[Decimal, int | None],
    issues: list[WaveformPlanningIssue],
) -> tuple[RealizedEventTiming, ...]:
    return _realize_timing_rows(
        tuple(
            (
                event.id,
                event.start_seconds,
                event.duration_seconds,
                isinstance(event.instruction, Play),
            )
            for event in events
        ),
        grid=grid,
        boundary_samples=boundary_samples,
        issues=issues,
    )


def _realize_resolved_event_timings(
    events: tuple[ResolvedWaveformEvent, ...],
    *,
    grid: SampleGrid,
    boundary_samples: dict[Decimal, int | None],
    issues: list[WaveformPlanningIssue],
) -> tuple[RealizedEventTiming, ...]:
    return _realize_timing_rows(
        tuple(
            (event.event_id, event.start_seconds, event.duration_seconds, True)
            for event in events
        ),
        grid=grid,
        boundary_samples=boundary_samples,
        issues=issues,
    )


def _realize_timing_rows(
    events: tuple[tuple[PulseEventId, Decimal, Decimal, bool], ...],
    *,
    grid: SampleGrid,
    boundary_samples: dict[Decimal, int | None],
    issues: list[WaveformPlanningIssue],
) -> tuple[RealizedEventTiming, ...]:
    timings: list[RealizedEventTiming] = []
    for event_id, start_seconds, duration_seconds, requires_samples in events:
        requested_end = start_seconds + duration_seconds
        start_sample = boundary_samples[start_seconds]
        end_sample = boundary_samples[requested_end]
        if start_sample is None:
            issues.append(
                WaveformPlanningIssue(
                    code="sampled_event_start_off_grid",
                    message=(
                        f"event {event_id.value!r} start is not on the strict "
                        "sample grid"
                    ),
                    event_id=event_id,
                )
            )
        if end_sample is None:
            issues.append(
                WaveformPlanningIssue(
                    code="sampled_event_end_off_grid",
                    message=(
                        f"event {event_id.value!r} end is not on the strict sample grid"
                    ),
                    event_id=event_id,
                )
            )
        if start_sample is None or end_sample is None:
            continue
        timing = RealizedEventTiming(
            event_id=event_id,
            requested_start_seconds=start_seconds,
            requested_duration_seconds=duration_seconds,
            sample_rate_hz=grid.sample_rate_hz,
            start_sample=start_sample,
            sample_count=end_sample - start_sample,
        )
        timings.append(timing)
        if requires_samples and duration_seconds > 0 and timing.sample_count <= 0:
            issues.append(
                WaveformPlanningIssue(
                    code="sampled_event_collapsed",
                    message=(f"event {event_id.value!r} collapses to zero samples"),
                    event_id=event_id,
                )
            )
    return tuple(timings)


@dataclass(frozen=True, slots=True)
class Float64ReferenceRenderer:
    """Readable authority for sampled-output numerical semantics."""

    def render(self, plan: SampledWaveformPlan) -> RenderedWaveforms:
        buffers = tuple(
            np.zeros(plan.sample_count, dtype=np.float64)
            for _ in range(plan.lane_count)
        )
        sample_rate_hz = plan.grid.sample_rate_hz
        sample_offset = 0.5 if plan.grid.sample_location == "midpoint" else 0.0
        for event in plan.render_events:
            timing = event.timing
            absolute_positions = (
                timing.start_sample
                + np.arange(timing.sample_count, dtype=np.float64)
                + sample_offset
            ) / sample_rate_hz
            if plan.grid.timing.mode == "continuous":
                local_positions = absolute_positions - float(
                    timing.requested_start_seconds
                )
                envelope_duration_seconds = float(timing.requested_duration_seconds)
            else:
                local_positions = (
                    np.arange(timing.sample_count, dtype=np.float64) + sample_offset
                ) / sample_rate_hz
                envelope_duration_seconds = timing.sample_count / sample_rate_hz
            envelope = _envelope_samples(
                event.envelope,
                local_positions=local_positions,
                envelope_duration_seconds=envelope_duration_seconds,
            )
            carrier_positions = absolute_positions - float(event.carrier_origin_seconds)
            carrier = np.exp(
                1j
                * (
                    event.effective_phase_radians
                    + math.tau
                    * event.binding.intermediate_frequency_hz
                    * carrier_positions
                )
            )
            samples = envelope * carrier
            mixer = event.binding.mixer
            physical_i = mixer.ii * samples.real + mixer.iq * samples.imag
            physical_q = mixer.qi * samples.real + mixer.qq * samples.imag
            selected = slice(timing.start_sample, timing.end_sample)
            buffers[event.binding.i_lane][selected] += physical_i
            buffers[event.binding.q_lane][selected] += physical_q

        lane_peaks = tuple(
            float(cast("np.float64", np.max(np.abs(buffer)))) if buffer.size else 0.0
            for buffer in buffers
        )
        for buffer in buffers:
            buffer.flags.writeable = False
        return RenderedWaveforms(
            semantics_id=plan.semantics_id,
            sample_rate_hz=sample_rate_hz,
            time_origin_seconds=plan.time_origin_seconds,
            buffers=buffers,
            lane_peaks=lane_peaks,
        )


def _envelope_samples(
    envelope: AnalyticEnvelope,
    *,
    local_positions: np.ndarray[tuple[int], np.dtype[np.float64]],
    envelope_duration_seconds: float,
) -> np.ndarray[tuple[int], np.dtype[np.complex128]]:
    if isinstance(envelope, FrequencyShift):
        base = _envelope_samples(
            envelope.envelope,
            local_positions=local_positions,
            envelope_duration_seconds=envelope_duration_seconds,
        )
        reference_seconds = (
            envelope_duration_seconds / 2.0
            if envelope.phase_reference == "center"
            else 0.0
        )
        phase_radians = (
            math.tau
            * float(envelope.frequency_offset.value)
            * (local_positions - reference_seconds)
        )
        return base * np.exp(1j * phase_radians)

    if isinstance(envelope, DerivativeQuadrature):
        base = _envelope_samples(
            envelope.envelope,
            local_positions=local_positions,
            envelope_duration_seconds=envelope_duration_seconds,
        )
        derivative = _envelope_derivative_samples(
            envelope.envelope,
            local_positions=local_positions,
            envelope_duration_seconds=envelope_duration_seconds,
        )
        return base + 1j * float(envelope.beta.value) * derivative

    amplitude = float(envelope.amplitude.value)
    if isinstance(envelope, Constant):
        return np.full(local_positions.shape, complex(amplitude), dtype=np.complex128)

    if isinstance(envelope, CosineFlatTop):
        samples = np.full(local_positions.shape, amplitude, dtype=np.float64)
        rise_seconds = float(envelope.rise_duration.value)
        if rise_seconds > 0:
            rising = local_positions < rise_seconds
            samples[rising] *= 0.5 * (
                1.0 - np.cos(math.pi * local_positions[rising] / rise_seconds)
            )
        fall_seconds = float(envelope.fall_duration.value)
        if fall_seconds > 0:
            fall_start = envelope_duration_seconds - fall_seconds
            falling = local_positions >= fall_start
            samples[falling] *= 0.5 * (
                1.0
                + np.cos(
                    math.pi * (local_positions[falling] - fall_start) / fall_seconds
                )
            )
        return samples.astype(np.complex128)

    assert isinstance(envelope, Gaussian)
    sigma_seconds = float(envelope.sigma.value)
    offsets = local_positions - envelope_duration_seconds / 2.0
    gaussian = amplitude * np.exp(
        -(offsets * offsets) / (2.0 * sigma_seconds * sigma_seconds)
    )
    return gaussian.astype(np.complex128)


def _envelope_derivative_samples(
    envelope: Gaussian | CosineFlatTop,
    *,
    local_positions: np.ndarray[tuple[int], np.dtype[np.float64]],
    envelope_duration_seconds: float,
) -> np.ndarray[tuple[int], np.dtype[np.complex128]]:
    amplitude = float(envelope.amplitude.value)
    if isinstance(envelope, Gaussian):
        sigma_seconds = float(envelope.sigma.value)
        offsets = local_positions - envelope_duration_seconds / 2.0
        gaussian = amplitude * np.exp(
            -(offsets * offsets) / (2.0 * sigma_seconds * sigma_seconds)
        )
        return (-offsets * gaussian / (sigma_seconds * sigma_seconds)).astype(
            np.complex128
        )

    derivative = np.zeros(local_positions.shape, dtype=np.float64)
    rise_seconds = float(envelope.rise_duration.value)
    if rise_seconds > 0:
        rising = local_positions < rise_seconds
        derivative[rising] = (
            amplitude
            * 0.5
            * math.pi
            / rise_seconds
            * np.sin(math.pi * local_positions[rising] / rise_seconds)
        )
    fall_seconds = float(envelope.fall_duration.value)
    if fall_seconds > 0:
        fall_start = envelope_duration_seconds - fall_seconds
        falling = local_positions >= fall_start
        derivative[falling] = (
            -amplitude
            * 0.5
            * math.pi
            / fall_seconds
            * np.sin(math.pi * (local_positions[falling] - fall_start) / fall_seconds)
        )
    return derivative.astype(np.complex128)


def _sampled_waveform_semantics_id(
    grid: SampleGrid,
) -> SampledWaveformSemanticsId:
    if grid.timing.mode == "continuous":
        if grid.sample_location == "midpoint":
            return CONTINUOUS_MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID
        return CONTINUOUS_LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID
    if grid.sample_location == "midpoint":
        return MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID
    return LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID


def _envelope_duration_seconds(
    timing: RealizedEventTiming,
    grid: SampleGrid,
) -> Decimal:
    if grid.timing.mode == "continuous":
        return timing.requested_duration_seconds
    return timing.realized_duration_seconds


def _quantize_boundary(seconds: Decimal, grid: SampleGrid) -> int | None:
    scaled = seconds * Decimal(grid.sample_rate_hz)
    if grid.timing.mode == "continuous":
        sample_offset = (
            Decimal("0.5") if grid.sample_location == "midpoint" else Decimal(0)
        )
        return int((scaled - sample_offset).to_integral_value(rounding=ROUND_CEILING))
    integral = scaled.to_integral_value(rounding=ROUND_HALF_EVEN)
    if grid.timing.mode == "strict" and scaled != integral:
        return None
    return int(integral)


def _sample_seconds(sample: int, sample_rate_hz: int) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return Decimal(sample) / Decimal(sample_rate_hz)


def _issue_sort_key(issue: WaveformPlanningIssue) -> tuple[object, ...]:
    event = (
        (0, (), "")
        if issue.event_id is None
        else (1, issue.event_id.scope, issue.event_id.local_id)
    )
    return (event, issue.code, issue.message)


__all__ = [
    "CONTINUOUS_LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID",
    "CONTINUOUS_MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID",
    "LEFT_EDGE_SAMPLED_WAVEFORM_SEMANTICS_ID",
    "MIDPOINT_SAMPLED_WAVEFORM_SEMANTICS_ID",
    "SAMPLED_WAVEFORM_SEMANTICS_ID",
    "CarrierPhaseReference",
    "Float64ReferenceRenderer",
    "IqMatrix",
    "PhaseParameterizedSampledWaveforms",
    "RealizedEventTiming",
    "RenderedWaveforms",
    "ResolvedWaveformEvent",
    "SampleGrid",
    "SampleLocation",
    "SampledOutputBinding",
    "SampledRenderEvent",
    "SampledWaveformPlan",
    "SampledWaveformSemanticsId",
    "TimingQuantizationMode",
    "TimingQuantizationPolicy",
    "WaveformPlanningError",
    "WaveformPlanningIssue",
    "factor_phase_parameterized_waveforms",
    "plan_sampled_waveform_window",
    "plan_sampled_waveforms",
    "realize_event_timings",
    "resolve_waveform_events",
]
