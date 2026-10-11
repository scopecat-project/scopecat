from __future__ import annotations

from typing import Literal

from ._list_mode_test_support import (
    DRIVE_Q0,
    READOUT_Q0,
    READOUT_Q1,
    Constant,
    Decimal,
    DerivativeQuadrature,
    Float64ReferenceRenderer,
    FrequencyShift,
    Gaussian,
    IqMixerCalibration,
    Play,
    PulseEventId,
    PulseParallel,
    PulseProgram,
    PulseProgramId,
    PulseSequence,
    Quantity,
    RenderedWaveforms,
    SampledWaveformPlan,
    ScheduledPulseProgram,
    ShiftPhase,
    TargetCompilationError,
    _modulated_samples,
    _output_binding,
    _request,
    _target,
    math,
    np,
    pytest,
    replace,
    schedule,
)


def test_list_mode_samples_drag_and_tracks_beta_in_artifact_identity() -> None:
    def compile_drag(beta_ns: float):
        target = _target()
        scheduled = schedule(
            PulseProgram(
                id=PulseProgramId("drag"),
                body=Play(
                    PulseEventId("drag-play"),
                    DRIVE_Q0,
                    DerivativeQuadrature(
                        envelope=Gaussian(
                            duration=Quantity(4, "ns"),
                            amplitude=Quantity(0.2, "arb"),
                            sigma=Quantity(1, "ns"),
                        ),
                        beta=Quantity(beta_ns, "ns"),
                    ),
                ),
            )
        )
        compiler, request = _request(target, (scheduled,), repetitions=1)
        return target, compiler.compile(request)

    target, baseline = compile_drag(0.5)
    _, changed = compile_drag(0.75)
    binding = _output_binding(target, DRIVE_Q0)
    waveforms = {
        waveform.channel_id: waveform.samples
        for waveform in baseline.entries[0].waveforms
    }
    offsets_ns = (-1.5, -0.5, 0.5, 1.5)
    gaussians = tuple(0.2 * math.exp(-(offset**2) / 2.0) for offset in offsets_ns)
    baseband = tuple(
        complex(gaussian, -0.5 * offset * gaussian)
        for offset, gaussian in zip(offsets_ns, gaussians, strict=True)
    )
    carrier = _modulated_samples(
        1.0,
        start_sample=0,
        sample_count=4,
        intermediate_frequency_hz=binding.intermediate_frequency_hz,
        sample_rate_hz=target.sample_rate_hz,
    )
    expected = tuple(
        envelope * rotation
        for envelope, rotation in zip(baseband, carrier, strict=True)
    )

    assert target.supported_envelopes == (
        "constant",
        "gaussian",
        "cosine_flat_top",
        "derivative_quadrature",
        "frequency_shift",
    )
    assert waveforms[binding.i_channel_id] == pytest.approx(
        tuple(sample.real for sample in expected)
    )
    assert waveforms[binding.q_channel_id] == pytest.approx(
        tuple(sample.imag for sample in expected)
    )
    assert changed.artifact_fingerprint != baseline.artifact_fingerprint


def test_list_mode_samples_composed_frequency_shift_and_tracks_its_semantics() -> None:
    def compile_shift(
        offset_mhz: float,
        *,
        phase_reference: Literal["start", "center"] = "center",
    ):
        target = _target()
        scheduled = schedule(
            PulseProgram(
                id=PulseProgramId("frequency-shift"),
                body=Play(
                    PulseEventId("shifted-play"),
                    DRIVE_Q0,
                    FrequencyShift(
                        envelope=DerivativeQuadrature(
                            envelope=Gaussian(
                                duration=Quantity(4, "ns"),
                                amplitude=Quantity(0.2, "arb"),
                                sigma=Quantity(1, "ns"),
                            ),
                            beta=Quantity(-0.5, "ns"),
                        ),
                        frequency_offset=Quantity(offset_mhz, "MHz"),
                        phase_reference=phase_reference,
                    ),
                ),
            )
        )
        compiler, request = _request(target, (scheduled,), repetitions=1)
        return target, compiler.compile(request)

    target, baseline = compile_shift(-125.0)
    _, changed_offset = compile_shift(-100.0)
    _, changed_reference = compile_shift(-125.0, phase_reference="start")
    binding = _output_binding(target, DRIVE_Q0)
    waveforms = {
        waveform.channel_id: waveform.samples
        for waveform in baseline.entries[0].waveforms
    }
    offsets_ns = (-1.5, -0.5, 0.5, 1.5)
    gaussians = tuple(0.2 * math.exp(-(offset**2) / 2.0) for offset in offsets_ns)
    corrected = tuple(
        gaussian + 0.5j * offset * gaussian
        for offset, gaussian in zip(offsets_ns, gaussians, strict=True)
    )
    local_rotation = tuple(
        complex(math.cos(phase), math.sin(phase))
        for offset in offsets_ns
        for phase in (math.tau * -125e6 * offset * 1e-9,)
    )
    carrier = _modulated_samples(
        1.0,
        start_sample=0,
        sample_count=4,
        intermediate_frequency_hz=binding.intermediate_frequency_hz,
        sample_rate_hz=target.sample_rate_hz,
    )
    expected: tuple[complex, ...] = tuple(
        envelope * rotation * carrier_sample
        for envelope, rotation, carrier_sample in zip(
            corrected,
            local_rotation,
            carrier,
            strict=True,
        )
    )

    assert waveforms[binding.i_channel_id] == pytest.approx(
        tuple(sample.real for sample in expected)
    )
    assert waveforms[binding.q_channel_id] == pytest.approx(
        tuple(sample.imag for sample in expected)
    )
    assert changed_offset.artifact_fingerprint != baseline.artifact_fingerprint
    assert changed_reference.artifact_fingerprint != baseline.artifact_fingerprint


