from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from scopecat import Quantity
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.setup import SetupRevisionRef
from scopecat_quantum._ids import PulseEventId, PulseProgramId, QubitId
from scopecat_quantum.flux import FluxCorrection, FluxCorrectionSnapshot
from scopecat_quantum.pulses import Constant, FluxSignal, Play, PulseProgram, schedule
from scopecat_quantum.targets import TargetCompilationError

from reference_lab.physical_policies import (
    IqOffsetCouplingPolicy,
    OutputOffsetCouplingGroup,
    OutputOffsetRequirement,
)
from reference_lab.targets.list_mode.inspection import ArtifactInspectionBounds
from reference_lab.targets.list_mode.model import ListModeTarget

from ._list_mode_test_support import (
    InstrumentListModeRuntime,
    _artifact_inspection,
    _RecordingInstrumentExecutor,
    _request,
    _target,
)

Z0, Z1 = FluxSignal(QubitId("q0")), FluxSignal(QubitId("q1"))


def flux_target() -> ListModeTarget:
    base = _target()
    outputs = base.preparation.outputs[:2]
    channels = tuple(o.channel_id for o in outputs)
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
            revision_id="setup-1", content_hash="sha256:" + "1" * 64
        ),
        parameter_revision=ParameterRevisionRef(
            revision_id="parameters-1", content_hash="sha256:" + "2" * 64
        ),
        parameter_id="flux_correction",
    )
    return replace(
        base,
        output_bindings=(),
        acquisition_bindings=(),
        timing_quantization="strict",
        flux_correction=snapshot,
        setup_revision=snapshot.setup_revision,
        parameter_revision=snapshot.parameter_revision,
        flux_signals=(Z0, Z1),
        flux_outputs=channels,
        preparation=replace(
            base.preparation,
            outputs=tuple(replace(o, amplitude_v=2.0) for o in outputs),
        ),
        host_state_policy=IqOffsetCouplingPolicy(
            id="zero-flux-offsets",
            coupling_groups=(
                OutputOffsetCouplingGroup(
                    id="flux",
                    activation_channels=channels,
                    output_offsets=tuple(
                        OutputOffsetRequirement(c, 0.0) for c in channels
                    ),
                ),
            ),
        ),
    )


def flux_program(amplitude: float = 0.4):
    return schedule(
        PulseProgram(
            PulseProgramId("flux"),
            Play(
                PulseEventId("step"),
                Z0,
                Constant(Quantity(2, "ns"), Quantity(amplitude, "V")),
            ),
        )
    )


def test_flux_preview_execution_buffers_resources_and_snapshot() -> None:
    target = flux_target()
    compiler, request = _request(
        target, (flux_program(), flux_program()), repetitions=2
    )
    artifact = compiler.compile(request)
    assert not artifact.phase_templates
    assert artifact.flux_correction == target.flux_correction
    assert artifact.entries[0].sample_count == 5  # 2 body + full tail + zero guard
    expected = ((0.2, 0.1, -0.1, 0, 0), (0, -0.025, -0.0375, -0.0125, 0))
    for entry in artifact.entries:
        np.testing.assert_allclose([w.samples for w in entry.waveforms], expected)
    assert {e.channel_id for e in artifact.physical_footprint.waveform_outputs} == {
        c.value for c in target.flux_outputs
    }
    assert len(artifact.host_state_requirements.output_offsets) == 2
    inspection = _artifact_inspection(artifact, bounds=ArtifactInspectionBounds())
    # Preview projects the exact retained buffers, not a second correction pass.
    for waveform, preview in zip(
        artifact.entries[0].waveforms, inspection.points[0].waveforms, strict=True
    ):
        assert waveform.samples_sha256 == preview.samples_sha256
        np.testing.assert_allclose(preview.samples, waveform.samples)
    assert any(f.id == "flux_correction_fingerprint" for f in inspection.facts)
    runtime = InstrumentListModeRuntime()
    instruments = _RecordingInstrumentExecutor()
    runtime.prepare(artifact, execution_id="flux-simulation", instruments=instruments)
    runtime.execute(artifact, execution_id="flux-simulation", instruments=instruments)
    assert instruments.batches
    assert artifact.awg_programs[0].entries[0].sample_count == 5
    assert compiler.compile(request) is artifact


def test_flux_revisions_rate_axes_and_host_offsets_are_checked() -> None:
    target = flux_target()
    with pytest.raises(ValueError, match="revisions"):
        replace(target, setup_revision=None)
    with pytest.raises(ValueError, match="sample rate"):
        replace(target, sample_rate_hz=500_000_000)
    with pytest.raises(ValueError, match="axes"):
        replace(target, flux_outputs=tuple(reversed(target.flux_outputs)))
    with pytest.raises(ValueError, match="zero hardware offsets"):
        replace(
            target,
            host_state_policy=IqOffsetCouplingPolicy(id="missing", coupling_groups=()),
        )


def test_flux_tail_and_final_dac_amplitude_are_checked() -> None:
    target = flux_target()
    compiler, request = _request(
        replace(target, max_samples_per_entry=3), (flux_program(),), repetitions=1
    )
    with pytest.raises(TargetCompilationError, match="FIR tail"):
        compiler.compile(request)
    compiler, request = _request(
        replace(target, max_abs_amplitude=0.1), (flux_program(),), repetitions=1
    )
    with pytest.raises(TargetCompilationError, match="final waveform"):
        compiler.compile(request)


def test_flux_coefficients_and_provenance_invalidate_caches() -> None:
    target = flux_target()
    assert target.flux_correction is not None
    frozen = target.flux_correction
    changed = frozen.model_copy(update={"calibration_evidence_ids": ("new-evidence",)})
    compiler, request = _request(target, (flux_program(),), repetitions=1)
    old = compiler.compile(request)
    other, _ = _request(
        replace(target, flux_correction=changed), (flux_program(),), repetitions=1
    )
    new = other.compile(request)
    assert old.compilation_key.value != new.compilation_key.value
    assert old.artifact_fingerprint != new.artifact_fingerprint
    assert old.flux_correction == frozen


def test_flux_rejects_conflicting_offset_policies_and_nonzero_dc() -> None:
    target = flux_target()
    original = target.host_state_policy.coupling_groups[0]
    conflict = replace(
        original,
        id="conflicting",
        output_offsets=(OutputOffsetRequirement(target.flux_outputs[0], 0.1),),
    )
    with pytest.raises(ValueError, match="zero hardware offsets"):
        replace(
            target,
            host_state_policy=replace(
                target.host_state_policy, coupling_groups=(conflict, original)
            ),
        )
    assert target.flux_correction is not None
    correction = FluxCorrection.model_validate(
        {
            **target.flux_correction.correction.model_dump(),
            "baseline_v": (0.1, 0),
        }
    )
    with pytest.raises(ValueError, match="zero DC baseline"):
        replace(
            target, flux_correction=replace_snapshot(target.flux_correction, correction)
        )


def replace_snapshot(
    snapshot: FluxCorrectionSnapshot, correction: FluxCorrection
) -> FluxCorrectionSnapshot:
    return FluxCorrectionSnapshot.model_validate(
        {**snapshot.model_dump(), "correction": correction}
    )
