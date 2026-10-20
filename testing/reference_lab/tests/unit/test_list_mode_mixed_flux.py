"""Mixed-record adapter evidence: known flux coefficients, unchanged IQ/ADC."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from numpy.typing import NDArray
from scopecat import Quantity
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.setup import SetupRevisionRef
from scopecat.sdk.instruments.execution import RunHardwareInvoke
from scopecat_quantum._ids import (
    AcquisitionSlotId,
    PulseEventId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import INTEGRATED_IQ_RESULT
from scopecat_quantum.flux import FluxCorrection, FluxCorrectionSnapshot
from scopecat_quantum.pulses import (
    Acquire,
    AcquisitionSlot,
    Constant,
    DerivativeQuadrature,
    FluxSignal,
    Gaussian,
    Parallel,
    Play,
    PulseProgram,
    ScheduledPulseProgram,
    Sequence,
    schedule,
)
from scopecat_quantum.targets import TargetCompilationError

from reference_lab.payloads import (
    AWG_PROGRAM_PAYLOAD,
    DIGITIZER_PROGRAM_PAYLOAD,
    MaterializedAwgProgramDocument,
)
from reference_lab.physical_policies import (
    AwgChannelId,
    IqOffsetCouplingPolicy,
    OutputOffsetCouplingGroup,
    OutputOffsetRequirement,
)
from reference_lab.targets.list_mode.execution_model import ListModeRun
from reference_lab.targets.list_mode.inspection import ArtifactInspectionBounds
from reference_lab.targets.list_mode.model import (
    ListModeTarget,
    OutputChannelPreparation,
)

from ._list_mode_test_support import (
    ACQUIRE_Q0,
    DRIVE_Q0,
    READOUT_Q0,
    InstrumentListModeRuntime,
    _artifact_inspection,
    _RecordingInstrumentExecutor,
    _request,
    _target,
)

Z0, Z1 = FluxSignal(QubitId("q0")), FluxSignal(QubitId("q1"))


def mixed_target() -> ListModeTarget:
    base = _target()
    channels = tuple(
        AwgChannelId(f"z{i}", "flux-awg", ("outputs", f"z{i}")) for i in range(2)
    )
    snapshot = FluxCorrectionSnapshot(
        correction=FluxCorrection(
            input_reference_plane="desired-device-input-voltage",
            output_reference_plane="physical-dac-voltage",
            input_signals=("qubit:q0", "qubit:q1"),
            output_ids=tuple(f"{c.instrument_id}:{c.value}" for c in channels),
            sample_rate_hz=base.sample_rate_hz,
            correction_matrix=((1, 0.5), (-0.25, 1)),
            fir_taps=((1, -0.5), (0, 0.5, 0.25)),
            baseline_v=(0, 0),
            max_abs_voltage_v=(2, 2),
        ),
        setup_revision=SetupRevisionRef(
            revision_id="mixed-setup", content_hash="sha256:" + "1" * 64
        ),
        parameter_revision=ParameterRevisionRef(
            revision_id="mixed-parameters", content_hash="sha256:" + "2" * 64
        ),
        parameter_id="flux_correction",
    )
    return replace(
        base,
        timing_quantization="strict",
        flux_correction=snapshot,
        flux_signals=(Z0, Z1),
        flux_outputs=channels,
        setup_revision=snapshot.setup_revision,
        parameter_revision=snapshot.parameter_revision,
        preparation=replace(
            base.preparation,
            clocks=(
                *base.preparation.clocks,
                replace(base.preparation.clocks[0], instrument_id="flux-awg"),
            ),
            outputs=base.preparation.outputs
            + tuple(OutputChannelPreparation(c, 2.0) for c in channels),
        ),
        host_state_policy=IqOffsetCouplingPolicy(
            id="mixed-offsets",
            coupling_groups=(
                *base.host_state_policy.coupling_groups,
                OutputOffsetCouplingGroup(
                    id="flux-zero",
                    activation_channels=channels,
                    output_offsets=tuple(
                        OutputOffsetRequirement(c, 0) for c in channels
                    ),
                ),
            ),
        ),
    )


def record(
    *, flux: bool = True, capture_ns: float = 8, flux_unit: str = "V"
) -> ScheduledPulseProgram:
    slot = AcquisitionSlot(AcquisitionSlotId("iq"), INTEGRATED_IQ_RESULT, ACQUIRE_Q0)
    iq = Sequence(
        (
            Play(
                PulseEventId("drive"),
                DRIVE_Q0,
                DerivativeQuadrature(
                    Gaussian(
                        Quantity(4, "ns"), Quantity(0.2, "arb"), Quantity(1, "ns")
                    ),
                    Quantity(0.5, "ns"),
                ),
            ),
            Parallel(
                (
                    Play(
                        PulseEventId("readout"),
                        READOUT_Q0,
                        Constant(Quantity(8, "ns"), Quantity(0.4, "arb")),
                    ),
                    Acquire(
                        PulseEventId("capture"),
                        ACQUIRE_Q0,
                        slot.id,
                        Quantity(capture_ns, "ns"),
                    ),
                )
            ),
        )
    )
    body = (
        Parallel(
            (
                iq,
                Play(
                    PulseEventId("flux"),
                    Z0,
                    Constant(Quantity(12, "ns"), Quantity(0.4, flux_unit)),
                ),
            )
        )
        if flux
        else iq
    )
    return schedule(
        PulseProgram(PulseProgramId("mixed-record"), body, acquisition_slots=(slot,))
    )


def test_mixed_record_preserves_iq_samples_acquisitions_and_results() -> None:
    target = mixed_target()
    before_compiler, before_request = _request(
        replace(_target(), timing_quantization="strict"),
        (record(flux=False),),
        repetitions=2,
    )
    before = before_compiler.compile(before_request)
    compiler, request = _request(target, (record(),), repetitions=2)
    artifact = compiler.compile(request)
    entry, original = artifact.entries[0], before.entries[0]
    assert original.sample_count == 12 and entry.sample_count == 15
    assert entry.acquisitions == original.acquisitions
    original_timings = {t.event_id: t for t in original.event_timings}
    for timing in entry.event_timings:
        if timing.event_id in original_timings:
            assert timing == original_timings[timing.event_id]
    buffers = {w.channel_id: w.samples for w in entry.waveforms}
    for waveform in original.waveforms:
        np.testing.assert_array_equal(
            buffers[waveform.channel_id][:12], waveform.samples
        )
        np.testing.assert_array_equal(buffers[waveform.channel_id][12:], np.zeros(3))
    np.testing.assert_allclose(
        buffers[target.flux_outputs[0]], (0.2, *([0.1] * 11), -0.1, 0, 0)
    )
    np.testing.assert_allclose(
        buffers[target.flux_outputs[1]], (0, -0.025, *([-0.0375] * 11), -0.0125, 0)
    )
    assert artifact.flux_correction == target.flux_correction
    assert {e.instrument_id for e in artifact.physical_footprint.waveform_outputs} == {
        *(w.channel_id.instrument_id for w in original.waveforms),
        "flux-awg",
    }
    assert set(artifact.host_state_requirements.output_offsets) == {
        *before.host_state_requirements.output_offsets,
        *(OutputOffsetRequirement(c, 0) for c in target.flux_outputs),
    }
    assert artifact.physical_footprint.waveform_bytes == len(entry.waveforms) * 15 * 8
    preview = _artifact_inspection(artifact, bounds=ArtifactInspectionBounds())
    for waveform, displayed in zip(
        entry.waveforms, preview.points[0].waveforms, strict=True
    ):
        assert waveform.samples_sha256 == displayed.samples_sha256
        np.testing.assert_array_equal(waveform.samples, displayed.samples)
    runs: list[ListModeRun] = []
    for compiled in (before, artifact):
        runtime, instruments = (
            InstrumentListModeRuntime(),
            _RecordingInstrumentExecutor(),
        )
        runtime.prepare(
            compiled, execution_id="mixed-simulation", instruments=instruments
        )
        if compiled is artifact:
            loaded: dict[tuple[str, tuple[str, ...]], NDArray[np.float64]] = {}
            for batch in instruments.batches:
                for action in batch.actions:
                    if not isinstance(action, RunHardwareInvoke):
                        continue
                    for payload in action.payloads.values():
                        if payload.schema_id == AWG_PROGRAM_PAYLOAD.schema_id:
                            document = AWG_PROGRAM_PAYLOAD.decode(payload)
                            assert isinstance(document, MaterializedAwgProgramDocument)
                            for encoded in document.entries[0].waveforms:
                                loaded[
                                    (action.instrument_id, encoded.component_path)
                                ] = encoded.samples
                        elif payload.schema_id == DIGITIZER_PROGRAM_PAYLOAD.schema_id:
                            document = DIGITIZER_PROGRAM_PAYLOAD.decode(payload)
                            assert document.entries[0].windows[0].start_sample == 4
                            assert document.entries[0].windows[0].sample_count == 8
            assert len(loaded) == len(entry.waveforms)
            for waveform in entry.waveforms:
                np.testing.assert_array_equal(
                    loaded[
                        (
                            waveform.channel_id.instrument_id,
                            waveform.channel_id.component_path,
                        )
                    ],
                    waveform.samples,
                )
        runs.append(
            runtime.execute(
                compiled, execution_id="mixed-simulation", instruments=instruments
            )
        )
    assert runs[0].results.addresses == runs[1].results.addresses
    np.testing.assert_array_equal(
        runs[0].results.chunks[0].values, runs[1].results.chunks[0].values
    )
    assert compiler.compile(request) is artifact


def test_iq_only_entry_does_not_activate_flux_or_extend_its_record() -> None:
    target = mixed_target()
    compiler, request = _request(target, (record(flux=False), record()), repetitions=2)
    artifact = compiler.compile(request)
    first, second = artifact.entries
    assert first.sample_count == 12 and second.sample_count == 15
    assert not artifact.phase_templates
    assert all(w.channel_id not in target.flux_outputs for w in first.waveforms)
    assert {w.channel_id for w in second.waveforms} >= set(target.flux_outputs)
    assert "flux-awg" not in artifact.trigger_participants(first).awg_instrument_ids
    assert "flux-awg" in artifact.trigger_participants(second).awg_instrument_ids
    assert first.acquisitions == second.acquisitions


def test_zero_duration_iq_routes_do_not_shift_flux_lane_indices() -> None:
    target = mixed_target()
    program = schedule(
        PulseProgram(
            PulseProgramId("zero-iq"),
            Parallel(
                (
                    Play(
                        PulseEventId("empty-readout"),
                        READOUT_Q0,
                        Constant(Quantity(0, "ns"), Quantity(0, "arb")),
                    ),
                    Play(
                        PulseEventId("drive"),
                        DRIVE_Q0,
                        Constant(Quantity(4, "ns"), Quantity(0.1, "arb")),
                    ),
                    Play(
                        PulseEventId("flux"),
                        Z0,
                        Constant(Quantity(4, "ns"), Quantity(0.2, "V")),
                    ),
                )
            ),
        )
    )
    compiler, request = _request(target, (program,), repetitions=1)
    artifact = compiler.compile(request)
    assert (
        len(artifact.entries[0].waveforms) == 4
    )  # two drive lanes and two flux outputs
    assert {w.channel_id for w in artifact.entries[0].waveforms} >= set(
        target.flux_outputs
    )
    assert all(len(w.samples) == 7 for w in artifact.entries[0].waveforms)


@pytest.mark.parametrize(
    ("capture_ns", "flux_unit", "message"),
    [
        (7.5, "V", "strict sample grid"),
        (8, "arb", "volts and zero phase"),
    ],
)
def test_mixed_projection_keeps_timing_and_voltage_rejections(
    capture_ns: float, flux_unit: str, message: str
) -> None:
    compiler, request = _request(
        mixed_target(),
        (record(capture_ns=capture_ns, flux_unit=flux_unit),),
        repetitions=1,
    )
    with pytest.raises(TargetCompilationError, match=message):
        compiler.compile(request)


def test_mixed_tail_counts_against_sample_and_memory_budgets() -> None:
    target = mixed_target()
    for bounded in (
        replace(target, max_samples_per_entry=12),
        replace(target, max_program_waveform_bytes=6 * 12 * 8),
    ):
        compiler, request = _request(bounded, (record(),), repetitions=1)
        with pytest.raises(TargetCompilationError, match=r"FIR tail|waveform bytes"):
            compiler.compile(request)


def test_mixed_limits_and_snapshot_changes() -> None:
    target = mixed_target()
    assert target.flux_correction is not None
    snapshot = target.flux_correction
    limited = FluxCorrection.model_validate(
        {
            **snapshot.correction.model_dump(),
            "max_abs_voltage_v": (2, 0.01),
        }
    )
    limited_snapshot = FluxCorrectionSnapshot.model_validate(
        {
            **snapshot.model_dump(),
            "correction": limited,
        }
    )
    compiler, request = _request(
        replace(target, flux_correction=limited_snapshot), (record(),), repetitions=1
    )
    with pytest.raises(TargetCompilationError, match="final flux waveform"):
        compiler.compile(request)
    original, request = _request(target, (record(),), repetitions=1)
    before = original.compile(request)
    changed = FluxCorrection.model_validate(
        {
            **snapshot.correction.model_dump(),
            "correction_matrix": ((1, 0.5), (-0.5, 1)),
        }
    )
    revision = ParameterRevisionRef(
        revision_id="next-parameters", content_hash="sha256:" + "3" * 64
    )
    next_snapshot = FluxCorrectionSnapshot.model_validate(
        {
            **snapshot.model_dump(),
            "correction": changed,
            "parameter_revision": revision,
        }
    )
    other, _ = _request(
        replace(target, flux_correction=next_snapshot, parameter_revision=revision),
        (record(),),
        repetitions=1,
    )
    after = other.compile(request)
    assert before.compilation_key.value != after.compilation_key.value
    assert before.flux_correction == snapshot
    assert before.entries[0].acquisitions == after.entries[0].acquisitions
    old = {w.channel_id: w.samples for w in before.entries[0].waveforms}
    new = {w.channel_id: w.samples for w in after.entries[0].waveforms}
    for channel in old.keys() - set(target.flux_outputs):
        np.testing.assert_array_equal(old[channel], new[channel])
    np.testing.assert_allclose(
        new[target.flux_outputs[1]], 2 * old[target.flux_outputs[1]]
    )


def test_iq_only_batch_keeps_phase_factoring_with_flux_configured() -> None:
    target = mixed_target()
    compiler, request = _request(
        target, (record(flux=False), record(flux=False)), repetitions=2
    )
    artifact = compiler.compile(request)
    assert artifact.phase_templates
    assert "flux-awg" not in artifact.instrument_ids
    assert all(entry.sample_count == 12 for entry in artifact.entries)
    assert artifact.flux_correction == target.flux_correction
    assert all(
        w.channel_id not in target.flux_outputs
        for entry in artifact.entries
        for w in artifact.entry_waveforms(entry)
    )


def test_repeated_mixed_entries_keep_independent_zero_delta_history() -> None:
    target = mixed_target()
    compiler, request = _request(target, (record(), record()), repetitions=2)
    artifact = compiler.compile(request)
    first, second = artifact.entries
    assert not artifact.phase_templates
    assert first.sample_count == second.sample_count == 15
    for a, b in zip(first.waveforms, second.waveforms, strict=True):
        assert a.channel_id == b.channel_id
        np.testing.assert_array_equal(a.samples, b.samples)
        assert a.samples[-1] == 0
    runtime, instruments = InstrumentListModeRuntime(), _RecordingInstrumentExecutor()
    runtime.prepare(artifact, execution_id="mixed-repeat", instruments=instruments)
    run = runtime.execute(
        artifact, execution_id="mixed-repeat", instruments=instruments
    )
    assert len(run.results.addresses) == 2
    assert run.results.addresses[0].slot_id == run.results.addresses[1].slot_id
    assert run.results.addresses[0].entry_id != run.results.addresses[1].entry_id
    assert run.results.shot_count == 2