def test_list_mode_renders_gaussian_and_records_realized_timing() -> None:
    target = _target()
    scheduled = schedule(
        PulseProgram(
            id=PulseProgramId("gaussian"),
            body=Play(
                PulseEventId("gaussian-play"),
                DRIVE_Q0,
                Gaussian(
                    duration=Quantity(2.4, "ns"),
                    amplitude=Quantity(0.2, "arb"),
                    sigma=Quantity(1, "ns"),
                ),
            ),
        )
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)

    artifact = compiler.compile(request)
    [entry] = artifact.entries
    [timing] = entry.event_timings
    binding = _output_binding(target, DRIVE_Q0)
    waveforms = {waveform.channel_id: waveform.samples for waveform in entry.waveforms}
    gaussian = 0.2 * math.exp(-(0.5**2) / 2.0)
    carrier = _modulated_samples(
        gaussian,
        start_sample=0,
        sample_count=2,
        intermediate_frequency_hz=binding.intermediate_frequency_hz,
        sample_rate_hz=target.sample_rate_hz,
    )

    assert artifact.waveform_semantics_id == "scopecat.sampled.midpoint.v1"
    assert artifact.timing_quantization == "nearest"
    assert timing.requested_duration_seconds == Decimal("2.4E-9")
    assert timing.sample_count == 2
    assert timing.realized_duration_seconds == Decimal("2E-9")
    assert timing.duration_error_seconds == Decimal("-4E-10")
    assert waveforms[binding.i_channel_id] == pytest.approx(
        tuple(sample.real for sample in carrier)
    )
    assert waveforms[binding.q_channel_id] == pytest.approx(
        tuple(sample.imag for sample in carrier)
    )


def test_list_mode_applies_shift_phase_before_playback() -> None:
    target = _target()
    scheduled = schedule(
        PulseProgram(
            id=PulseProgramId("phase-shift"),
            body=PulseSequence(
                (
                    ShiftPhase(
                        PulseEventId("shift"),
                        DRIVE_Q0,
                        Quantity(math.pi / 2, "rad"),
                    ),
                    Play(
                        PulseEventId("play"),
                        DRIVE_Q0,
                        Constant(Quantity(2, "ns"), Quantity(0.25, "arb")),
                    ),
                )
            ),
        )
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)

    artifact = compiler.compile(request)
    binding = _output_binding(target, DRIVE_Q0)
    waveforms = {
        waveform.channel_id: waveform.samples
        for waveform in artifact.entries[0].waveforms
    }
    expected = tuple(
        sample * 1j
        for sample in _modulated_samples(
            0.25,
            start_sample=0,
            sample_count=2,
            intermediate_frequency_hz=binding.intermediate_frequency_hz,
            sample_rate_hz=target.sample_rate_hz,
        )
    )

    assert waveforms[binding.i_channel_id] == pytest.approx(
        tuple(sample.real for sample in expected)
    )
    assert waveforms[binding.q_channel_id] == pytest.approx(
        tuple(sample.imag for sample in expected)
    )


