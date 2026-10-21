"""Explicit whole-record flux correction before device scaling/quantization.

This is a selected correction, never a measured response or an automatic inverse.
The model acts on real voltage deltas about an independently reviewed physical
DC baseline: y[o,n] = baseline[o] + FIR[o](IIR[o](sum_i C[o,i] * x[i,n])).
It makes no assertion that this factorization describes every physical line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Annotated, Literal, cast

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.parameter import ScalarParameterValue
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.setup import SetupRevisionRef

from scopecat_quantum.iir import SosIir
from scopecat_quantum.pulses import (
    Constant,
    CosineFlatTop,
    Delay,
    FluxSignal,
    Gaussian,
    Play,
    ScheduledPulseProgram,
)
from scopecat_quantum.waveforms import (
    Float64ReferenceRenderer,
    IqMatrix,
    RenderedWaveforms,
    SampledOutputBinding,
    SampledWaveformPlan,
    SampleGrid,
    TimingQuantizationPolicy,
    plan_sampled_waveforms,
)

type Finite = Annotated[float, Field(allow_inf_nan=False)]
type AxisId = Annotated[str, Field(min_length=1)]


def flux_signal_id(signal: FluxSignal) -> str:
    """Keep qubit and coupler coordinates distinct, even for equal names."""
    from scopecat_quantum._ids import QubitId

    kind = "qubit" if isinstance(signal.owner, QubitId) else "coupler"
    return f"{kind}:{signal.owner.value}"


def _is_none(value: object) -> bool:
    return value is None


class IirTailPolicy(BaseModel):
    """Explicit IIR cutoff after zero-input samples, certified before truncation.

    The bound is in final output volts, including FIR L1 gain. FIR then drains
    completely and adds its usual baseline guard. Failure to settle is an error.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    zero_input_samples: Annotated[int, Field(strict=True, ge=0)]
    max_residual_v: Annotated[Finite, Field(gt=0)]


class FluxCorrection(BaseModel):
    """Immutable coefficients in ordered input/output coordinates, all in volts.

    Rows are physical outputs, columns are logical flux deltas. Coefficients and
    FIR taps are dimensionless. FIR tap k multiplies input n-k; explicit leading
    zero taps represent delay. Each complete shot starts at zero delta history.
    V1 drains the complete FIR tail plus a baseline guard. V2 adds ordered IIR
    sections before FIR and an explicit certified IIR cutoff policy. No implicit
    latency compensation or state carried between shots.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    semantics_id: Literal[
        "scopecat.flux.static-fir.v1", "scopecat.flux.static-iir-fir.v2"
    ] = "scopecat.flux.static-fir.v1"
    implementation: Literal["software"] = "software"
    delta_unit: Literal["V"] = "V"
    input_reference_plane: AxisId
    output_reference_plane: AxisId
    input_signals: tuple[AxisId, ...]
    output_ids: tuple[AxisId, ...]
    sample_rate_hz: Annotated[int, Field(strict=True, gt=0)]
    correction_matrix: tuple[tuple[Finite, ...], ...]
    fir_taps: tuple[tuple[Finite, ...], ...]
    baseline_v: tuple[Finite, ...]
    max_abs_voltage_v: tuple[Annotated[Finite, Field(gt=0)], ...]

    # Omitted v1 fields stay omitted in serialized historical snapshots.
    iir_filters: tuple[SosIir, ...] | None = Field(default=None, exclude_if=_is_none)
    iir_tail: IirTailPolicy | None = Field(default=None, exclude_if=_is_none)

    @model_validator(mode="after")
    def validate_axes(self) -> FluxCorrection:
        ni, no = len(self.input_signals), len(self.output_ids)
        if not ni or not no:
            raise ValueError("flux correction requires nonempty input/output axes")
        if len(set(self.input_signals)) != ni or len(set(self.output_ids)) != no:
            raise ValueError("flux correction axes must be unique")
        if len(self.correction_matrix) != no or any(
            len(row) != ni for row in self.correction_matrix
        ):
            raise ValueError("correction matrix rows=outputs, columns=inputs")
        if any(
            len(values) != no
            for values in (self.fir_taps, self.baseline_v, self.max_abs_voltage_v)
        ) or any(not taps for taps in self.fir_taps):
            raise ValueError("each physical output needs taps, baseline and limit")
        if any(
            abs(b) > limit
            for b, limit in zip(self.baseline_v, self.max_abs_voltage_v, strict=True)
        ):
            raise ValueError("DC baseline exceeds physical voltage limit")
        if self.semantics_id == "scopecat.flux.static-fir.v1":
            if self.iir_filters is not None or self.iir_tail is not None:
                raise ValueError("IIR requires explicit static-iir-fir.v2 semantics")
        elif (
            self.iir_filters is None
            or len(self.iir_filters) != no
            or self.iir_tail is None
            or any(f.sample_rate_hz != self.sample_rate_hz for f in self.iir_filters)
        ):
            raise ValueError(
                "IIR requires one filter per output, matching sample rate and tail"
            )
        elif any(self.baseline_v):
            raise ValueError("IIR v2 requires zero DC baseline")
        return self

    @property
    def tail_samples(self) -> int:
        return max(map(len, self.fir_taps)) + (
            self.iir_tail.zero_input_samples if self.iir_tail is not None else 0
        )


class FluxCorrectionSnapshot(BaseModel):
    """Resolved parameter content and existing revision references for one run.

    Evidence IDs are provenance links, not an applicability/qualification claim.
    The laboratory's existing calibration policies must still gate adoption.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    correction: FluxCorrection
    setup_revision: SetupRevisionRef
    parameter_revision: ParameterRevisionRef
    parameter_id: AxisId
    calibration_evidence_ids: tuple[AxisId, ...] = ()

    @property
    def fingerprint(self) -> str:
        return sha256_json_hash(self.model_dump(mode="json"))


