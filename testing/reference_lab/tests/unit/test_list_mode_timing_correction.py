"""Synthetic complete-record timing contract and reference adapter consumption."""

from dataclasses import replace
from decimal import Decimal
from typing import Literal

import numpy as np
import pytest
from numpy.typing import NDArray
from scopecat.sdk.instruments.execution import RunHardwareInvoke
from scopecat_quantum.pulses import Delay, Sequence
from scopecat_quantum.targets import TargetCompileRequest
from scopecat_quantum.timing_corrections import (
    SignalTimeShift,
    TimingCorrectionSnapshot,
)

from reference_lab.payloads import (
    AWG_PROGRAM_PAYLOAD,
    DIGITIZER_PROGRAM_PAYLOAD,
    DigitizerDspWindowDocument,
    MaterializedAwgProgramDocument,
)
from reference_lab.targets.list_mode.compiler import ListModeTargetCompiler
from reference_lab.targets.list_mode.inspection import ArtifactInspectionBounds
from reference_lab.targets.list_mode.model import ListModeArtifact

from ._list_mode_test_support import (
    DRIVE_Q0,
    READOUT_Q0,
    Constant,
    InstrumentListModeRuntime,
    Play,
    PulseEventId,
    PulseProgram,
    PulseProgramId,
    Quantity,
    TargetCompilationError,
    _artifact_inspection,
    _calibrated_acquisition,
    _RecordingInstrumentExecutor,
    _request,
    _target,
    schedule,
)
from .test_list_mode_mixed_flux import mixed_target


def correction(drive: int = 0, acquire: int = 0) -> TimingCorrectionSnapshot:
    entries: tuple[tuple[Literal["drive", "acquire"], int], ...] = (
        ("drive", drive),
        ("acquire", acquire),
    )
    return TimingCorrectionSnapshot(
        sample_rate_hz=1_000_000_000,
        shifts=tuple(
            SignalTimeShift(
                signal=(role, "qubit", "q0"), seconds=Decimal(samples) / 10**9
            )
            for role, samples in entries
        ),
    )


def compile_record(
    selected: TimingCorrectionSnapshot | None = None,
) -> tuple[ListModeTargetCompiler, TargetCompileRequest, ListModeArtifact]:
    target = replace(
        _target(), timing_quantization="strict", timing_correction=selected
    )
    scheduled, _ = _calibrated_acquisition()
    compiler, request = _request(target, (scheduled,), repetitions=2)
    return compiler, request, compiler.compile(request)


def test_zero_correction_preserves_all_device_programs():
    _, _, baseline = compile_record()
    _, _, zero = compile_record(correction())
    assert baseline.entries[0].event_timings == zero.entries[0].event_timings
    assert baseline.digitizer_programs == zero.digitizer_programs
    for original, actual in zip(
        baseline.entries[0].waveforms, zero.entries[0].waveforms, strict=True
    ):
        np.testing.assert_array_equal(original.samples, actual.samples)
    assert (
        baseline.artifact_fingerprint != zero.artifact_fingerprint
    )  # explicit provenance


