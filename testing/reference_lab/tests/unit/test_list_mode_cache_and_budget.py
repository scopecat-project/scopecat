from __future__ import annotations

from ._list_mode_test_support import (
    ListModeCompilationCachePolicy,
    ListModeTargetCompiler,
    TargetCompilationError,
    TargetCompileEntryId,
    TargetCompilerId,
    _calibrated_acquisition,
    _request,
    _target,
    pytest,
    replace,
)


def test_list_mode_compilation_key_caches_and_explains_batch_capacity() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(
        _target(),
        max_list_entries=7,
        max_program_waveform_bytes=1024,
    )
    compiler, request = _request(target, (scheduled,), repetitions=2)

    artifact, cold_trace = compiler.compile_with_trace(request)

    assert cold_trace.artifact == "miss"
    assert cold_trace.semantic == "miss"
    assert cold_trace.placement == "miss"
    assert cold_trace.layout == "miss"
    assert not cold_trace.artifact_reused
    assert compiler.cache_info.artifact.hits == 0
    assert compiler.cache_info.artifact.misses == 1
    assert compiler.cache_info.semantic.misses == 1
    assert compiler.cache_info.placement.misses == 1
    assert compiler.cache_info.layout.misses == 1
    reused, warm_trace = compiler.compile_with_trace(request)
    assert reused is artifact
    assert warm_trace.artifact == "hit"
    assert warm_trace.semantic == "not_checked"
    assert warm_trace.placement == "not_checked"
    assert warm_trace.layout == "not_checked"
    assert warm_trace.artifact_reused
    assert compiler.cache_info.artifact.hits == 1
    assert compiler.cache_info.artifact.size == 1
    assert warm_trace.cache_info == compiler.cache_info
    assert cold_trace.cache_info.artifact.hits == 0
    assert all(
        seconds >= 0
        for seconds in (
            cold_trace.semantic_seconds,
            cold_trace.placement_seconds,
            cold_trace.layout_seconds,
            cold_trace.artifact_seconds,
        )
    )
    for stage in (
        compiler.cache_info.semantic,
        compiler.cache_info.placement,
        compiler.cache_info.layout,
        compiler.cache_info.artifact,
    ):
        assert 0 < stage.retained_bytes <= stage.max_retained_bytes
        assert stage.oversize_skips == 0
    same_artifact = ListModeTargetCompiler(compiler.id, target).compile(request)
    assert same_artifact.compilation_key == artifact.compilation_key
    assert same_artifact.artifact_fingerprint == artifact.artifact_fingerprint
    assert artifact.compilation_key.device_snapshot_fingerprint == (
        artifact.device_snapshot.snapshot_fingerprint
    )
    assert artifact.compilation_key.value.startswith("sha256:")

    budget = artifact.compilation_budget
    largest_entry_bytes = max(
        artifact.materialized_waveform_bytes(entry) for entry in artifact.entries
    )
    assert budget.dimension("list_entries").usage == 1
    assert budget.dimension("list_entries").projected_point_capacity == 7
    assert budget.dimension("waveform_memory_bytes").usage == (
        artifact.physical_footprint.waveform_bytes
    )
    assert budget.dimension("waveform_memory_bytes").projected_point_capacity == (
        target.max_program_waveform_bytes // largest_entry_bytes
    )
    assert budget.dimension("event_count").usage == len(scheduled.events)
    assert budget.dimension("acquisition_count").usage == 1
    assert budget.dimension("result_bytes").usage == 2 * 17
    assert budget.dimension("result_chunk_bytes").usage == 17
    assert budget.dimension("result_chunk_bytes").projected_shot_capacity == (
        target.max_result_chunk_bytes // 17
    )
    assert budget.dimension("samples_per_entry").usage == 12
    assert budget.dimension("repetitions").usage == 2
    assert budget.limiting_dimensions == ("waveform_memory_bytes",)
    assert budget.next_batch_max_points == 2

    changed, partial_trace = compiler.compile_with_trace(
        replace(request, repetitions=3)
    )
    assert partial_trace.artifact == "miss"
    assert partial_trace.semantic == "hit"
    assert partial_trace.placement == "hit"
    assert partial_trace.layout == "miss"
    assert changed.compilation_key.semantic_program_fingerprint == (
        artifact.compilation_key.semantic_program_fingerprint
    )
    assert changed.compilation_key.placement_fingerprint == (
        artifact.compilation_key.placement_fingerprint
    )
    assert changed.compilation_key.value != artifact.compilation_key.value
    assert compiler.cache_info.semantic.hits == 1
    assert compiler.cache_info.placement.hits == 1
    assert compiler.cache_info.layout.misses == 2

    renamed = compiler.compile(
        replace(
            request,
            entries=(replace(request.entries[0], id=TargetCompileEntryId("renamed")),),
        )
    )
    assert renamed.compilation_key.semantic_program_fingerprint == (
        artifact.compilation_key.semantic_program_fingerprint
    )
    assert renamed.compilation_key.placement_fingerprint == (
        artifact.compilation_key.placement_fingerprint
    )
    assert renamed.compilation_key.value != artifact.compilation_key.value
    assert compiler.cache_info.semantic.hits == 2
    assert compiler.cache_info.placement.hits == 2
    assert compiler.cache_info.layout.misses == 3


