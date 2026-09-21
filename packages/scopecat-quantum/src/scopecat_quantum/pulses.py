"""Hardware-independent pulse authoring and canonical scheduling.

The authoring tree in :class:`PulseProgram` describes relative composition.
:class:`ScheduledPulseProgram` is the canonical leaf of a target program's
realtime block: scheduling flattens composition, normalizes quantities, validates
acquisition closure, and proves that each logical signal has a non-overlapping
timeline.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import InitVar, dataclass, field, replace
from decimal import Decimal
from heapq import heappop, heappush
from typing import Literal

from scopecat import Quantity

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CouplerId,
    PulseEventId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import (
    QuantumResultContract,
)


@dataclass(frozen=True, slots=True)
class DriveSignal:
    """Logical microwave-drive signal for a qubit."""

    qubit: QubitId


@dataclass(frozen=True, slots=True)
class ReadoutSignal:
    """Logical readout-stimulus signal for a qubit."""

    qubit: QubitId


@dataclass(frozen=True, slots=True)
class AcquireSignal:
    """Logical acquisition signal for a qubit."""

    qubit: QubitId


@dataclass(frozen=True, slots=True)
class FluxSignal:
    """Logical flux-control signal for either a qubit or a coupler."""

    owner: QubitId | CouplerId


type LogicalSignal = DriveSignal | ReadoutSignal | AcquireSignal | FluxSignal
type PlaySignal = DriveSignal | ReadoutSignal | FluxSignal
type FrameSignal = DriveSignal | ReadoutSignal


def _zero_phase() -> Quantity:
    return Quantity(value=0.0, unit="rad")


@dataclass(frozen=True, slots=True)
class Constant:
    """A constant complex envelope over a finite duration."""

    duration: Quantity
    amplitude: Quantity
    phase: Quantity = field(default_factory=_zero_phase)


@dataclass(frozen=True, slots=True)
class Gaussian:
    """A duration-truncated Gaussian envelope."""

    duration: Quantity
    amplitude: Quantity
    sigma: Quantity
    phase: Quantity = field(default_factory=_zero_phase)


@dataclass(frozen=True, slots=True)
class CosineFlatTop:
    """A flat-top envelope joined to zero by half-cosine edges.

    ``rise_duration`` and ``fall_duration`` name the complete duration of each
    edge.  The remaining envelope duration is the plateau.  Either edge may be
    zero to request an abrupt boundary.
    """

    duration: Quantity
    amplitude: Quantity
    rise_duration: Quantity
    fall_duration: Quantity
    phase: Quantity = field(default_factory=_zero_phase)


type DifferentiableEnvelope = Gaussian | CosineFlatTop


@dataclass(frozen=True, slots=True)
class DerivativeQuadrature:
    """Add a scaled derivative of one smooth envelope in quadrature.

    ``beta`` has units of time, so ``i * beta * d(envelope) / dt`` has the same
    amplitude dimension as the base envelope.  The correction is deliberately
    independent of the base shape; targets may materialize both together after
    choosing a sample grid.
    """

    envelope: DifferentiableEnvelope
    beta: Quantity

    @property
    def duration(self) -> Quantity:
        return self.envelope.duration

    @property
    def amplitude(self) -> Quantity:
        return self.envelope.amplitude

    @property
    def phase(self) -> Quantity:
        return self.envelope.phase


type FrequencyShiftBase = Constant | DifferentiableEnvelope | DerivativeQuadrature
type EnvelopePhaseReference = Literal["start", "center"]


@dataclass(frozen=True, slots=True)
class FrequencyShift:
    """Apply a pulse-local constant frequency offset to one envelope.

    The corresponding phase ramp is reset for every envelope. ``center`` keeps
    the authored phase at the envelope midpoint, matching common DRAG-detuning
    practice without accumulating detuning phase through idle gaps.
    """

    envelope: FrequencyShiftBase
    frequency_offset: Quantity
    phase_reference: EnvelopePhaseReference = "center"

    @property
    def duration(self) -> Quantity:
        return self.envelope.duration

    @property
    def amplitude(self) -> Quantity:
        return self.envelope.amplitude

    @property
    def phase(self) -> Quantity:
        return self.envelope.phase


type AnalyticEnvelope = FrequencyShiftBase | FrequencyShift


@dataclass(frozen=True, slots=True)
class AcquisitionSlot:
    """A declared result slot that an ``Acquire`` instruction must close once."""

    id: AcquisitionSlotId
    contract: QuantumResultContract
    signal: AcquireSignal

    def __post_init__(self) -> None:
        if not self.contract.is_concrete:
            raise ValueError(
                "target acquisition slots require point-bound result dimensions"
            )


@dataclass(frozen=True, slots=True)
class Play:
    """Play an analytic envelope on a non-acquisition logical signal."""

    id: PulseEventId
    signal: PlaySignal
    envelope: AnalyticEnvelope


@dataclass(frozen=True, slots=True)
class Acquire:
    """Acquire one declared result slot."""

    id: PulseEventId
    signal: AcquireSignal
    slot_id: AcquisitionSlotId
    duration: Quantity


@dataclass(frozen=True, slots=True)
class Delay:
    """Reserve time on one logical signal without playing a waveform."""

    id: PulseEventId
    signal: LogicalSignal
    duration: Quantity


@dataclass(frozen=True, slots=True)
class ShiftPhase:
    """Advance one oscillator-backed logical signal's phase frame.

    This is a relative, zero-duration frame operation.  Keeping the phase
    change explicit lets target compilers preserve virtual-Z and readout-frame
    semantics without baking mutable frame state into analytic envelopes.
    """

    id: PulseEventId
    signal: FrameSignal
    phase: Quantity


@dataclass(frozen=True, slots=True)
class Sequence:
    """Compose instructions consecutively on a shared program timeline."""

    instructions: tuple[PulseInstruction, ...]


@dataclass(frozen=True, slots=True)
class Parallel:
    """Compose branches with common start or end times."""

    branches: tuple[PulseInstruction, ...]
    alignment: Literal["start", "end"] = "start"


@dataclass(frozen=True, slots=True)
class TimeShift:
    """Shift an entire static subtree without reserving an additional signal."""

    instruction: PulseInstruction
    duration: Quantity

    def __post_init__(self) -> None:
        value = self.duration.to("s").value
        if not math.isfinite(value) or value < 0:
            raise ValueError("pulse time shift must be finite and non-negative")


type PulseLeaf = Play | Acquire | Delay | ShiftPhase
type PulseInstruction = PulseLeaf | Sequence | Parallel | TimeShift


def iter_pulse_leaves(instruction: PulseInstruction) -> Iterator[PulseLeaf]:
    """Yield pulse leaves in deterministic structural order."""

    match instruction:
        case Play() | Acquire() | Delay() | ShiftPhase():
            yield instruction
        case TimeShift(instruction=child):
            yield from iter_pulse_leaves(child)
        case Sequence(instructions=children) | Parallel(branches=children):
            for child in children:
                yield from iter_pulse_leaves(child)


def pulse_leaf_owners(
    instruction: PulseInstruction,
) -> tuple[QubitId | CouplerId, ...]:
    """Return logical signal owners without exposing authoring internals."""

    owners: list[QubitId | CouplerId] = []
    for leaf in iter_pulse_leaves(instruction):
        owners.extend(
            signal.owner if isinstance(signal, FluxSignal) else signal.qubit
            for signal in _leaf_signals(leaf)
        )
    return tuple(owners)


@dataclass(frozen=True, slots=True)
class PulseProgram:
    """Relative pulse authoring IR; never a target-compiler input."""

    id: PulseProgramId
    body: PulseInstruction
    acquisition_slots: tuple[AcquisitionSlot, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduledPulseEvent:
    """One normalized leaf at an absolute start time in seconds."""

    id: PulseEventId
    start_seconds: Decimal
    duration_seconds: Decimal
    instruction: PulseLeaf

    def __post_init__(self) -> None:
        if self.id != self.instruction.id:
            msg = "scheduled pulse event id must match its instruction"
            raise ValueError(msg)
        if self.start_seconds < 0:
            msg = "scheduled pulse event start must be non-negative"
            raise ValueError(msg)
        if self.duration_seconds < 0:
            msg = "scheduled pulse event duration must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ScheduledPulseProgram:
    """Canonical pulse IR derived from one relative authoring program."""

    source: InitVar[PulseProgram]
    id: PulseProgramId = field(init=False)
    duration_seconds: Decimal = field(init=False)
    events: tuple[ScheduledPulseEvent, ...] = field(init=False)
    acquisition_slots: tuple[AcquisitionSlot, ...] = field(init=False)

    def __post_init__(self, source: PulseProgram) -> None:
        issues: list[PulseIssue] = []
        acquisition_uses: dict[AcquisitionSlotId, list[Acquire]] = {}
        placed, duration = _place_instruction(
            source.body,
            start=Decimal(0),
            path=(),
            issues=issues,
            seen_ids=set(),
            acquisition_uses=acquisition_uses,
        )
        slots = _validate_acquisitions(
            source.acquisition_slots,
            acquisition_uses,
            issues,
        )
        _validate_overlaps(placed, issues)
        if issues:
            raise PulseValidationError(_stable_issues(issues))
        events = tuple(
            ScheduledPulseEvent(
                id=event.leaf.id,
                start_seconds=event.start,
                duration_seconds=event.duration,
                instruction=event.leaf,
            )
            for event in _ordered_placed_leaves(placed)
        )
        object.__setattr__(self, "id", source.id)
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "acquisition_slots", slots)


@dataclass(frozen=True, slots=True)
class PulseIssue:
    """One structured pulse validation failure."""

    code: str
    message: str
    instruction_id: PulseEventId | None = None
    path: tuple[int, ...] = ()


class PulseValidationError(ValueError):
    """Aggregate of all independently discoverable pulse issues."""

    __slots__ = ("issues",)

    def __init__(self, issues: tuple[PulseIssue, ...]) -> None:
        if not issues:
            msg = "pulse validation errors require at least one issue"
            raise ValueError(msg)
        self.issues = issues
        summary = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(summary)


@dataclass(frozen=True, slots=True)
class _PlacedLeaf:
    leaf: PulseLeaf
    start: Decimal
    duration: Decimal
    path: tuple[int, ...]
    sequence_predecessors: frozenset[PulseEventId] = frozenset()


_TIME_FACTORS: dict[str, Decimal] = {
    "s": Decimal(1),
    "ms": Decimal("1e-3"),
    "us": Decimal("1e-6"),
    "ns": Decimal("1e-9"),
}


def _structural_identity_sort_key(
    value: PulseEventId | AcquisitionSlotId,
) -> tuple[tuple[str, ...], str]:
    return (value.scope, value.local_id)


def _issue(
    issues: list[PulseIssue],
    code: str,
    message: str,
    *,
    instruction_id: PulseEventId | None = None,
    path: tuple[int, ...] = (),
) -> None:
    issues.append(
        PulseIssue(
            code=code,
            message=message,
            instruction_id=instruction_id,
            path=path,
        )
    )


def _time_value(
    quantity: Quantity,
    *,
    name: str,
    issues: list[PulseIssue],
    instruction_id: PulseEventId,
    path: tuple[int, ...],
    positive: bool,
) -> Decimal | None:
    factor = _TIME_FACTORS.get(quantity.unit)
    if factor is None:
        _issue(
            issues,
            "pulse_time_unit_invalid",
            f"{name} must have a time unit, got {quantity.unit!r}",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    if not math.isfinite(quantity.value):
        _issue(
            issues,
            "pulse_quantity_nonfinite",
            f"{name} must be finite",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    result = Decimal(str(quantity.value)) * factor
    if positive and result <= 0:
        _issue(
            issues,
            "pulse_duration_nonpositive",
            f"{name} must be positive",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    return result


def _representable_quantity_seconds(
    value: Decimal,
    *,
    name: str,
    issues: list[PulseIssue],
    instruction_id: PulseEventId,
    path: tuple[int, ...],
) -> float | None:
    try:
        converted = float(value)
    except OverflowError:
        converted = math.inf if value >= 0 else -math.inf
    if not math.isfinite(converted):
        _issue(
            issues,
            "pulse_quantity_unrepresentable",
            f"{name} cannot be represented as a finite Quantity in seconds",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    if value != 0 and converted == 0:
        _issue(
            issues,
            "pulse_quantity_unrepresentable",
            f"nonzero {name} rounds to zero in a Quantity expressed in seconds",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    return converted


def _normalized_amplitude(
    quantity: Quantity,
    *,
    issues: list[PulseIssue],
    instruction_id: PulseEventId,
    path: tuple[int, ...],
) -> Quantity | None:
    if not math.isfinite(quantity.value):
        _issue(
            issues,
            "pulse_quantity_nonfinite",
            "amplitude must be finite",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    for unit in ("arb", "ratio", "V"):
        try:
            return quantity.to(unit)
        except ValueError:
            pass
    _issue(
        issues,
        "pulse_amplitude_unit_invalid",
        (
            "amplitude must be dimensionless, arbitrary, or voltage, "
            f"got {quantity.unit!r}"
        ),
        instruction_id=instruction_id,
        path=path,
    )
    return None


def _normalized_phase(
    quantity: Quantity,
    *,
    issues: list[PulseIssue],
    instruction_id: PulseEventId,
    path: tuple[int, ...],
) -> Quantity | None:
    if not math.isfinite(quantity.value):
        _issue(
            issues,
            "pulse_quantity_nonfinite",
            "phase must be finite",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    try:
        return quantity.to("rad")
    except ValueError:
        _issue(
            issues,
            "pulse_phase_unit_invalid",
            f"phase must have a phase unit, got {quantity.unit!r}",
            instruction_id=instruction_id,
            path=path,
        )
        return None


def _normalized_frequency(
    quantity: Quantity,
    *,
    issues: list[PulseIssue],
    instruction_id: PulseEventId,
    path: tuple[int, ...],
) -> Quantity | None:
    if not math.isfinite(quantity.value):
        _issue(
            issues,
            "pulse_quantity_nonfinite",
            "frequency offset must be finite",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    try:
        return quantity.to("Hz")
    except ValueError:
        _issue(
            issues,
            "pulse_frequency_unit_invalid",
            f"frequency offset must have a frequency unit, got {quantity.unit!r}",
            instruction_id=instruction_id,
            path=path,
        )
        return None


def _normalized_frequency_shift(
    envelope: FrequencyShift,
    *,
    issues: list[PulseIssue],
    instruction_id: PulseEventId,
    path: tuple[int, ...],
) -> tuple[FrequencyShift, Decimal] | None:
    if envelope.phase_reference not in {"start", "center"}:
        _issue(
            issues,
            "pulse_frequency_reference_invalid",
            "frequency-shift phase reference must be either 'start' or 'center'",
            instruction_id=instruction_id,
            path=path,
        )
        return None
    normalized_base = _normalized_envelope(
        envelope.envelope,
        issues=issues,
        instruction_id=instruction_id,
        path=path,
    )
    frequency_offset = _normalized_frequency(
        envelope.frequency_offset,
        issues=issues,
        instruction_id=instruction_id,
        path=path,
    )
    if normalized_base is None or frequency_offset is None:
        return None
    base, duration = normalized_base
    assert isinstance(
        base,
        Constant | Gaussian | CosineFlatTop | DerivativeQuadrature,
    )
    return (
        FrequencyShift(
            envelope=base,
            frequency_offset=frequency_offset,
            phase_reference=envelope.phase_reference,
        ),
        duration,
    )


def _normalized_envelope(
    envelope: AnalyticEnvelope,
    *,
    issues: list[PulseIssue],
    instruction_id: PulseEventId,
    path: tuple[int, ...],
) -> tuple[AnalyticEnvelope, Decimal] | None:
    if isinstance(envelope, FrequencyShift):
        return _normalized_frequency_shift(
            envelope,
            issues=issues,
            instruction_id=instruction_id,
            path=path,
        )

    if isinstance(envelope, DerivativeQuadrature):
        normalized_base = _normalized_envelope(
            envelope.envelope,
            issues=issues,
            instruction_id=instruction_id,
            path=path,
        )
        beta = _time_value(
            envelope.beta,
            name="derivative-quadrature beta",
            issues=issues,
            instruction_id=instruction_id,
            path=path,
            positive=False,
        )
        if normalized_base is None or beta is None:
            return None
        normalized_beta_value = _representable_quantity_seconds(
            beta,
            name="derivative-quadrature beta",
            issues=issues,
            instruction_id=instruction_id,
            path=path,
        )
        if normalized_beta_value is None:
            return None
        base, duration = normalized_base
        assert isinstance(base, Gaussian | CosineFlatTop)
        return (
            DerivativeQuadrature(
                envelope=base,
                beta=Quantity(value=normalized_beta_value, unit="s"),
            ),
            duration,
        )

    duration = _time_value(
        envelope.duration,
        name="envelope duration",
        issues=issues,
        instruction_id=instruction_id,
        path=path,
        positive=True,
    )
    amplitude = _normalized_amplitude(
        envelope.amplitude,
        issues=issues,
        instruction_id=instruction_id,
        path=path,
    )
    phase = _normalized_phase(
        envelope.phase,
        issues=issues,
        instruction_id=instruction_id,
        path=path,
    )
    sigma: Decimal | None = None
    rise_duration: Decimal | None = None
    fall_duration: Decimal | None = None
    if isinstance(envelope, Gaussian):
        sigma = _time_value(
            envelope.sigma,
            name="Gaussian sigma",
            issues=issues,
            instruction_id=instruction_id,
            path=path,
            positive=True,
        )
        if duration is not None and sigma is not None and sigma > duration:
            _issue(
                issues,
                "pulse_sigma_exceeds_duration",
                "Gaussian sigma cannot exceed the envelope duration",
                instruction_id=instruction_id,
                path=path,
            )
    if isinstance(envelope, CosineFlatTop):
        rise_duration = _time_value(
            envelope.rise_duration,
            name="cosine-flat-top rise duration",
            issues=issues,
            instruction_id=instruction_id,
            path=path,
            positive=False,
        )
        fall_duration = _time_value(
            envelope.fall_duration,
            name="cosine-flat-top fall duration",
            issues=issues,
            instruction_id=instruction_id,
            path=path,
            positive=False,
        )
        if rise_duration is not None and rise_duration < 0:
            _issue(
                issues,
                "pulse_edge_duration_negative",
                "cosine-flat-top rise duration cannot be negative",
                instruction_id=instruction_id,
                path=path,
            )
        if fall_duration is not None and fall_duration < 0:
            _issue(
                issues,
                "pulse_edge_duration_negative",
                "cosine-flat-top fall duration cannot be negative",
                instruction_id=instruction_id,
                path=path,
            )
        if (
            duration is not None
            and rise_duration is not None
            and fall_duration is not None
            and rise_duration >= 0
            and fall_duration >= 0
            and rise_duration + fall_duration > duration
        ):
            _issue(
                issues,
                "pulse_edge_durations_exceed_duration",
                "cosine-flat-top edge durations cannot exceed the envelope duration",
                instruction_id=instruction_id,
                path=path,
            )
    if duration is None or amplitude is None or phase is None:
        return None
    normalized_duration_value = _representable_quantity_seconds(
        duration,
        name="envelope duration",
        issues=issues,
        instruction_id=instruction_id,
        path=path,
    )
    if normalized_duration_value is None:
        return None
    normalized_duration = Quantity(value=normalized_duration_value, unit="s")
    if isinstance(envelope, Constant):
        return replace(
            envelope,
            duration=normalized_duration,
            amplitude=amplitude,
            phase=phase,
        ), duration
    if isinstance(envelope, CosineFlatTop):
        if (
            rise_duration is None
            or fall_duration is None
            or rise_duration < 0
            or fall_duration < 0
            or rise_duration + fall_duration > duration
        ):
            return None
        normalized_rise_value = _representable_quantity_seconds(
            rise_duration,
            name="cosine-flat-top rise duration",
            issues=issues,
            instruction_id=instruction_id,
            path=path,
        )
        normalized_fall_value = _representable_quantity_seconds(
            fall_duration,
            name="cosine-flat-top fall duration",
            issues=issues,
            instruction_id=instruction_id,
            path=path,
        )
        if normalized_rise_value is None or normalized_fall_value is None:
            return None
        return replace(
            envelope,
            duration=normalized_duration,
            amplitude=amplitude,
            rise_duration=Quantity(value=normalized_rise_value, unit="s"),
            fall_duration=Quantity(value=normalized_fall_value, unit="s"),
            phase=phase,
        ), duration
    if sigma is None:
        return None
    normalized_sigma_value = _representable_quantity_seconds(
        sigma,
        name="Gaussian sigma",
        issues=issues,
        instruction_id=instruction_id,
        path=path,
    )
    if normalized_sigma_value is None:
        return None
    normalized_sigma = Quantity(value=normalized_sigma_value, unit="s")
    return replace(
        envelope,
        duration=normalized_duration,
        amplitude=amplitude,
        sigma=normalized_sigma,
        phase=phase,
    ), duration


def _signal_key(signal: LogicalSignal) -> tuple[str, str, str]:
    match signal:
        case DriveSignal(qubit=qubit):
            return ("drive", "qubit", qubit.value)
        case ReadoutSignal(qubit=qubit):
            return ("readout", "qubit", qubit.value)
        case AcquireSignal(qubit=qubit):
            return ("acquire", "qubit", qubit.value)
        case FluxSignal(owner=owner):
            owner_kind = "qubit" if isinstance(owner, QubitId) else "coupler"
            return ("flux", owner_kind, owner.value)


def _leaf_signals(leaf: PulseLeaf) -> tuple[LogicalSignal, ...]:
    match leaf:
        case (
            Play(signal=signal)
            | Acquire(signal=signal)
            | Delay(signal=signal)
            | ShiftPhase(signal=signal)
        ):
            return (signal,)


def _place_instruction(
    instruction: PulseInstruction,
    *,
    start: Decimal,
    path: tuple[int, ...],
    issues: list[PulseIssue],
    seen_ids: set[PulseEventId],
    acquisition_uses: dict[AcquisitionSlotId, list[Acquire]],
) -> tuple[list[_PlacedLeaf], Decimal]:
    if isinstance(instruction, TimeShift):
        offset = Decimal(str(instruction.duration.to("s").value))
        events, duration = _place_instruction(
            instruction.instruction,
            start=start + offset,
            path=(*path, 0),
            issues=issues,
            seen_ids=seen_ids,
            acquisition_uses=acquisition_uses,
        )
        return events, offset + duration
    if isinstance(instruction, Sequence):
        placed: list[_PlacedLeaf] = []
        cursor = start
        preceding_ids: set[PulseEventId] = set()
        for index, child in enumerate(instruction.instructions):
            child_events, child_duration = _place_instruction(
                child,
                start=cursor,
                path=(*path, index),
                issues=issues,
                seen_ids=seen_ids,
                acquisition_uses=acquisition_uses,
            )
            if preceding_ids:
                child_events = [
                    replace(
                        event,
                        sequence_predecessors=(
                            event.sequence_predecessors | preceding_ids
                        ),
                    )
                    for event in child_events
                ]
            placed.extend(child_events)
            preceding_ids.update(event.leaf.id for event in child_events)
            cursor += child_duration
        return placed, cursor - start
    if isinstance(instruction, Parallel):
        placed = []
        duration = Decimal(0)
        branches: list[tuple[list[_PlacedLeaf], Decimal]] = []
        for index, child in enumerate(instruction.branches):
            child_events, child_duration = _place_instruction(
                child,
                start=start,
                path=(*path, index),
                issues=issues,
                seen_ids=seen_ids,
                acquisition_uses=acquisition_uses,
            )
            branches.append((child_events, child_duration))
            duration = max(duration, child_duration)
        for events, branch_duration in branches:
            offset = (
                duration - branch_duration
                if instruction.alignment == "end"
                else Decimal(0)
            )
            placed.extend(
                replace(event, start=event.start + offset) for event in events
            )
        return placed, duration

    event_id = instruction.id
    if event_id in seen_ids:
        _issue(
            issues,
            "pulse_instruction_duplicate",
            f"instruction id {event_id.value!r} is declared more than once",
            instruction_id=event_id,
            path=path,
        )
    else:
        seen_ids.add(event_id)

    normalized: PulseLeaf = instruction
    duration = Decimal(0)
    match instruction:
        case Play():
            envelope = _normalized_envelope(
                instruction.envelope,
                issues=issues,
                instruction_id=event_id,
                path=path,
            )
            if envelope is not None:
                normalized_envelope, duration = envelope
                normalized = replace(instruction, envelope=normalized_envelope)
        case Acquire():
            acquisition_uses.setdefault(instruction.slot_id, []).append(instruction)
            normalized_duration = _time_value(
                instruction.duration,
                name="acquisition duration",
                issues=issues,
                instruction_id=event_id,
                path=path,
                positive=True,
            )
            if normalized_duration is not None:
                duration = normalized_duration
                normalized_duration_value = _representable_quantity_seconds(
                    duration,
                    name="acquisition duration",
                    issues=issues,
                    instruction_id=event_id,
                    path=path,
                )
                if normalized_duration_value is not None:
                    normalized = replace(
                        instruction,
                        duration=Quantity(value=normalized_duration_value, unit="s"),
                    )
        case Delay():
            normalized_duration = _time_value(
                instruction.duration,
                name="delay duration",
                issues=issues,
                instruction_id=event_id,
                path=path,
                positive=False,
            )
            if normalized_duration is not None and normalized_duration < 0:
                _issue(
                    issues,
                    "pulse_duration_negative",
                    "delay duration must be non-negative",
                    instruction_id=event_id,
                    path=path,
                )
                normalized_duration = None
            if normalized_duration is not None:
                duration = normalized_duration
                normalized_duration_value = _representable_quantity_seconds(
                    duration,
                    name="delay duration",
                    issues=issues,
                    instruction_id=event_id,
                    path=path,
                )
                if normalized_duration_value is not None:
                    normalized = replace(
                        instruction,
                        duration=Quantity(value=normalized_duration_value, unit="s"),
                    )
        case ShiftPhase():
            normalized_phase = _normalized_phase(
                instruction.phase,
                issues=issues,
                instruction_id=event_id,
                path=path,
            )
            if normalized_phase is not None:
                normalized = replace(instruction, phase=normalized_phase)
    return [_PlacedLeaf(normalized, start, duration, path)], duration


def _validate_acquisitions(
    slots: tuple[AcquisitionSlot, ...],
    uses: dict[AcquisitionSlotId, list[Acquire]],
    issues: list[PulseIssue],
) -> tuple[AcquisitionSlot, ...]:
    declarations: dict[AcquisitionSlotId, AcquisitionSlot] = {}
    for slot in slots:
        if slot.id in declarations:
            _issue(
                issues,
                "pulse_acquisition_slot_duplicate",
                f"acquisition slot {slot.id.value!r} is declared more than once",
            )
        else:
            declarations[slot.id] = slot
    for slot_id, instructions in uses.items():
        declaration = declarations.get(slot_id)
        if declaration is None:
            for instruction in instructions:
                _issue(
                    issues,
                    "pulse_acquisition_slot_undeclared",
                    f"instruction references undeclared slot {slot_id.value!r}",
                    instruction_id=instruction.id,
                )
            continue
        if len(instructions) > 1:
            _issue(
                issues,
                "pulse_acquisition_slot_multiple",
                f"acquisition slot {slot_id.value!r} is acquired more than once",
            )
        for instruction in instructions:
            if instruction.signal != declaration.signal:
                _issue(
                    issues,
                    "pulse_acquisition_signal_mismatch",
                    f"instruction signal does not match slot {slot_id.value!r}",
                    instruction_id=instruction.id,
                )

    for slot_id in declarations.keys() - uses.keys():
        _issue(
            issues,
            "pulse_acquisition_slot_missing",
            f"declared acquisition slot {slot_id.value!r} is never acquired",
        )
    return tuple(sorted(slots, key=lambda slot: _structural_identity_sort_key(slot.id)))


def _validate_overlaps(placed: list[_PlacedLeaf], issues: list[PulseIssue]) -> None:
    by_signal: dict[LogicalSignal, list[_PlacedLeaf]] = {}
    frame_shifts: list[_PlacedLeaf] = []
    for event in placed:
        if isinstance(event.leaf, ShiftPhase):
            frame_shifts.append(event)
        if event.duration <= 0:
            continue
        for signal in _leaf_signals(event.leaf):
            by_signal.setdefault(signal, []).append(event)
    for signal, events in by_signal.items():
        ordered = sorted(
            events,
            key=lambda event: (
                event.start,
                _structural_identity_sort_key(event.leaf.id),
            ),
        )
        active_end = Decimal(-1)
        active_id: PulseEventId | None = None
        for event in ordered:
            if event.start < active_end:
                assert active_id is not None
                _issue(
                    issues,
                    "pulse_signal_overlap",
                    (
                        f"instructions {active_id.value!r} and "
                        f"{event.leaf.id.value!r} overlap on {_signal_key(signal)!r}"
                    ),
                    instruction_id=event.leaf.id,
                    path=event.path,
                )
            end = event.start + event.duration
            if end > active_end:
                active_end = end
                active_id = event.leaf.id

    for shift in frame_shifts:
        assert isinstance(shift.leaf, ShiftPhase)
        for active in by_signal.get(shift.leaf.signal, ()):
            if (
                isinstance(active.leaf, Play)
                and active.start < shift.start < active.start + active.duration
            ):
                _issue(
                    issues,
                    "pulse_frame_shift_during_play",
                    (
                        f"frame shift {shift.leaf.id.value!r} occurs inside active "
                        f"Play {active.leaf.id.value!r} on "
                        f"{_signal_key(shift.leaf.signal)!r}"
                    ),
                    instruction_id=shift.leaf.id,
                    path=shift.path,
                )


def _event_sort_key(event: _PlacedLeaf) -> tuple[object, ...]:
    signals = tuple(_signal_key(signal) for signal in _leaf_signals(event.leaf))
    instantaneous_priority = 0 if event.duration == 0 else 1
    return (
        event.start,
        instantaneous_priority,
        signals,
        _structural_identity_sort_key(event.leaf.id),
    )


def _ordered_placed_leaves(placed: list[_PlacedLeaf]) -> tuple[_PlacedLeaf, ...]:
    """Return a canonical linearization that retains Sequence causality.

    Positive durations normally make sequence order visible in timestamps.
    Zero-duration frame operations do not advance the cursor, so events at one
    instant are topologically ordered by their Sequence predecessors. Unrelated
    Parallel events retain the existing canonical signal-and-identity ordering.
    """

    by_start: dict[Decimal, list[_PlacedLeaf]] = {}
    for event in placed:
        by_start.setdefault(event.start, []).append(event)

    ordered: list[_PlacedLeaf] = []
    for start in sorted(by_start):
        events = by_start[start]
        events_by_id = {event.leaf.id: event for event in events}
        ids_at_start = events_by_id.keys()
        indegree: dict[PulseEventId, int] = {}
        successors: dict[PulseEventId, list[PulseEventId]] = {}
        for event in events:
            dependencies = event.sequence_predecessors & ids_at_start
            indegree[event.leaf.id] = len(dependencies)
            for predecessor in dependencies:
                successors.setdefault(predecessor, []).append(event.leaf.id)

        ready: list[tuple[tuple[object, ...], int, PulseEventId]] = []
        for ordinal, event in enumerate(events):
            if indegree[event.leaf.id] == 0:
                heappush(ready, (_event_sort_key(event), ordinal, event.leaf.id))

        emitted_count = 0
        while ready:
            _, _, event_id = heappop(ready)
            event = events_by_id[event_id]
            ordered.append(event)
            emitted_count += 1
            for successor_id in successors.get(event_id, ()):
                successor_indegree = indegree[successor_id] - 1
                indegree[successor_id] = successor_indegree
                if successor_indegree == 0:
                    successor = events_by_id[successor_id]
                    heappush(
                        ready,
                        (
                            _event_sort_key(successor),
                            len(events) + emitted_count,
                            successor_id,
                        ),
                    )
        if emitted_count != len(events):
            msg = "pulse sequence precedence unexpectedly contains a cycle"
            raise RuntimeError(msg)
    return tuple(ordered)


def _issue_sort_key(issue: PulseIssue) -> tuple[object, ...]:
    instruction_id = (
        (0, (), "")
        if issue.instruction_id is None
        else (1, *_structural_identity_sort_key(issue.instruction_id))
    )
    return (issue.path, issue.code, instruction_id, issue.message)


def _stable_issues(issues: list[PulseIssue]) -> tuple[PulseIssue, ...]:
    return tuple(sorted(set(issues), key=_issue_sort_key))


def schedule(program: PulseProgram) -> ScheduledPulseProgram:
    """Validate and lower a relative pulse program to canonical scheduled IR.

    All independent failures are returned together in ``PulseValidationError``.
    Exact decimal SI time is retained in the canonical representation, preserving
    sequence reassociation and parallel branch permutation without target-specific
    timeline quantization.
    """

    return ScheduledPulseProgram(program)