def test_correction_identity_cache_preview_and_recorded_payloads():
    compiler, request, artifact = compile_record(correction(2, -2))
    assert compiler.compile(request) is artifact
    _, _, baseline = compile_record()
    _, _, different = compile_record(correction(1, -1))
    assert artifact.compilation_key.value != different.compilation_key.value
    assert artifact.artifact_fingerprint != baseline.artifact_fingerprint
    assert artifact.entries[0].acquisitions[0].start_sample == 2
    assert artifact.compilation_budget.dimension("samples_per_entry").usage == 12
    assert (
        artifact.physical_footprint.waveform_bytes
        == baseline.physical_footprint.waveform_bytes
    )
    for actual, before in zip(
        artifact.entries[0].waveforms, baseline.entries[0].waveforms, strict=True
    ):
        if actual.channel_id.instrument_id == "drive-awg":
            np.testing.assert_array_equal(actual.samples[2:6], before.samples[:4])
    preview = _artifact_inspection(artifact, bounds=ArtifactInspectionBounds())
    assert (
        preview.fact("timing_correction_fingerprint").value
        == correction(2, -2).fingerprint
    )
    error = preview.fact("max_abs_boundary_error_seconds").value
    assert isinstance(error, str)
    assert Decimal(error) == 0
    instruments = _RecordingInstrumentExecutor()
    runtime = InstrumentListModeRuntime()
    runtime.prepare(artifact, execution_id="synthetic-timing", instruments=instruments)
    loaded: dict[tuple[str, tuple[str, ...]], NDArray[np.float64]] = {}
    windows: list[DigitizerDspWindowDocument] = []
    for batch in instruments.batches:
        for action in batch.actions:
            assert isinstance(action, RunHardwareInvoke)
            for payload in action.payloads.values():
                if payload.schema_id == AWG_PROGRAM_PAYLOAD.schema_id:
                    document = AWG_PROGRAM_PAYLOAD.decode(payload)
                    assert isinstance(document, MaterializedAwgProgramDocument)
                    for encoded in document.entries[0].waveforms:
                        loaded[(action.instrument_id, encoded.component_path)] = (
                            encoded.samples
                        )
                elif payload.schema_id == DIGITIZER_PROGRAM_PAYLOAD.schema_id:
                    windows.extend(
                        DIGITIZER_PROGRAM_PAYLOAD.decode(payload).entries[0].windows
                    )
    assert windows[0].start_sample == artifact.entries[0].acquisitions[0].start_sample
    for actual, displayed in zip(
        artifact.entries[0].waveforms, preview.points[0].waveforms, strict=True
    ):
        np.testing.assert_array_equal(
            loaded[(actual.channel_id.instrument_id, actual.channel_id.component_path)],
            displayed.samples,
        )
        assert actual.samples_sha256 == displayed.samples_sha256
    runtime.execute(artifact, execution_id="synthetic-timing", instruments=instruments)


@pytest.mark.parametrize("drive,acquire", [(-1, 0), (0, 1), (9, 0), (0, -5)])
def test_corrected_output_and_acquisition_bounds(drive: int, acquire: int) -> None:
    with pytest.raises(TargetCompilationError, match="outside record"):
        compile_record(correction(drive, acquire))


def test_post_shift_shared_output_conflict():
    target = _target()
    readout = next(b for b in target.output_bindings if b.signal == READOUT_Q0)
    drive = next(b for b in target.output_bindings if b.signal == DRIVE_Q0)
    # Sequential logical signals share the exact physical output calibration.
    target = replace(
        target,
        timing_quantization="strict",
        output_bindings=tuple(
            replace(readout, signal=DRIVE_Q0) if b == drive else b
            for b in target.output_bindings
        ),
    )
    scheduled = schedule(
        PulseProgram(
            PulseProgramId("shared-output"),
            Sequence(
                (
                    Play(
                        PulseEventId("d"),
                        DRIVE_Q0,
                        Constant(Quantity(4, "ns"), Quantity(0.1, "arb")),
                    ),
                    Play(
                        PulseEventId("r"),
                        READOUT_Q0,
                        Constant(Quantity(4, "ns"), Quantity(0.1, "arb")),
                    ),
                    Delay(PulseEventId("tail"), DRIVE_Q0, Quantity(4, "ns")),
                )
            ),
        )
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)
    compiler.compile(request)
    compiler, request = _request(
        replace(target, timing_correction=correction(2)), (scheduled,), repetitions=1
    )
    with pytest.raises(TargetCompilationError, match="overlap"):
        compiler.compile(request)


