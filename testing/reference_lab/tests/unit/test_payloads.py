from __future__ import annotations

import numpy as np
import pytest

from reference_lab.payloads import (
    AWG_PROGRAM_PAYLOAD,
    AwgChannelWaveformDocument,
    AwgEntryDocument,
    AwgMixerDocument,
    AwgPhaseTemplateDocument,
    AwgPhaseTemplateUseDocument,
    MaterializedAwgProgramDocument,
    PhaseSynthesizedAwgEntryDocument,
    PhaseSynthesizedAwgProgramDocument,
)
from reference_lab.virtual_lab.capture_payload import (
    VIRTUAL_CAPTURE_QUEUE_PAYLOAD,
    VirtualCaptureDocument,
    VirtualCaptureQueueDocument,
    VirtualCaptureTraceDocument,
)


def _phase_template(
    *,
    id: str = "drive",
    start_sample: int = 0,
    logical_i: np.ndarray | None = None,
    logical_q: np.ndarray | None = None,
) -> AwgPhaseTemplateDocument:
    return AwgPhaseTemplateDocument(
        id=id,
        i_component_path=("outputs", "ch1"),
        q_component_path=("outputs", "ch2"),
        start_sample=start_sample,
        logical_i=(np.ones(4, dtype=np.float64) if logical_i is None else logical_i),
        logical_q=(np.zeros(4, dtype=np.float64) if logical_q is None else logical_q),
        mixer=AwgMixerDocument(ii=1.0, iq=0.0, qi=0.0, qq=1.0),
    )


def _phase_entry(
    sample_count: int,
    *uses: tuple[str, float],
) -> PhaseSynthesizedAwgEntryDocument:
    return PhaseSynthesizedAwgEntryDocument(
        sample_count=sample_count,
        template_uses=tuple(
            AwgPhaseTemplateUseDocument(
                template_id=template_id,
                phase_radians=phase_radians,
            )
            for template_id, phase_radians in uses
        ),
    )


def test_awg_program_contract_keeps_samples_in_float64_binary() -> None:
    samples = np.linspace(-1.0, 1.0, 4096, dtype=np.float64)
    value = MaterializedAwgProgramDocument(
        kind="materialized",
        max_abs_amplitude=1.0,
        entries=(
            AwgEntryDocument(
                waveforms=(
                    AwgChannelWaveformDocument(
                        component_path=("outputs", "ch1"),
                        samples=samples,
                    ),
                )
            ),
        ),
    )

    encoded = AWG_PROGRAM_PAYLOAD.encode(value)
    decoded = AWG_PROGRAM_PAYLOAD.decode_content(encoded.content)

    assert isinstance(decoded, MaterializedAwgProgramDocument)
    assert encoded.codec_id == "scopecat.pydantic-buffer-bundle"
    assert encoded.codec_version == 2
    assert len(encoded.content) < samples.nbytes + 1024
    actual = decoded.entries[0].waveforms[0].samples
    assert np.shares_memory(actual, samples) is False
    assert actual.flags.writeable is False
    np.testing.assert_array_equal(actual, samples)


def test_phase_synthesized_awg_program_materializes_contiguous_buffers() -> None:
    value = PhaseSynthesizedAwgProgramDocument(
        kind="phase_synthesized",
        max_abs_amplitude=1.0,
        templates=(_phase_template(),),
        entries=(
            _phase_entry(4, ("drive", 0.0)),
            _phase_entry(4, ("drive", np.pi / 2)),
        ),
    )

    encoded = AWG_PROGRAM_PAYLOAD.encode(value)
    decoded = AWG_PROGRAM_PAYLOAD.decode_content(encoded.content)

    assert isinstance(decoded, PhaseSynthesizedAwgProgramDocument)
    materialized = decoded.materialize()
    first_i, first_q = materialized.entries[0].waveforms
    second_i, second_q = materialized.entries[1].waveforms
    np.testing.assert_allclose(first_i.samples, np.ones(4), atol=1e-15)
    np.testing.assert_allclose(first_q.samples, np.zeros(4), atol=1e-15)
    np.testing.assert_allclose(second_i.samples, np.zeros(4), atol=1e-15)
    np.testing.assert_allclose(second_q.samples, np.ones(4), atol=1e-15)
    assert all(
        waveform.samples.flags.c_contiguous and not waveform.samples.flags.writeable
        for entry in materialized.entries
        for waveform in entry.waveforms
    )


def test_phase_synthesized_awg_program_checks_materialized_amplitude() -> None:
    value = PhaseSynthesizedAwgProgramDocument(
        kind="phase_synthesized",
        max_abs_amplitude=0.5,
        templates=(_phase_template(),),
        entries=(_phase_entry(4, ("drive", 0.0)),),
    )

    with pytest.raises(ValueError, match=r"device limit is 0\.5"):
        value.materialize()


def test_phase_synthesized_awg_program_accumulates_shared_channels() -> None:
    value = PhaseSynthesizedAwgProgramDocument(
        kind="phase_synthesized",
        max_abs_amplitude=1.0,
        templates=tuple(
            _phase_template(
                id=template_id,
                start_sample=start_sample,
                logical_i=np.full(2, 0.25, dtype=np.float64),
                logical_q=np.zeros(2, dtype=np.float64),
            )
            for template_id, start_sample in (("first", 0), ("second", 1))
        ),
        entries=(_phase_entry(3, ("first", 0.0), ("second", 0.0)),),
    )

    i_waveform, q_waveform = value.materialize().entries[0].waveforms

    np.testing.assert_allclose(i_waveform.samples, [0.25, 0.5, 0.25])
    np.testing.assert_array_equal(q_waveform.samples, np.zeros(3))


def test_virtual_capture_contract_keeps_samples_in_float64_binary() -> None:
    samples = np.linspace(-0.5, 0.5, 4096, dtype=np.float64)
    value = VirtualCaptureQueueDocument(
        captures=(
            VirtualCaptureDocument(
                traces=(
                    VirtualCaptureTraceDocument(
                        instrument_id="digitizer",
                        component_path=("inputs", "ch1"),
                        samples=samples,
                    ),
                )
            ),
        )
    )

    encoded = VIRTUAL_CAPTURE_QUEUE_PAYLOAD.encode(value)
    decoded = VIRTUAL_CAPTURE_QUEUE_PAYLOAD.decode_content(encoded.content)

    assert encoded.codec_id == "scopecat.pydantic-buffer-bundle"
    assert len(encoded.content) < samples.nbytes + 1024
    trace = decoded.captures[0].traces[0]
    assert trace.samples.flags.writeable is False
    np.testing.assert_array_equal(trace.samples, samples)
