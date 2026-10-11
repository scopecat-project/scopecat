from __future__ import annotations

from ._list_mode_test_support import (
    DRIVE_Q0,
    IQ_OFFSET_COUPLING_POLICY_ID,
    Q1,
    READOUT_Q0,
    Constant,
    Decimal,
    DriveSignal,
    IqOffsetCouplingGroupDefinition,
    IqOffsetPolicyDefinition,
    ListModeTargetCompiler,
    OutputOffsetRequirement,
    Play,
    PulseEventId,
    PulseProgram,
    PulseProgramId,
    Quantity,
    QubitId,
    TargetCompilationError,
    TargetCompilerId,
    _calibrated_acquisition,
    _compiled_calibrated_acquisition,
    _modulated_samples,
    _output_binding,
    _request,
    _ReroutingPlacementProvider,
    _target,
    grouped_iq_offset_policy,
    pytest,
    replace,
    schedule,
)


def test_list_mode_compiler_projects_calibrated_physical_programs() -> None:
    target, scheduled, slot, artifact = _compiled_calibrated_acquisition()

    [entry] = artifact.entries
    drive_binding = _output_binding(target, DRIVE_Q0)
    readout_binding = _output_binding(target, READOUT_Q0)
    waveforms = {waveform.channel_id: waveform.samples for waveform in entry.waveforms}
    offset_requirements = artifact.host_state_requirements

    assert all(not waveform.flags.writeable for waveform in waveforms.values())
    assert all(waveform.flags.c_contiguous for waveform in waveforms.values())

    assert offset_requirements.policy_id == IQ_OFFSET_COUPLING_POLICY_ID
    assert offset_requirements.coupling_group_ids == (
        "drive-awg.outputs",
        "readout-awg.outputs",
    )
    assert {
        instrument_id: sum(
            requirement.channel_id.instrument_id == instrument_id
            for requirement in offset_requirements.output_offsets
        )
        for instrument_id in {"drive-awg", "readout-awg"}
    } == {"drive-awg": 9, "readout-awg": 2}
    assert set(waveforms) < {
        requirement.channel_id for requirement in offset_requirements.output_offsets
    }
    snapshot = target.device_snapshot
    assert artifact.device_snapshot == snapshot
    assert snapshot.configuration_fingerprint == target.configuration_fingerprint
    assert snapshot.snapshot_fingerprint.startswith("sha256:")
    assert (
        artifact.placement.device_snapshot_fingerprint == snapshot.snapshot_fingerprint
    )
    assert artifact.placement.logical_qubit_ids == ("q0",)
    assert {event.signal.signal[0] for event in artifact.placement.events} == {
        "acquire",
        "drive",
        "readout",
    }
    assert {constraint.kind for constraint in artifact.placement.constraints} >= {
        "configured_route",
        "shared_local_oscillator",
        "demodulator_slot",
        "timing_domain",
    }
    assert all(event.constraint_ids for event in artifact.placement.events)
    assert all(
        any(
            constraint_id.startswith("route:") for constraint_id in event.constraint_ids
        )
        for event in artifact.placement.events
    )
    candidates_by_id = {
        candidate.id: candidate for candidate in artifact.placement.candidates
    }
    assert all(event.candidate_ids for event in artifact.placement.events)
    for event in artifact.placement.events:
        candidates = tuple(
            candidates_by_id[candidate_id] for candidate_id in event.candidate_ids
        )
        [selected] = tuple(
            candidate for candidate in candidates if candidate.status == "selected"
        )
        assert selected.route == event.signal
        assert all(
            candidate.rejections
            for candidate in candidates
            if candidate.status == "rejected"
        )
    assert any(
        rejection.code == "entity_mismatch"
        for candidate in artifact.placement.candidates
        for rejection in candidate.rejections
    )
    footprint = artifact.physical_footprint
    assert footprint.instrument_ids == artifact.instrument_ids
    assert footprint.event_count == len(scheduled.events)
    assert footprint.acquisition_count == 1
    assert footprint.result_bytes == 2 * 17
    assert footprint.waveform_bytes == sum(
        waveform.samples.nbytes
        for entry in artifact.entries
        for waveform in entry.waveforms
    )

    assert scheduled.duration_seconds == Decimal("12e-9")
    drive_samples = _modulated_samples(
        0.25,
        start_sample=0,
        sample_count=4,
        intermediate_frequency_hz=drive_binding.intermediate_frequency_hz,
        sample_rate_hz=target.sample_rate_hz,
    )
    readout_samples = _modulated_samples(
        0.4,
        start_sample=4,
        sample_count=8,
        intermediate_frequency_hz=readout_binding.intermediate_frequency_hz,
        sample_rate_hz=target.sample_rate_hz,
    )
    assert waveforms[drive_binding.i_channel_id] == pytest.approx(
        tuple(sample.real for sample in drive_samples) + (0.0,) * 8
    )
    assert waveforms[drive_binding.q_channel_id] == pytest.approx(
        tuple(sample.imag for sample in drive_samples) + (0.0,) * 8
    )
    assert waveforms[readout_binding.i_channel_id] == pytest.approx(
        (0.0,) * 4 + tuple(sample.real for sample in readout_samples)
    )
    assert waveforms[readout_binding.q_channel_id] == pytest.approx(
        (0.0,) * 4 + tuple(sample.imag for sample in readout_samples)
    )
    [window] = entry.acquisitions
    assert window.slot_id == slot.id
    assert (window.start_sample, window.sample_count) == (4, 8)
    assert window.input_id.component_path == ("inputs", "ch1")
    assert window.demodulator_slot_id.value == "demod0"
    assert window.intent.demodulation_frequency_hz == -300.0e6
    assert window.intent.output_representation == "integrated_iq"
    assert window.lowering.execution == "device"
    assert window.lowering.device_result_representation == "integrated_iq"
    assert len(artifact.awg_programs) == 2
    assert len(artifact.digitizer_programs) == 1
    assert artifact.instrument_ids == (
        "drive-awg",
        "readout-awg",
        "readout-digitizer",
        "timing-controller",
    )


