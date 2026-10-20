"""Explicit software translations inside one complete sampled record.

Positive means later in the common record, not measured physical latency.
Translations preserve the rendered values (including carrier/frame phase).
No pre-roll, record extension, resampling or automatic compensation is implied.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from scopecat.kernel.content_identity import sha256_json_hash

from scopecat_quantum._ids import QubitId
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    DriveSignal,
    FluxSignal,
    Play,
    ReadoutSignal,
    ScheduledPulseProgram,
)
from scopecat_quantum.waveforms import RealizedEventTiming, SampledWaveformPlan


def timing_signal_key(
    signal: DriveSignal | ReadoutSignal | AcquireSignal | FluxSignal,
) -> tuple[str, str, str]:
    """Canonical logical coordinates, independent of a laboratory route."""
    kind = "qubit" if isinstance(signal.owner, QubitId) else "readout_line"
    if isinstance(signal, DriveSignal):
        role = "drive"
    elif isinstance(signal, ReadoutSignal):
        role = "readout"
    elif isinstance(signal, AcquireSignal):
        role = "acquire"
    else:
        role = "flux"
        kind = "qubit" if isinstance(signal.owner, QubitId) else "coupler"
    return role, kind, signal.owner.value


class SignalTimeShift(BaseModel):
    """One selected logical output/acquisition shift in seconds."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    signal: tuple[
        Literal["drive", "readout", "acquire"],
        Literal["qubit", "readout_line"],
        Annotated[str, Field(min_length=1)],
    ]
    seconds: Annotated[Decimal, Field(allow_inf_nan=False)]


class TimingCorrectionSnapshot(BaseModel):
    """Immutable explicit control values; evidence is not qualification."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    semantics_id: Literal["scopecat.timing.sample-translation.v1"] = (
        "scopecat.timing.sample-translation.v1"
    )
    sample_rate_hz: Annotated[int, Field(strict=True, gt=0)]
    shifts: tuple[SignalTimeShift, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_shifts(self) -> TimingCorrectionSnapshot:
        if len({shift.signal for shift in self.shifts}) != len(self.shifts):
            raise ValueError("timing correction signals must be unique")
        for shift in self.shifts:
            numerator, denominator = shift.seconds.as_integer_ratio()
            if numerator * self.sample_rate_hz % denominator:
                raise ValueError("timing correction must be an integer sample shift")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256_json_hash(self.model_dump(mode="json"))

    @property
    def nonzero(self) -> bool:
        return any(shift.seconds != 0 for shift in self.shifts)


def apply_timing_corrections(
    plan: SampledWaveformPlan,
    program: ScheduledPulseProgram,
    snapshot: TimingCorrectionSnapshot,
) -> SampledWaveformPlan:
    """Translate all occurrences of selected signals after strict-grid planning.

    Event order, including repeated authored IDs, is the occurrence address.
    Delays/frame operations retain their authored coordinates. Every shifted
    output or capture must fit the existing full record, including zero-width
    events. This operation is deliberately single application.
    """
    if snapshot.sample_rate_hz != plan.grid.sample_rate_hz:
        raise ValueError("timing correction sample rate does not match plan")
    if plan.time_origin_seconds != 0 or plan.source_events != program.events:
        raise ValueError("timing correction requires a complete record")
    if snapshot.nonzero and (
        program.duration_seconds * plan.grid.sample_rate_hz != plan.sample_count
    ):
        raise ValueError("timing correction requires the complete record length")
    if snapshot.nonzero and plan.grid.timing.mode != "strict":
        raise ValueError("nonzero timing correction requires strict-grid planning")
    if program.id != plan.program_id or len(program.events) != len(plan.event_timings):
        raise ValueError("timing correction requires exact scheduled event coverage")
    shifts: dict[tuple[str, str, str], int] = {
        shift.signal: shift.seconds.as_integer_ratio()[0]
        * snapshot.sample_rate_hz
        // shift.seconds.as_integer_ratio()[1]
        for shift in snapshot.shifts
    }
    translated: dict[int, RealizedEventTiming] = {}
    timings: list[RealizedEventTiming] = []
    for event, timing in zip(program.events, plan.event_timings, strict=True):
        if (
            event.id != timing.event_id
            or event.start_seconds != timing.requested_start_seconds
            or event.duration_seconds != timing.requested_duration_seconds
            or timing.correction_samples
        ):
            raise ValueError("timing correction requires uncorrected occurrence order")
        shift = (
            shifts.get(timing_signal_key(event.instruction.signal), 0)
            if isinstance(event.instruction, Play | Acquire)
            else 0
        )
        start = timing.start_sample + shift
        if start < 0 or start + timing.sample_count > plan.sample_count:
            raise ValueError(
                f"timing correction moves event {event.id.value!r} outside record"
            )
        corrected = replace(timing, start_sample=start, correction_samples=shift)
        timings.append(corrected)
        translated[id(timing)] = corrected
    return replace(
        plan,
        event_timings=tuple(timings),
        render_events=tuple(
            replace(event, timing=translated[id(event.timing)])
            for event in plan.render_events
        ),
    )