def test_list_mode_intermediate_cache_survives_artifact_eviction() -> None:
    scheduled, _slot = _calibrated_acquisition()
    compiler, request = _request(_target(), (scheduled,), repetitions=2)
    first = compiler.compile(request)

    for index in range(1, compiler.cache_info.artifact.capacity + 1):
        compiler.compile(
            replace(
                request,
                entries=(
                    replace(
                        request.entries[0],
                        id=TargetCompileEntryId(f"entry-{index}"),
                    ),
                ),
            )
        )

    before = compiler.cache_info
    assert before.artifact.evictions == 1
    assert before.layout.evictions == 0

    restored = compiler.compile(request)

    after = compiler.cache_info
    assert restored.artifact_fingerprint == first.artifact_fingerprint
    assert after.artifact.misses == before.artifact.misses + 1
    assert after.semantic.hits == before.semantic.hits + 1
    assert after.placement.hits == before.placement.hits + 1
    assert after.layout.hits == before.layout.hits + 1


def test_list_mode_artifact_cache_evicts_by_retained_bytes() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = _target()
    _default_compiler, request = _request(target, (scheduled,), repetitions=2)
    probe = ListModeTargetCompiler(TargetCompilerId("cache-probe.v1"), target)
    probe.compile(request)
    artifact_bytes = probe.cache_info.artifact.retained_bytes
    assert artifact_bytes > 1

    policy = replace(
        ListModeCompilationCachePolicy(),
        artifact_max_bytes=artifact_bytes,
    )
    compiler = ListModeTargetCompiler(
        TargetCompilerId("byte-bounded-cache.v1"),
        target,
        cache_policy=policy,
    )
    first = compiler.compile(request)
    renamed_request = replace(
        request,
        entries=(replace(request.entries[0], id=TargetCompileEntryId("renamed")),),
    )
    compiler.compile(renamed_request)

    bounded = compiler.cache_info.artifact
    assert bounded.capacity > bounded.size == 1
    assert bounded.retained_bytes == artifact_bytes
    assert bounded.evictions == 1
    assert compiler.compile(request).artifact_fingerprint == first.artifact_fingerprint
    assert compiler.cache_info.artifact.evictions == 2

    oversize = ListModeTargetCompiler(
        TargetCompilerId("oversize-cache.v1"),
        target,
        cache_policy=replace(policy, artifact_max_bytes=artifact_bytes - 1),
    )
    oversize.compile(request)
    oversize.compile(request)
    skipped = oversize.cache_info.artifact
    assert skipped.size == 0
    assert skipped.retained_bytes == 0
    assert skipped.misses == 2
    assert skipped.oversize_skips == 2


def test_list_mode_result_volume_can_limit_the_next_batch() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(
        _target(),
        max_list_entries=7,
        max_result_bytes=68,
    )
    compiler, request = _request(target, (scheduled,), repetitions=2)

    budget = compiler.compile(request).compilation_budget

    assert budget.dimension("result_bytes").projected_point_capacity == 2
    assert budget.next_batch_max_points == 2
    assert budget.limiting_dimensions == ("result_bytes",)


def test_list_mode_rejects_programs_larger_than_awg_memory() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(_target(), max_program_waveform_bytes=1)
    compiler, request = _request(target, (scheduled,), repetitions=1)

    with pytest.raises(TargetCompilationError) as caught:
        compiler.compile(request)

    assert {issue.code for issue in caught.value.issues} == {
        "list_mode_program_waveform_memory_exceeded"
    }


def test_list_mode_rejects_programs_with_too_many_scheduled_events() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(_target(), max_program_event_count=2)
    compiler, request = _request(target, (scheduled,), repetitions=1)

    with pytest.raises(TargetCompilationError) as caught:
        compiler.compile(request)

    assert {issue.code for issue in caught.value.issues} == {
        "list_mode_program_event_limit_exceeded"
    }


def test_list_mode_rejects_programs_with_too_many_acquisitions() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(_target(), max_program_acquisition_count=1)
    compiler, request = _request(target, (scheduled, scheduled), repetitions=1)

    with pytest.raises(TargetCompilationError) as caught:
        compiler.compile(request)

    assert {issue.code for issue in caught.value.issues} == {
        "list_mode_program_acquisition_limit_exceeded"
    }


def test_list_mode_rejects_result_volume_larger_than_memory() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(_target(), max_result_bytes=33)
    compiler, request = _request(target, (scheduled,), repetitions=2)

    with pytest.raises(TargetCompilationError) as caught:
        compiler.compile(request)

    assert {issue.code for issue in caught.value.issues} == {
        "list_mode_result_memory_exceeded"
    }


def test_list_mode_rejects_one_result_row_larger_than_a_chunk() -> None:
    scheduled, _slot = _calibrated_acquisition()
    target = replace(_target(), max_result_chunk_bytes=16)
    compiler, request = _request(target, (scheduled,), repetitions=1)

    with pytest.raises(TargetCompilationError) as caught:
        compiler.compile(request)

    assert {issue.code for issue in caught.value.issues} == {
        "list_mode_result_chunk_row_exceeded"
    }