def test_list_mode_placement_candidates_are_bounded_per_signal() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = _target()
    drive_binding = _output_binding(target, DRIVE_Q0)
    target = replace(
        target,
        output_bindings=(
            *target.output_bindings,
            *(
                replace(
                    drive_binding,
                    signal=DriveSignal(QubitId(f"candidate-{index}")),
                )
                for index in range(20)
            ),
        ),
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)

    placement = compiler.compile(request).placement
    drive_event = next(
        event
        for event in placement.events
        if event.signal.signal == ("drive", "qubit", "q0")
    )

    assert drive_event.candidate_count > len(drive_event.candidate_ids)
    assert len(drive_event.candidate_ids) == 8
    assert placement.candidates_truncated
    assert placement.candidate_count > len(placement.candidates)
    assert any(
        candidate.status == "selected"
        for candidate in placement.candidates
        if candidate.id in drive_event.candidate_ids
    )


def test_list_mode_placement_provider_controls_physical_artifact_and_cache() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = _target()
    _compiler, request = _request(target, (scheduled,), repetitions=1)
    provider = _ReroutingPlacementProvider()
    compiler = ListModeTargetCompiler(
        TargetCompilerId("list-mode-compiler.v1"),
        target,
        placement_provider=provider,
    )

    artifact = compiler.compile(request)
    q0_binding = _output_binding(target, DRIVE_Q0)
    q1_binding = _output_binding(target, DriveSignal(Q1))
    waveform_channels = {
        waveform.channel_id for waveform in artifact.entries[0].waveforms
    }

    assert provider.calls == 1
    assert artifact.placement.provider_id == provider.id
    assert artifact.placement.provider_fingerprint == provider.fingerprint
    assert (
        artifact.compilation_key.placement_provider_fingerprint == provider.fingerprint
    )
    assert set(q1_binding.channel_ids) <= waveform_channels
    assert set(q0_binding.channel_ids).isdisjoint(waveform_channels)
    rerouted = next(
        event
        for event in artifact.placement.events
        if event.signal.signal == ("drive", "qubit", "q0")
    )
    assert {endpoint.channel_id for endpoint in rerouted.signal.endpoints} == {
        channel.value for channel in q1_binding.channel_ids
    }

    assert compiler.compile(request) is artifact
    assert provider.calls == 1
    default = ListModeTargetCompiler(compiler.id, target).compile(request)
    assert default.compilation_key.placement_fingerprint != (
        artifact.compilation_key.placement_fingerprint
    )


