from __future__ import annotations

from ._list_mode_test_support import (
    DRIVE_Q0,
    ArtifactInspectionBounds,
    Gaussian,
    Play,
    PulseEventId,
    PulseProgram,
    PulseProgramId,
    Quantity,
    _artifact_inspection,
    _request,
    _target,
    cast,
    np,
    schedule,
)


def test_list_mode_artifact_inspection_is_bounded_and_preserves_peaks() -> None:
    target = _target()
    scheduled = schedule(
        PulseProgram(
            id=PulseProgramId("preview"),
            body=Play(
                PulseEventId("preview-play"),
                DRIVE_Q0,
                Gaussian(
                    duration=Quantity(100.4, "ns"),
                    amplitude=Quantity(0.8, "arb"),
                    sigma=Quantity(2, "ns"),
                ),
            ),
        )
    )
    compiler, request = _request(target, (scheduled, scheduled), repetitions=3)
    artifact = compiler.compile(request)

    inspection = _artifact_inspection(
        artifact,
        bounds=ArtifactInspectionBounds(
            max_entries=1,
            max_channels_per_entry=1,
            max_samples_per_waveform=10,
        ),
    )
    [entry] = inspection.points
    [preview] = entry.waveforms
    source = artifact.entry_waveforms(artifact.entries[0])[0].samples

    assert inspection.schema_id == "scopecat.compiled_artifact_inspection.v2"
    assert inspection.kind == "reference_lab.list_mode.v1"
    assert inspection.point_count == 2
    assert inspection.points_truncated
    assert inspection.fact("max_abs_boundary_error_seconds").value == "4E-10"
    assert inspection.fact("device_snapshot_fingerprint").value == (
        artifact.device_snapshot.snapshot_fingerprint
    )
    assert inspection.fact("logical_qubit_count").value == 1
    assert inspection.fact("physical_instrument_count").value == len(
        artifact.physical_footprint.instrument_ids
    )
    assert inspection.fact("waveform_bytes").value == (
        artifact.physical_footprint.waveform_bytes
    )
    assert entry.waveform_count == 2
    assert entry.waveforms_truncated
    assert preview.source_sample_count == 100
    assert len(preview.samples) <= 10
    assert preview.sample_indices == tuple(sorted(preview.sample_indices))
    assert preview.peak_abs == float(cast("np.float64", np.max(np.abs(source))))
    assert preview.peak_abs == max(abs(sample) for sample in preview.samples)
    # The display is bounded to one entry, but work describes both compiled
    # entries and all three repetitions, not a full experiment wall-clock estimate.
    [playback] = [
        estimate
        for estimate in inspection.work_estimates
        if estimate.metric == "waveform_playback_time"
    ]
    assert playback.lower == playback.upper == 6e-7
    assert playback.unit == "s"
    assert inspection.bounds.max_points == 1
    assert inspection.bounds.max_waveforms_per_point == 1
    assert inspection.bounds.max_samples_per_waveform == 10

    complete = _artifact_inspection(
        artifact,
        bounds=ArtifactInspectionBounds(max_entries=2),
    )
    assert complete.points[0].realization_fingerprint == (
        complete.points[1].realization_fingerprint
    )
