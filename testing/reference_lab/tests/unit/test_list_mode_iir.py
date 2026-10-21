"""IIR uses the retained mixed-record path; no hardware or calibration loop."""

from dataclasses import replace

import numpy as np
import pytest
from scopecat.sdk.instruments.execution import RunHardwareInvoke
from scopecat_quantum.flux import FluxCorrection
from scopecat_quantum.iir import inverse_exponential_sos
from scopecat_quantum.targets import TargetCompilationError

from reference_lab.payloads import AWG_PROGRAM_PAYLOAD, MaterializedAwgProgramDocument
from reference_lab.targets.list_mode.model import ListModeTarget

from ._list_mode_test_support import (
    InstrumentListModeRuntime,
    _RecordingInstrumentExecutor,
    _request,
)
from .test_flux_correction import replace_snapshot
from .test_list_mode_mixed_flux import mixed_target, record
from .test_list_mode_timing_correction import correction as timing_correction


def iir_target() -> ListModeTarget:
    target = mixed_target()
    assert target.flux_correction is not None
    iir = inverse_exponential_sos(
        amplitude=0.2, tau_seconds=2e-9, sample_rate_hz=target.sample_rate_hz
    )
    c = FluxCorrection.model_validate(
        {
            **target.flux_correction.correction.model_dump(),
            "semantics_id": "scopecat.flux.static-iir-fir.v2",
            "iir_filters": (iir, iir),
            "iir_tail": {"zero_input_samples": 80, "max_residual_v": 1e-10},
        }
    )
    return replace(target, flux_correction=replace_snapshot(target.flux_correction, c))


def test_iir_mixed_record_retained_upload_iq_acquisition_reset_and_identity() -> None:
    target = iir_target()
    previous_compiler, previous_request = _request(
        mixed_target(), (record(),), repetitions=2
    )
    previous = previous_compiler.compile(previous_request)
    compiler, request = _request(target, (record(), record()), repetitions=2)
    artifact = compiler.compile(request)
    entry, original = artifact.entries[0], previous.entries[0]
    assert entry.sample_count == original.sample_count + 80
    assert entry.acquisitions == original.acquisitions
    assert artifact.flux_correction == target.flux_correction
    assert artifact.compilation_key != previous.compilation_key
    assert (
        artifact.physical_footprint.waveform_bytes
        == 2 * len(entry.waveforms) * entry.sample_count * 8
    )
    by_channel = {w.channel_id: w for w in entry.waveforms}
    for before in original.waveforms:
        after = by_channel[before.channel_id]
        if before.channel_id not in target.flux_outputs:
            np.testing.assert_array_equal(after.samples[:15], before.samples)
            np.testing.assert_array_equal(after.samples[15:], np.zeros(80))
        else:
            assert after.samples_sha256 != before.samples_sha256
            assert after.samples[-1] == 0
    for first, second in zip(
        entry.waveforms, artifact.entries[1].waveforms, strict=True
    ):
        np.testing.assert_array_equal(first.samples, second.samples)
    runtime, instruments = InstrumentListModeRuntime(), _RecordingInstrumentExecutor()
    runtime.prepare(artifact, execution_id="iir-simulation", instruments=instruments)
    uploaded = {}
    for batch in instruments.batches:
        for action in batch.actions:
            if isinstance(action, RunHardwareInvoke):
                for payload in action.payloads.values():
                    if payload.schema_id == AWG_PROGRAM_PAYLOAD.schema_id:
                        document = AWG_PROGRAM_PAYLOAD.decode(payload)
                        assert isinstance(document, MaterializedAwgProgramDocument)
                        for waveform in document.entries[0].waveforms:
                            uploaded[
                                (action.instrument_id, waveform.component_path)
                            ] = waveform.samples
    assert len(uploaded) == len(entry.waveforms)
    for waveform in entry.waveforms:
        np.testing.assert_array_equal(
            uploaded[
                (waveform.channel_id.instrument_id, waveform.channel_id.component_path)
            ],
            waveform.samples,
        )
    runtime.execute(artifact, execution_id="iir-simulation", instruments=instruments)


def test_iir_tail_sample_memory_amplitude_and_timing_boundaries() -> None:
    target = iir_target()
    for bounded, message in (
        (replace(target, max_samples_per_entry=15), "IIR/FIR tail"),
        (replace(target, max_program_waveform_bytes=6 * 15 * 8), "waveform bytes"),
        (replace(target, max_abs_amplitude=0.01), "final waveform"),
    ):
        compiler, request = _request(bounded, (record(),), repetitions=1)
        with pytest.raises(TargetCompilationError, match=message):
            compiler.compile(request)
    with pytest.raises(ValueError, match="flux/FIR/IIR"):
        replace(target, timing_correction=timing_correction(1))
    # Explicit identity timing remains compatible; no samples/windows move.
    compiler, request = _request(
        replace(target, timing_correction=timing_correction()),
        (record(),),
        repetitions=1,
    )
    assert compiler.compile(request).entries[0].sample_count == 95