def test_reject_nonzero_flux_combination_and_unsupported_configuration():
    with pytest.raises(ValueError, match="flux/FIR"):
        replace(mixed_target(), timing_correction=correction(1))
    with pytest.raises(ValueError, match="strict"):
        replace(_target(), timing_correction=correction(1))
    with pytest.raises(ValueError, match="sample rate"):
        replace(_target(), timing_correction=TimingCorrectionSnapshot(sample_rate_hz=2))
    with pytest.raises(ValueError, match="configured route"):
        replace(
            _target(),
            timing_correction=TimingCorrectionSnapshot(
                sample_rate_hz=1_000_000_000,
                shifts=(
                    SignalTimeShift(
                        signal=("drive", "qubit", "missing"), seconds=Decimal(0)
                    ),
                ),
            ),
        )


def test_positive_capture_shift_and_existing_budget_checks():
    scheduled, slot = _calibrated_acquisition()
    # Extend authored full record explicitly; correction never adds pre-roll/tail.
    from scopecat_quantum.pulses import Acquire, AcquireSignal, Parallel

    scheduled = schedule(
        PulseProgram(
            PulseProgramId("padded"),
            Sequence(
                (
                    Delay(PulseEventId("lead"), DRIVE_Q0, Quantity(4, "ns")),
                    Parallel(
                        (
                            Play(
                                PulseEventId("play"),
                                DRIVE_Q0,
                                Constant(Quantity(4, "ns"), Quantity(0.1, "arb")),
                            ),
                            Acquire(
                                PulseEventId("capture"),
                                AcquireSignal(DRIVE_Q0.owner),
                                slot.id,
                                Quantity(4, "ns"),
                            ),
                        )
                    ),
                    Delay(PulseEventId("tail"), DRIVE_Q0, Quantity(4, "ns")),
                )
            ),
            acquisition_slots=(slot,),
        )
    )
    target = replace(
        _target(), timing_quantization="strict", timing_correction=correction(-2, 2)
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)
    artifact = compiler.compile(request)
    assert artifact.entries[0].acquisitions[0].start_sample == 6
    assert (
        next(
            t
            for t in artifact.entries[0].event_timings
            if t.event_id == PulseEventId("play")
        ).start_sample
        == 2
    )
    for limited in (
        replace(target, max_samples_per_entry=11),
        replace(target, max_program_waveform_bytes=1),
    ):
        compiler, request = _request(limited, (scheduled,), repetitions=1)
        with pytest.raises(TargetCompilationError):
            compiler.compile(request)


def test_corrected_phase_templates_preview_and_upload_match_direct_render():
    from reference_lab.payloads import materialize_awg_program

    def program(phase: float):
        return schedule(
            PulseProgram(
                PulseProgramId("phase-translation"),
                Sequence(
                    (
                        Delay(PulseEventId("lead"), DRIVE_Q0, Quantity(4, "ns")),
                        Play(
                            PulseEventId("play"),
                            DRIVE_Q0,
                            Constant(
                                Quantity(4, "ns"),
                                Quantity(0.1, "arb"),
                                phase=Quantity(phase, "rad"),
                            ),
                        ),
                        Delay(PulseEventId("tail"), DRIVE_Q0, Quantity(4, "ns")),
                    )
                ),
            )
        )

    target = replace(
        _target(), timing_quantization="strict", timing_correction=correction(2)
    )
    programs = tuple(program(phase) for phase in (0.0, 0.5, 1.0))
    compiler, request = _request(target, programs, repetitions=1)
    artifact = compiler.compile(request)
    assert artifact.phase_templates
    instruments = _RecordingInstrumentExecutor()
    InstrumentListModeRuntime().prepare(
        artifact, execution_id="phase-translation", instruments=instruments
    )
    loaded: dict[tuple[int, str, tuple[str, ...]], NDArray[np.float64]] = {}
    for action in instruments.batches[0].actions:
        assert isinstance(action, RunHardwareInvoke)
        for payload in action.payloads.values():
            if payload.schema_id == AWG_PROGRAM_PAYLOAD.schema_id:
                document = materialize_awg_program(AWG_PROGRAM_PAYLOAD.decode(payload))
                for index, encoded_entry in enumerate(document.entries):
                    for encoded in encoded_entry.waveforms:
                        loaded[
                            (index, action.instrument_id, encoded.component_path)
                        ] = encoded.samples
    preview = _artifact_inspection(artifact, bounds=ArtifactInspectionBounds())
    preview = _artifact_inspection(
        artifact, bounds=ArtifactInspectionBounds(max_entries=3)
    )
    for index, scheduled in enumerate(programs):
        direct_compiler, direct_request = _request(target, (scheduled,), repetitions=1)
        direct = direct_compiler.compile(direct_request)
        for actual, expected, displayed in zip(
            artifact.entry_waveforms(artifact.entries[index]),
            direct.entries[0].waveforms,
            preview.points[index].waveforms,
            strict=True,
        ):
            np.testing.assert_allclose(
                actual.samples, expected.samples, rtol=1e-14, atol=1e-16
            )
            np.testing.assert_array_equal(actual.samples, displayed.samples)
            np.testing.assert_allclose(
                loaded[
                    (
                        index,
                        actual.channel_id.instrument_id,
                        actual.channel_id.component_path,
                    )
                ],
                expected.samples,
                rtol=1e-14,
                atol=1e-16,
            )