def resolve_flux_correction(
    *,
    parameters: ParameterRevision,
    parameter_id: str,
    setup_revision: SetupRevisionRef,
    calibration_evidence_ids: tuple[str, ...] = (),
) -> FluxCorrectionSnapshot:
    """Freeze an explicitly selected JSON string cell from accepted parameters.

    Store this small coefficient payload in an ordinary string parameter;
    there is no independent active-calibration store or hidden lookup at render.
    """
    value = parameters.parameters.get(parameter_id)
    if not isinstance(value, ScalarParameterValue) or not isinstance(value.value, str):
        raise ValueError(
            "flux correction must be an existing scalar JSON string parameter"
        )
    return FluxCorrectionSnapshot(
        correction=FluxCorrection.model_validate_json(value.value),
        setup_revision=setup_revision,
        parameter_revision=parameters.ref,
        parameter_id=parameter_id,
        calibration_evidence_ids=calibration_evidence_ids,
    )


@dataclass(frozen=True, slots=True)
class FluxWaveformPlan:
    """A whole-record sampled plan bound to exactly one correction snapshot."""

    sampled: SampledWaveformPlan
    snapshot: FluxCorrectionSnapshot


def plan_flux_waveforms(
    program: ScheduledPulseProgram,
    *,
    snapshot: FluxCorrectionSnapshot,
    sample_rate_hz: int,
    allow_mixed_record: bool = False,
) -> FluxWaveformPlan:
    """Plan a complete strict-grid flux-only record, including the reserved tail.

    Only unmodulated real Constant/Gaussian/CosineFlatTop plays in volts and
    delays are supported. Full records are independently initialized/repeated;
    window/segment rendering and shared-state concatenation are unsupported.
    ``allow_mixed_record`` projects only flux outputs from the same complete
    record; its other outputs/acquisitions remain the enclosing target's work.
    All event boundaries and the complete record duration still use this grid.
    """
    correction = snapshot.correction
    if sample_rate_hz != correction.sample_rate_hz:
        raise ValueError("flux correction sample rate does not match target")
    signals: dict[str, FluxSignal] = {}
    for event in program.events:
        instruction = event.instruction
        if allow_mixed_record and not isinstance(instruction.signal, FluxSignal):
            continue
        if isinstance(instruction, Delay) and isinstance(
            instruction.signal, FluxSignal
        ):
            if flux_signal_id(instruction.signal) not in correction.input_signals:
                raise ValueError("flux delay has an unbound input axis")
            continue
        if not isinstance(instruction, Play) or not isinstance(
            instruction.signal, FluxSignal
        ):
            raise ValueError("flux v1 requires a complete flux-only play/delay record")
        envelope = instruction.envelope
        if not isinstance(envelope, Constant | Gaussian | CosineFlatTop):
            raise ValueError("flux v1 does not support complex/modulated envelopes")
        if envelope.amplitude.unit != "V" or float(envelope.phase.value) != 0:
            raise ValueError("flux deltas require volts and zero phase")
        signals[flux_signal_id(instruction.signal)] = instruction.signal
    if not signals or not signals.keys() <= set(correction.input_signals):
        raise ValueError("flux record contains no plays or has an unbound input axis")
    # Reuse the sampled planner/real-envelope renderer with a one-real-lane
    # projection. No IQ matrix or carrier is exposed as a flux configuration.
    bindings = tuple(
        SampledOutputBinding(signal, lane, lane, 0.0, IqMatrix(1, 0, 0, 0))
        for lane, axis in enumerate(correction.input_signals)
        if (signal := signals.get(axis)) is not None
    )
    plan = plan_sampled_waveforms(
        program,
        bindings=bindings,
        grid=SampleGrid(sample_rate_hz, timing=TimingQuantizationPolicy("strict")),
        output_signals=frozenset(signals.values()),
    )
    return FluxWaveformPlan(
        sampled=replace(
            plan,
            lane_count=len(correction.input_signals),
            sample_count=plan.sample_count + correction.tail_samples,
        ),
        snapshot=snapshot,
    )