def test_list_mode_default_placement_reports_unconfigured_signal() -> None:
    scheduled = schedule(
        PulseProgram(
            id=PulseProgramId("unconfigured-drive"),
            body=Play(
                PulseEventId("drive"),
                DriveSignal(QubitId("q-unconfigured")),
                Constant(Quantity(2, "ns"), Quantity(0.25, "arb")),
            ),
        )
    )
    compiler, request = _request(_target(), (scheduled,), repetitions=1)

    with pytest.raises(TargetCompilationError) as caught:
        compiler.compile(request)

    assert {issue.code for issue in caught.value.issues} == {
        "list_mode_output_signal_unbound"
    }


def test_offset_coupling_groups_may_split_one_physical_awg() -> None:
    target = _target()
    [guard_requirement] = tuple(
        requirement
        for group in target.host_state_policy.coupling_groups
        for requirement in group.output_offsets
        if requirement.channel_id.component_path == ("outputs", "ch9")
    )
    target = replace(
        target,
        host_state_policy=grouped_iq_offset_policy(
            policy=IqOffsetPolicyDefinition(
                id=IQ_OFFSET_COUPLING_POLICY_ID,
                coupling_groups=(
                    IqOffsetCouplingGroupDefinition(
                        id="drive-awg.bank-01",
                        activation_chain_ids=("drive-q0", "drive-q1"),
                        required_chain_ids=("drive-q0", "drive-q1"),
                        required_output_slot_ids=("guard",),
                    ),
                    IqOffsetCouplingGroupDefinition(
                        id="drive-awg.bank-23",
                        activation_chain_ids=("drive-q2", "drive-q3"),
                        required_chain_ids=("drive-q2", "drive-q3"),
                    ),
                    IqOffsetCouplingGroupDefinition(
                        id="readout-awg.outputs",
                        activation_chain_ids=("readout",),
                        required_chain_ids=("readout",),
                    ),
                ),
            ),
            output_slots={"guard": guard_requirement},
            chain_outputs={
                binding.iq_chain_id: (
                    OutputOffsetRequirement(
                        channel_id=binding.i_channel_id,
                        offset_v=binding.mixer.i_offset_v,
                    ),
                    OutputOffsetRequirement(
                        channel_id=binding.q_channel_id,
                        offset_v=binding.mixer.q_offset_v,
                    ),
                )
                for binding in target.output_bindings
            },
        ),
    )
    scheduled = schedule(
        PulseProgram(
            PulseProgramId("q0-drive-only"),
            Play(
                PulseEventId("drive"),
                DRIVE_Q0,
                Constant(Quantity(4, "ns"), Quantity(0.25, "arb")),
            ),
        )
    )
    compiler, request = _request(target, (scheduled,), repetitions=1)

    requirements = compiler.compile(request).host_state_requirements

    assert requirements.coupling_group_ids == ("drive-awg.bank-01",)
    assert {
        requirement.channel_id.component_path
        for requirement in requirements.output_offsets
    } == {
        ("outputs", "ch1"),
        ("outputs", "ch2"),
        ("outputs", "ch3"),
        ("outputs", "ch4"),
        ("outputs", "ch9"),
    }