def test_post_shift_shared_acquisition_slot_conflict():
    from scopecat_quantum._ids import AcquisitionSlotId, QubitId
    from scopecat_quantum.acquisitions import INTEGRATED_IQ_RESULT
    from scopecat_quantum.pulses import Acquire, AcquireSignal, AcquisitionSlot

    q0, q1 = AcquireSignal(QubitId("q0")), AcquireSignal(QubitId("q1"))
    slots = tuple(
        AcquisitionSlot(AcquisitionSlotId(name), INTEGRATED_IQ_RESULT, signal)
        for name, signal in [("a", q0), ("b", q1)]
    )
    target = replace(_target(), timing_quantization="strict")
    base = target.acquisition_bindings[0]
    target = replace(
        target,
        acquisition_bindings=(replace(base, signal=q0), replace(base, signal=q1)),
    )
    scheduled = schedule(
        PulseProgram(
            PulseProgramId("shared-adc"),
            Sequence(
                tuple(
                    Acquire(
                        PulseEventId(slot.id.local_id),
                        signal,
                        slot.id,
                        Quantity(4, "ns"),
                    )
                    for signal, slot in zip((q0, q1), slots, strict=True)
                )
            ),
            acquisition_slots=slots,
        )
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)
    compiler.compile(request)
    selected = TimingCorrectionSnapshot(
        sample_rate_hz=target.sample_rate_hz,
        shifts=(
            SignalTimeShift(
                signal=("acquire", "qubit", "q1"), seconds=Decimal("-2e-9")
            ),
        ),
    )
    compiler, request = _request(
        replace(target, timing_correction=selected), (scheduled,), repetitions=1
    )
    with pytest.raises(TargetCompilationError, match="demodulator slot"):
        compiler.compile(request)


def test_evidence_only_snapshot_change_invalidates_identity():
    _, _, before = compile_record(correction(1))
    _, _, after = compile_record(
        correction(1).model_copy(update={"evidence_ids": ("synthetic-evidence",)})
    )
    assert before.compilation_key.value != after.compilation_key.value
    assert before.artifact_fingerprint != after.artifact_fingerprint
    for left, right in zip(
        before.entries[0].waveforms, after.entries[0].waveforms, strict=True
    ):
        np.testing.assert_array_equal(left.samples, right.samples)


def test_offgrid_authored_boundaries_rejected_before_correction():
    scheduled = schedule(
        PulseProgram(
            PulseProgramId("offgrid"),
            Play(
                PulseEventId("play"),
                DRIVE_Q0,
                Constant(Quantity(4.5, "ns"), Quantity(0.1, "arb")),
            ),
        )
    )
    target = replace(
        _target(), timing_quantization="strict", timing_correction=correction(1)
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)
    with pytest.raises(TargetCompilationError):
        compiler.compile(request)