def test_list_mode_factors_phase_sweeps_without_per_point_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()
    rendered_plans: list[SampledWaveformPlan] = []
    render = Float64ReferenceRenderer.render

    def record_render(
        self: Float64ReferenceRenderer,
        plan: SampledWaveformPlan,
    ) -> RenderedWaveforms:
        rendered_plans.append(plan)
        return render(self, plan)

    monkeypatch.setattr(Float64ReferenceRenderer, "render", record_render)

    def phase_program(program_id: str, phase: float) -> ScheduledPulseProgram:
        return schedule(
            PulseProgram(
                id=PulseProgramId(program_id),
                body=PulseSequence(
                    (
                        ShiftPhase(
                            PulseEventId("shift"),
                            DRIVE_Q0,
                            Quantity(phase, "rad"),
                        ),
                        Play(
                            PulseEventId("play"),
                            DRIVE_Q0,
                            Constant(Quantity(2, "ns"), Quantity(0.25, "arb")),
                        ),
                    )
                ),
            )
        )

    programs = (
        phase_program("phase-zero", 0.0),
        phase_program("phase-quarter", math.pi / 2),
    )
    compiler, request = _request(target, programs, repetitions=1)

    artifact = compiler.compile(request)

    assert artifact.phase_templates
    assert all(not entry.waveforms for entry in artifact.entries)
    assert len(rendered_plans) == 1
    for index, program in enumerate(programs):
        concrete_compiler, concrete_request = _request(
            target,
            (program,),
            repetitions=1,
        )
        concrete = concrete_compiler.compile(concrete_request)
        synthesized = artifact.entry_waveforms(artifact.entries[index])
        assert [waveform.channel_id for waveform in synthesized] == [
            waveform.channel_id for waveform in concrete.entries[0].waveforms
        ]
        for actual, expected_waveform in zip(
            synthesized,
            concrete.entries[0].waveforms,
            strict=True,
        ):
            np.testing.assert_allclose(
                actual.samples,
                expected_waveform.samples,
                atol=1e-15,
            )
        assert artifact.materialized_waveform_bytes(artifact.entries[index]) == sum(
            waveform.samples.nbytes for waveform in concrete.entries[0].waveforms
        )


def test_list_mode_defers_compact_sweep_amplitude_check_until_materialization() -> None:
    target = replace(_target(), max_abs_amplitude=0.1)

    def phase_program(program_id: str, phase: float) -> ScheduledPulseProgram:
        return schedule(
            PulseProgram(
                id=PulseProgramId(program_id),
                body=PulseSequence(
                    (
                        ShiftPhase(
                            PulseEventId("shift"),
                            DRIVE_Q0,
                            Quantity(phase, "rad"),
                        ),
                        Play(
                            PulseEventId("play"),
                            DRIVE_Q0,
                            Constant(Quantity(2, "ns"), Quantity(0.25, "arb")),
                        ),
                    )
                ),
            )
        )

    compiler, request = _request(
        target,
        (
            phase_program("phase-zero", 0.0),
            phase_program("phase-quarter", math.pi / 2),
        ),
        repetitions=1,
    )

    artifact = compiler.compile(request)

    with pytest.raises(ValueError, match=r"target limit is 0\.1"):
        artifact.entry_waveforms(artifact.entries[0])


def test_list_mode_checks_final_peak_after_readout_accumulation() -> None:
    target = _target()
    target = replace(
        target,
        output_bindings=tuple(
            replace(binding, intermediate_frequency_hz=0.0)
            if binding.signal in {READOUT_Q0, READOUT_Q1}
            else binding
            for binding in target.output_bindings
        ),
    )
    scheduled = schedule(
        PulseProgram(
            id=PulseProgramId("multiplexed-peak"),
            body=PulseParallel(
                (
                    Play(
                        PulseEventId("readout-q0"),
                        READOUT_Q0,
                        Constant(Quantity(2, "ns"), Quantity(0.6, "arb")),
                    ),
                    Play(
                        PulseEventId("readout-q1"),
                        READOUT_Q1,
                        Constant(Quantity(2, "ns"), Quantity(0.6, "arb")),
                    ),
                )
            ),
        )
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)

    with pytest.raises(TargetCompilationError) as caught:
        compiler.compile(request)

    assert {issue.code for issue in caught.value.issues} == {
        "list_mode_amplitude_limit_exceeded"
    }


def test_list_mode_applies_full_iq_mixer_matrix_to_physical_waveforms() -> None:
    target = _target()
    binding = _output_binding(target, DRIVE_Q0)
    calibrated_binding = replace(
        binding,
        mixer=IqMixerCalibration(
            ii=0.8,
            iq=0.1,
            qi=-0.2,
            qq=0.9,
            i_offset_v=0.01,
            q_offset_v=-0.02,
        ),
    )
    target = replace(
        target,
        output_bindings=tuple(
            calibrated_binding if candidate == binding else candidate
            for candidate in target.output_bindings
        ),
    )
    scheduled = schedule(
        PulseProgram(
            PulseProgramId("mixer-calibration"),
            Play(
                PulseEventId("drive"),
                DRIVE_Q0,
                Constant(Quantity(4, "ns"), Quantity(0.25, "arb")),
            ),
        )
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)

    artifact = compiler.compile(request)
    waveforms = {
        waveform.channel_id: waveform.samples
        for waveform in artifact.entries[0].waveforms
    }
    ideal = _modulated_samples(
        0.25,
        start_sample=0,
        sample_count=4,
        intermediate_frequency_hz=calibrated_binding.intermediate_frequency_hz,
        sample_rate_hz=target.sample_rate_hz,
    )
    assert waveforms[calibrated_binding.i_channel_id] == pytest.approx(
        tuple(0.8 * sample.real + 0.1 * sample.imag for sample in ideal)
    )
    assert waveforms[calibrated_binding.q_channel_id] == pytest.approx(
        tuple(-0.2 * sample.real + 0.9 * sample.imag for sample in ideal)
    )
