from __future__ import annotations

from ._list_mode_test_support import (
    InstrumentListModeRuntime,
    RunHardwareCollect,
    TargetAcquisitionAddress,
    TargetCompileEntryId,
    _calibrated_acquisition,
    _compiled_calibrated_acquisition,
    _IndeterminateInstrumentExecutor,
    _RecordingInstrumentExecutor,
    _request,
    _target,
    cast,
    np,
    pytest,
    replace,
)


def test_list_mode_worker_protocol_is_stable_per_execution_identity() -> None:
    _target, _scheduled, slot, artifact = _compiled_calibrated_acquisition()
    instruments = _RecordingInstrumentExecutor()
    instrument_runtime = InstrumentListModeRuntime()
    instrument_runtime.prepare(
        artifact,
        execution_id="test.calibrated-acquisition",
        instruments=instruments,
    )
    instrument_run = instrument_runtime.execute(
        artifact,
        execution_id="test.calibrated-acquisition",
        instruments=instruments,
    )
    assert instrument_run.results.addresses == (
        TargetAcquisitionAddress(
            entry_id=TargetCompileEntryId("entry-0"),
            slot_id=slot.id,
        ),
    )
    [result_chunk] = instrument_run.results.chunks
    assert result_chunk.values.shape == (1, 2)
    assert result_chunk.values.nbytes == 2 * np.dtype(np.complex128).itemsize
    assert result_chunk.available.nbytes == 2 * np.dtype(np.bool_).itemsize
    assert not result_chunk.values.flags.writeable
    assert not result_chunk.available.flags.writeable
    assert np.all(result_chunk.available)
    first_value = cast("np.complex128", result_chunk.values[0, 0])
    second_value = cast("np.complex128", result_chunk.values[0, 1])
    assert second_value == pytest.approx(first_value)

    assert instruments.batches[0].operation_id.endswith(":load")
    assert instruments.batches[1].operation_id.endswith(":load-trigger")
    assert instruments.batches[2].operation_id.endswith(":prepare")
    assert all(
        "target:test.calibrated-acquisition:" in batch.operation_id
        for batch in instruments.batches
    )
    assert [action.kind for action in instruments.batches[0].actions] == [
        "invoke",
        "invoke",
        "invoke",
    ]
    assert [batch.operation_id.rsplit(":", 1)[-1] for batch in instruments.batches] == [
        "load",
        "load-trigger",
        "prepare",
        "execute",
    ]
    assert [action.kind for action in instruments.batches[3].actions] == [
        "invoke",
        "invoke",
        "invoke",
        "invoke",
        "collect",
    ]

    other_execution = _RecordingInstrumentExecutor()
    other_runtime = InstrumentListModeRuntime()
    other_runtime.prepare(
        artifact,
        execution_id="test.other-invocation",
        instruments=other_execution,
    )
    other_runtime.execute(
        artifact,
        execution_id="test.other-invocation",
        instruments=other_execution,
    )
    assert {batch.operation_id for batch in instruments.batches}.isdisjoint(
        batch.operation_id for batch in other_execution.batches
    )
    retry = _RecordingInstrumentExecutor()
    retry_runtime = InstrumentListModeRuntime()
    retry_runtime.prepare(
        artifact,
        execution_id="test.calibrated-acquisition",
        instruments=retry,
    )
    retry_runtime.execute(
        artifact,
        execution_id="test.calibrated-acquisition",
        instruments=retry,
    )
    assert [batch.operation_id for batch in retry.batches] == [
        batch.operation_id for batch in instruments.batches
    ]


def test_list_mode_worker_retains_bounded_shot_chunks() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(_target(), max_result_chunk_bytes=34)
    compiler, request = _request(target, (scheduled,), repetitions=5)
    artifact = compiler.compile(request)
    instruments = _RecordingInstrumentExecutor()
    runtime = InstrumentListModeRuntime()
    runtime.prepare(artifact, execution_id="test.chunked", instruments=instruments)

    run = runtime.execute(
        artifact,
        execution_id="test.chunked",
        instruments=instruments,
    )

    assert [chunk.shot_start for chunk in run.results.chunks] == [0, 2, 4]
    assert [chunk.shot_count for chunk in run.results.chunks] == [2, 2, 1]
    collect_actions = [
        action
        for batch in instruments.batches
        for action in batch.actions
        if isinstance(action, RunHardwareCollect)
    ]
    assert len(collect_actions) == 3
    assert [action.requests[0].dimensions[0].offset for action in collect_actions] == [
        0,
        2,
        4,
    ]
    assert [action.requests[0].dimensions[0].size for action in collect_actions] == [
        2,
        2,
        1,
    ]
    assert all(
        action.kind == "collect"
        for batch in instruments.batches[-2:]
        for action in batch.actions
    )
    assert all(
        chunk.values.nbytes + chunk.available.nbytes <= artifact.max_result_chunk_bytes
        for chunk in run.results.chunks
    )
    assert run.results.shot_count == 5
    assert all(np.all(chunk.available) for chunk in run.results.chunks)


def test_list_mode_realtime_action_count_does_not_scale_with_repetitions() -> None:
    _target, _scheduled, _slot, artifact = _compiled_calibrated_acquisition()
    action_counts: list[list[int]] = []
    for repetitions in (1, 32):
        instruments = _RecordingInstrumentExecutor()
        runtime = InstrumentListModeRuntime()
        selected = replace(artifact, repetitions=repetitions)
        runtime.prepare(
            selected,
            execution_id=f"test.repetitions-{repetitions}",
            instruments=instruments,
        )
        runtime.execute(
            selected,
            execution_id=f"test.repetitions-{repetitions}",
            instruments=instruments,
        )
        action_counts.append([len(batch.actions) for batch in instruments.batches])

    assert action_counts == [[3, 1, 3, 5], [3, 1, 3, 5]]


def test_list_mode_acquisition_lowering_selects_target_or_device_dsp() -> None:
    target, scheduled, _slot, artifact = _compiled_calibrated_acquisition()
    [device_window] = artifact.entries[0].acquisitions
    assert device_window.lowering.execution == "device"
    assert device_window.lowering.device_result_representation == "integrated_iq"
    assert artifact.digitizer_programs[0].result_representation == "integrated_iq"

    target_dsp = replace(
        target,
        digitizer_result_representation="raw_trace",
    )
    target_compiler, target_request = _request(
        target_dsp,
        (scheduled,),
        repetitions=2,
    )
    target_artifact = target_compiler.compile(target_request)
    [target_window] = target_artifact.entries[0].acquisitions
    assert target_window.intent == device_window.intent
    assert target_window.lowering.execution == "target"
    assert target_window.lowering.device_result_representation == "raw_trace"
    assert target_artifact.digitizer_programs[0].result_representation == "raw_trace"


def test_indeterminate_awg_program_load_stops_before_realtime() -> None:
    scheduled, _slot = _calibrated_acquisition()
    compiler, request = _request(_target(), (scheduled,), repetitions=1)
    artifact = compiler.compile(request)
    instruments = _IndeterminateInstrumentExecutor()

    with pytest.raises(RuntimeError, match="outcome is indeterminate"):
        InstrumentListModeRuntime().prepare(
            artifact,
            execution_id="test.indeterminate-load",
            instruments=instruments,
        )

    assert len(instruments.batches) == 1
    assert instruments.batches[0].operation_id.endswith(":load")