def render_flux_waveforms(
    flux_plan: FluxWaveformPlan,
) -> RenderedWaveforms:
    """Shared preview/execution realization, with checks after the full transform."""
    plan, snapshot = flux_plan.sampled, flux_plan.snapshot
    correction = snapshot.correction
    if (
        plan.grid.timing.mode != "strict"
        or plan.grid.sample_rate_hz != correction.sample_rate_hz
        or plan.time_origin_seconds != 0
        or plan.sample_count <= correction.tail_samples
        or plan.lane_count != len(correction.input_signals)
    ):
        raise ValueError("flux rendering requires the complete matching planned record")
    body_end = plan.sample_count - correction.tail_samples
    for event in plan.render_events:
        binding = event.binding
        if (
            not isinstance(binding.signal, FluxSignal)
            or not 0 <= binding.i_lane < len(correction.input_signals)
            or flux_signal_id(binding.signal)
            != correction.input_signals[binding.i_lane]
            or binding.i_lane != binding.q_lane
            or binding.mixer != IqMatrix(1, 0, 0, 0)
            or binding.intermediate_frequency_hz != 0
            or event.timing.start_sample < 0
            or event.timing.end_sample > body_end
        ):
            raise ValueError("flux plan bindings/timing do not match correction axes")
    ideal = Float64ReferenceRenderer().render(plan)
    with np.errstate(over="ignore", invalid="ignore"):
        mixed = np.asarray(correction.correction_matrix, dtype=np.float64) @ np.asarray(
            ideal.buffers, dtype=np.float64
        )
        if correction.iir_filters is not None:
            assert correction.iir_tail is not None
            cutoff = body_end + correction.iir_tail.zero_input_samples
            for o, iir in enumerate(correction.iir_filters):
                filtered, state = iir.filter_chunk(mixed[o, :cutoff])
                residual = iir.remaining_output_bound(state) * sum(
                    abs(tap) for tap in correction.fir_taps[o]
                )
                if (
                    not math.isfinite(residual)
                    or residual > correction.iir_tail.max_residual_v
                ):
                    raise ValueError(
                        "IIR tail exceeds explicit residual voltage tolerance"
                    )
                mixed[o, :cutoff] = filtered
                # Deliberate, certified approximation under the selected policy.
                # The FIR drains fully from this cutoff; no hidden end clipping.
                mixed[o, cutoff:] = 0
        buffers = tuple(
            np.convolve(cast("NDArray[np.float64]", mixed[o]), taps, mode="full")[
                : plan.sample_count
            ]
            + baseline
            for o, (taps, baseline) in enumerate(
                zip(correction.fir_taps, correction.baseline_v, strict=True)
            )
        )
    peaks: list[float] = []
    for buffer, limit in zip(buffers, correction.max_abs_voltage_v, strict=True):
        if not np.all(np.isfinite(buffer)):
            raise ValueError("final flux waveform contains nonfinite samples")
        peak = float(cast("np.float64", np.max(np.abs(buffer))))
        if not math.isfinite(peak) or peak > limit:
            raise ValueError("final flux waveform exceeds physical voltage limit")
        peaks.append(peak)
        buffer.flags.writeable = False
    return replace(ideal, buffers=buffers, lane_peaks=tuple(peaks))
