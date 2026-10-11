"""Pure compiler for the demo list-mode AWG and segmented digitizer target.

This laboratory-owned compiler accepts target programs at the shared boundary
and translates the static scheduled-block subset into physical list entries and
acquisition windows. It rejects real-time control as a target capability choice,
then validates sample-grid alignment, signal bindings, overlap, list, and shot
limits before producing an artifact. Retained physical buffers are
amplitude-checked here; compact parameterized buffers are checked when they
materialize at the AWG boundary. No instrument effect occurs here.

Physical list and segment positions are artifact layout, never logical result
identity. Entry-qualified event and acquisition addresses survive compilation
so runtime evidence can be correlated to the exact prepared quantum work.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import cast

from scopecat.kernel.content_identity import content_fingerprint, sha256_json_hash
from scopecat_quantum._ids import (
    TargetArtifactId,
    TargetCompileEntryId,
    TargetCompilerId,
)
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    Delay,
    DriveSignal,
    Play,
    ReadoutSignal,
    ScheduledPulseProgram,
    ShiftPhase,
)
from scopecat_quantum.realtime import ScheduledBlock
from scopecat_quantum.targets import (
    TargetCompilationError,
    TargetCompilationIssue,
    TargetCompilationIssueDimension,
    TargetCompileEntry,
    TargetCompileRequest,
)
from scopecat_quantum.waveforms import (
    SAMPLED_WAVEFORM_SEMANTICS_ID,
    Float64ReferenceRenderer,
    IqMatrix,
    SampledOutputBinding,
    SampledWaveformPlan,
    SampleGrid,
    TimingQuantizationPolicy,
    WaveformPlanningError,
    factor_phase_parameterized_waveforms,
    plan_sampled_waveforms,
)

from reference_lab.targets.list_mode.iq_semantics import (
    INTEGRATED_IQ_SEMANTICS_ID,
)
from reference_lab.targets.list_mode.model import (
    AcquisitionBinding,
    AcquisitionIntent,
    AwgChannelId,
    AwgChannelWaveform,
    AwgPhaseTemplate,
    AwgPhaseTemplateUse,
    DemodulatorSlotId,
    DeviceAcquisitionLowering,
    DigitizerAcquisitionWindow,
    IqMixerCalibration,
    IqOutputBinding,
    ListModeArtifact,
    ListModeBudgetDimension,
    ListModeCompilationBudget,
    ListModeCompilationCacheInfo,
    ListModeCompilationCacheOutcome,
    ListModeCompilationCachePolicy,
    ListModeCompilationKey,
    ListModeCompilationStageCacheInfo,
    ListModeCompilationTrace,
    ListModeDeviceSnapshot,
    ListModeEntry,
    ListModeEventPlacement,
    ListModeHostStateRequirements,
    ListModePhysicalEndpoint,
    ListModePhysicalFootprint,
    ListModePreparation,
    ListModeProgramPlacement,
    ListModeSignalPlacement,
    ListModeTarget,
    OutputSignal,
    TargetAcquisitionLowering,
    acquisition_slot_identity_payload,
    awg_phase_template_identity_payload,
    awg_waveform_identity_payload,
    compilation_budget_payload,
    compilation_key_payload,
    device_snapshot_payload,
    host_state_requirements_payload,
    physical_footprint_payload,
    preparation_payload,
    program_placement_payload,
    pulse_event_identity_payload,
    signal_key,
)
from reference_lab.targets.list_mode.placement import (
    ConfiguredRoutePlacementProvider,
    ListModePlacementDecision,
    ListModePlacementError,
    ListModePlacementProvider,
)

_RESULT_BYTES_PER_VALUE = 17


def _compilation_trace(
    before: ListModeCompilationCacheInfo,
    after: ListModeCompilationCacheInfo,
    timings: _CompilationStageTimings,
) -> ListModeCompilationTrace:
    return ListModeCompilationTrace(
        semantic=_stage_cache_outcome(before.semantic, after.semantic),
        placement=_stage_cache_outcome(before.placement, after.placement),
        layout=_stage_cache_outcome(before.layout, after.layout),
        artifact=_stage_cache_outcome(before.artifact, after.artifact),
        semantic_seconds=timings.semantic,
        placement_seconds=timings.placement,
        layout_seconds=timings.layout,
        artifact_seconds=timings.artifact,
        cache_info=after,
    )


def _stage_cache_outcome(
    before: ListModeCompilationStageCacheInfo,
    after: ListModeCompilationStageCacheInfo,
) -> ListModeCompilationCacheOutcome:
    if after.hits == before.hits + 1:
        return "hit"
    if after.misses == before.misses + 1:
        return "miss"
    return "not_checked"


@dataclass(slots=True)
class _CompilationStageCache[ValueT]:
    capacity: int
    max_retained_bytes: int
    values: OrderedDict[str, tuple[ValueT, int]] = field(default_factory=OrderedDict)
    retained_bytes: int = 0
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    oversize_skips: int = 0

    def get(self, key: str) -> ValueT | None:
        retained = self.values.get(key)
        if retained is None:
            self.misses += 1
            return None
        self.hits += 1
        self.values.move_to_end(key)
        return retained[0]

    def put(self, key: str, value: ValueT, *, retained_bytes: int) -> None:
        previous = self.values.pop(key, None)
        if previous is not None:
            self.retained_bytes -= previous[1]
        if retained_bytes > self.max_retained_bytes:
            self.oversize_skips += 1
            return
        self.values[key] = (value, retained_bytes)
        self.retained_bytes += retained_bytes
        self.values.move_to_end(key)
        while (
            len(self.values) > self.capacity
            or self.retained_bytes > self.max_retained_bytes
        ):
            _evicted_key, (_evicted_value, evicted_bytes) = self.values.popitem(
                last=False
            )
            self.retained_bytes -= evicted_bytes
            self.evictions += 1

    @property
    def info(self) -> ListModeCompilationStageCacheInfo:
        return ListModeCompilationStageCacheInfo(
            hits=self.hits,
            misses=self.misses,
            evictions=self.evictions,
            size=len(self.values),
            capacity=self.capacity,
            retained_bytes=self.retained_bytes,
            max_retained_bytes=self.max_retained_bytes,
            oversize_skips=self.oversize_skips,
        )


@dataclass(slots=True)
class _CompilationStageTimings:
    semantic: float = 0.0
    placement: float = 0.0
    layout: float = 0.0
    artifact: float = 0.0


@dataclass(frozen=True, slots=True)
class _SemanticProgramPlan:
    event_count: int
    acquisition_count: int
    selected_signals: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class _PlannedProgram:
    waveform_plan: SampledWaveformPlan
    lane_channels: tuple[AwgChannelId, ...]
    active_lanes: tuple[int, ...]
    acquisitions: tuple[DigitizerAcquisitionWindow, ...]

    @property
    def sample_count(self) -> int:
        return self.waveform_plan.sample_count


@dataclass(frozen=True, slots=True)
class _PlacementPlan:
    programs: tuple[_PlannedProgram, ...]
    decision: ListModePlacementDecision


@dataclass(frozen=True, slots=True)
class _LayoutPlan:
    entries: tuple[ListModeEntry, ...]
    phase_templates: tuple[AwgPhaseTemplate, ...]
    waveform_bytes: int


@dataclass(frozen=True, slots=True)
class _EntryPlan:
    list_index: int
    source: TargetCompileEntry
    waveform_plan: SampledWaveformPlan
    lane_channels: tuple[AwgChannelId, ...]
    active_lanes: tuple[int, ...]
    acquisitions: tuple[DigitizerAcquisitionWindow, ...]

    @property
    def sample_count(self) -> int:
        return self.waveform_plan.sample_count


def _semantic_plan_retained_bytes(plan: _SemanticProgramPlan) -> int:
    return 128 + sum(
        32 + sum(len(part.encode()) for part in signal)
        for signal in plan.selected_signals
    )


def _placement_plan_retained_bytes(plan: _PlacementPlan) -> int:
    program_bytes = sum(
        256
        + 160 * len(program.waveform_plan.event_timings)
        + 384 * len(program.waveform_plan.render_events)
        + 192 * len(program.acquisitions)
        + 48 * len(program.lane_channels)
        for program in plan.programs
    )
    decision = plan.decision
    decision_bytes = (
        256
        + 256 * len(decision.placements)
        + 384 * len(decision.candidates)
        + 256 * len(decision.constraints)
    )
    return program_bytes + decision_bytes


def _layout_retained_bytes(layout: _LayoutPlan) -> int:
    return _entries_retained_bytes(layout.entries, layout.phase_templates)


def _entries_retained_bytes(
    entries: tuple[ListModeEntry, ...],
    phase_templates: tuple[AwgPhaseTemplate, ...],
) -> int:
    entry_bytes = sum(
        256
        + 128 * len(entry.acquisitions)
        + 128 * len(entry.event_timings)
        + 64 * len(entry.phase_template_uses)
        + sum(128 + waveform.samples.nbytes for waveform in entry.waveforms)
        for entry in entries
    )
    template_bytes = sum(
        256 + template.logical_i.nbytes + template.logical_q.nbytes
        for template in phase_templates
    )
    return entry_bytes + template_bytes


def _artifact_retained_bytes(artifact: ListModeArtifact) -> int:
    return (
        2048
        + _entries_retained_bytes(artifact.entries, artifact.phase_templates)
        + 256 * len(artifact.placement.events)
        + 384 * len(artifact.placement.candidates)
        + 256 * len(artifact.placement.constraints)
    )


@dataclass(frozen=True, slots=True)
class ListModeTargetCompiler:
    """Compile canonical pulse schedules into finite physical list payloads."""

    id: TargetCompilerId
    target: ListModeTarget
    placement_provider: ListModePlacementProvider = field(
        default_factory=ConfiguredRoutePlacementProvider,
        repr=False,
        compare=False,
    )
    cache_policy: ListModeCompilationCachePolicy = field(
        default_factory=ListModeCompilationCachePolicy,
        repr=False,
        compare=False,
    )
    _semantic_cache: _CompilationStageCache[_SemanticProgramPlan] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _placement_cache: _CompilationStageCache[_PlacementPlan] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _layout_cache: _CompilationStageCache[_LayoutPlan] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _artifact_cache: _CompilationStageCache[ListModeArtifact] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        policy = self.cache_policy
        object.__setattr__(
            self,
            "_semantic_cache",
            _CompilationStageCache(
                policy.semantic_max_entries,
                policy.semantic_max_bytes,
            ),
        )
        object.__setattr__(
            self,
            "_placement_cache",
            _CompilationStageCache(
                policy.placement_max_entries,
                policy.placement_max_bytes,
            ),
        )
        object.__setattr__(
            self,
            "_layout_cache",
            _CompilationStageCache(
                policy.layout_max_entries,
                policy.layout_max_bytes,
            ),
        )
        object.__setattr__(
            self,
            "_artifact_cache",
            _CompilationStageCache(
                policy.artifact_max_entries,
                policy.artifact_max_bytes,
            ),
        )

    @property
    def cache_info(self) -> ListModeCompilationCacheInfo:
        """Return deterministic counters for every process-local stage LRU."""

        return ListModeCompilationCacheInfo(
            semantic=self._semantic_cache.info,
            placement=self._placement_cache.info,
            layout=self._layout_cache.info,
            artifact=self._artifact_cache.info,
        )

    def compile(self, request: TargetCompileRequest) -> ListModeArtifact:
        """Compile one checked finite request without performing hardware effects."""

        artifact, _trace = self.compile_with_trace(request)
        return artifact

    def compile_with_trace(
        self,
        request: TargetCompileRequest,
    ) -> tuple[ListModeArtifact, ListModeCompilationTrace]:
        """Compile and report which process-local stages supplied the result."""

        before = self.cache_info
        timings = _CompilationStageTimings()
        artifact = self._compile(request, timings=timings)
        after = self.cache_info
        return artifact, _compilation_trace(before, after, timings)

    def _compile(
        self,
        request: TargetCompileRequest,
        *,
        timings: _CompilationStageTimings,
    ) -> ListModeArtifact:
        """Execute the layered compilation after trace counters are sampled."""

        _require_static_schedules(request)
        device_snapshot = self.target.device_snapshot
        compilation_key = _compilation_key(
            self.id,
            self.placement_provider.fingerprint,
            device_snapshot,
            request,
        )
        started = perf_counter()
        cached = self._artifact_cache.get(compilation_key.value)
        timings.artifact += perf_counter() - started
        if cached is not None:
            return cached

        started = perf_counter()
        semantic = self._semantic_cache.get(
            compilation_key.semantic_program_fingerprint
        )
        if semantic is None:
            semantic = _semantic_program_plan(request)
            self._semantic_cache.put(
                compilation_key.semantic_program_fingerprint,
                semantic,
                retained_bytes=_semantic_plan_retained_bytes(semantic),
            )
        timings.semantic += perf_counter() - started

        issues: list[TargetCompilationIssue] = []
        if len(request.entries) > self.target.max_list_entries:
            _issue(
                issues,
                dimension=TargetCompilationIssueDimension.CAPABILITY,
                code="list_mode_entry_limit_exceeded",
                message=(
                    f"request has {len(request.entries)} list entries; target limit is "
                    f"{self.target.max_list_entries}"
                ),
            )
        if request.repetitions > self.target.max_repetitions:
            _issue(
                issues,
                dimension=TargetCompilationIssueDimension.CAPABILITY,
                code="list_mode_repetition_limit_exceeded",
                message=(
                    f"request has {request.repetitions} repetitions; target limit is "
                    f"{self.target.max_repetitions}"
                ),
            )
        event_count = semantic.event_count
        acquisition_count = semantic.acquisition_count
        result_bytes = acquisition_count * request.repetitions * _RESULT_BYTES_PER_VALUE
        result_bytes_per_shot = acquisition_count * _RESULT_BYTES_PER_VALUE
        if event_count > self.target.max_program_event_count:
            _issue(
                issues,
                dimension=TargetCompilationIssueDimension.CAPABILITY,
                code="list_mode_program_event_limit_exceeded",
                message=(
                    f"request has {event_count} scheduled events; target limit is "
                    f"{self.target.max_program_event_count}"
                ),
            )
        if acquisition_count > self.target.max_program_acquisition_count:
            _issue(
                issues,
                dimension=TargetCompilationIssueDimension.CAPABILITY,
                code="list_mode_program_acquisition_limit_exceeded",
                message=(
                    f"request has {acquisition_count} acquisitions; target limit is "
                    f"{self.target.max_program_acquisition_count}"
                ),
            )
        if result_bytes > self.target.max_result_bytes:
            _issue(
                issues,
                dimension=TargetCompilationIssueDimension.CAPABILITY,
                code="list_mode_result_memory_exceeded",
                message=(
                    f"request produces {result_bytes} result bytes; target limit is "
                    f"{self.target.max_result_bytes}"
                ),
            )
        if result_bytes_per_shot > self.target.max_result_chunk_bytes:
            _issue(
                issues,
                dimension=TargetCompilationIssueDimension.CAPABILITY,
                code="list_mode_result_chunk_row_exceeded",
                message=(
                    f"one result shot requires {result_bytes_per_shot} bytes; "
                    f"target chunk limit is {self.target.max_result_chunk_bytes}"
                ),
            )

        started = perf_counter()
        placement_plan = self._placement_cache.get(
            compilation_key.placement_fingerprint
        )
        if placement_plan is None:
            try:
                decision = self.placement_provider.place(
                    semantic.selected_signals,
                    device_snapshot,
                )
            except ListModePlacementError as error:
                _unplaced_signal_issues(request, error, issues=issues)
                raise TargetCompilationError(tuple(issues)) from error
            planned_programs = tuple(
                plan
                for entry in request.entries
                if (
                    plan := self._plan_program(
                        entry,
                        decision=decision,
                        issues=issues,
                    )
                )
                is not None
            )
            if not issues:
                placement_plan = _PlacementPlan(
                    programs=planned_programs,
                    decision=decision,
                )
                self._placement_cache.put(
                    compilation_key.placement_fingerprint,
                    placement_plan,
                    retained_bytes=_placement_plan_retained_bytes(placement_plan),
                )
        timings.placement += perf_counter() - started
        if issues:
            raise TargetCompilationError(tuple(issues))
        assert placement_plan is not None
        plans = _bind_entry_plans(request, placement_plan.programs)

        started = perf_counter()
        layout = self._layout_cache.get(compilation_key.artifact_layout_fingerprint)
        if layout is None:
            compact = _compile_phase_template_sweep(plans)
            if compact is None:
                phase_templates: tuple[AwgPhaseTemplate, ...] = ()
                entries = tuple(
                    self._render_entry(plan, issues=issues) for plan in plans
                )
            else:
                phase_templates, entries = compact
            if issues:
                raise TargetCompilationError(tuple(issues))
            waveform_bytes = _materialized_waveform_bytes(entries, phase_templates)
            if waveform_bytes > self.target.max_program_waveform_bytes:
                _issue(
                    issues,
                    dimension=TargetCompilationIssueDimension.CAPABILITY,
                    code="list_mode_program_waveform_memory_exceeded",
                    message=(
                        f"request requires {waveform_bytes} waveform bytes; target "
                        "program-memory limit is "
                        f"{self.target.max_program_waveform_bytes}"
                    ),
                )
            if issues:
                raise TargetCompilationError(tuple(issues))
            layout = _LayoutPlan(
                entries=entries,
                phase_templates=phase_templates,
                waveform_bytes=waveform_bytes,
            )
            self._layout_cache.put(
                compilation_key.artifact_layout_fingerprint,
                layout,
                retained_bytes=_layout_retained_bytes(layout),
            )
        timings.layout += perf_counter() - started
        started = perf_counter()
        entries = layout.entries
        phase_templates = layout.phase_templates
        waveform_bytes = layout.waveform_bytes
        preparation = _project_preparation(self.target, entries, phase_templates)
        host_state_requirements = _project_host_state_requirements(
            self.target,
            entries,
            phase_templates,
        )
        placement = _project_program_placement(request, placement_plan)
        physical_footprint = _project_physical_footprint(
            entries,
            phase_templates,
            timing_instrument_id=preparation.timing.trigger_instrument_id,
            waveform_bytes=waveform_bytes,
            result_bytes=result_bytes,
        )
        compilation_budget = _project_compilation_budget(
            self.target,
            request,
            entries,
            phase_templates,
            waveform_bytes=waveform_bytes,
        )
        artifact_fingerprint = sha256_json_hash(
            _artifact_payload(
                compiler_id=self.id,
                target=self.target,
                request=request,
                compilation_key=compilation_key,
                compilation_budget=compilation_budget,
                device_snapshot=device_snapshot,
                placement=placement,
                physical_footprint=physical_footprint,
                preparation=preparation,
                host_state_requirements=host_state_requirements,
                entries=entries,
                phase_templates=phase_templates,
            )
        )
        digest = artifact_fingerprint.removeprefix("sha256:")
        artifact = ListModeArtifact(
            id=TargetArtifactId(f"list-mode-artifact-{digest}"),
            target_id=self.target.id,
            compiler_id=self.id,
            capability_fingerprint=self.target.capability_fingerprint,
            configuration_fingerprint=self.target.configuration_fingerprint,
            artifact_fingerprint=artifact_fingerprint,
            source_entry_ids=tuple(entry.id for entry in request.entries),
            repetitions=request.repetitions,
            sample_rate_hz=self.target.sample_rate_hz,
            waveform_semantics_id=SAMPLED_WAVEFORM_SEMANTICS_ID,
            max_abs_amplitude=self.target.max_abs_amplitude,
            max_result_chunk_bytes=self.target.max_result_chunk_bytes,
            timing_quantization=self.target.timing_quantization,
            compilation_key=compilation_key,
            compilation_budget=compilation_budget,
            device_snapshot=device_snapshot,
            placement=placement,
            physical_footprint=physical_footprint,
            preparation=preparation,
            host_state_requirements=host_state_requirements,
            entries=entries,
            phase_templates=phase_templates,
        )
        self._artifact_cache.put(
            compilation_key.value,
            artifact,
            retained_bytes=_artifact_retained_bytes(artifact),
        )
        timings.artifact += perf_counter() - started
        return artifact

    def _plan_program(
        self,
        entry: TargetCompileEntry,
        *,
        decision: ListModePlacementDecision,
        issues: list[TargetCompilationIssue],
    ) -> _PlannedProgram | None:
        program = _scheduled_program(entry)
        placements_by_signal = {
            placement.signal: placement for placement in decision.placements
        }
        lane_channels, sampled_bindings = _sampled_output_projection(
            self.target,
            program,
            placements_by_signal,
        )
        try:
            waveform_plan = plan_sampled_waveforms(
                program,
                bindings=sampled_bindings,
                grid=SampleGrid(
                    sample_rate_hz=self.target.sample_rate_hz,
                    timing=TimingQuantizationPolicy(
                        mode=self.target.timing_quantization
                    ),
                ),
            )
        except WaveformPlanningError as error:
            for planning_issue in error.issues:
                _entry_issue(
                    issues,
                    entry.id,
                    code=(f"list_mode_{planning_issue.code.removeprefix('sampled_')}"),
                    message=planning_issue.message,
                )
            return None

        if waveform_plan.sample_count > self.target.max_samples_per_entry:
            _entry_issue(
                issues,
                entry.id,
                code="list_mode_samples_per_entry_limit_exceeded",
                message=(
                    f"program {program.id.value!r} requires "
                    f"{waveform_plan.sample_count} "
                    f"samples; target entry limit is "
                    f"{self.target.max_samples_per_entry}"
                ),
            )

        acquisitions: list[DigitizerAcquisitionWindow] = []
        output_intervals: dict[AwgChannelId, list[tuple[int, int, str]]] = {}
        acquisition_intervals: dict[DemodulatorSlotId, list[tuple[int, int, str]]] = {}
        for event in program.events:
            timing = waveform_plan.timing_for(event.id)
            instruction = event.instruction
            match instruction:
                case Play():
                    signal = instruction.signal
                    if not isinstance(signal, DriveSignal | ReadoutSignal):
                        _unsupported_issue(issues, entry.id, event.id.value)
                        continue
                    if instruction.envelope.amplitude.unit not in {"arb", "ratio"}:
                        _entry_capability_issue(
                            issues,
                            entry.id,
                            code="list_mode_amplitude_unit_unsupported",
                            message=(
                                f"event {event.id.value!r} uses unsupported "
                                "amplitude unit "
                                f"{instruction.envelope.amplitude.unit!r}; "
                                "list-mode mode supports 'arb' and 'ratio'"
                            ),
                        )
                    binding = _output_binding_for_placement(
                        self.target,
                        placements_by_signal[signal_key(signal)],
                    )
                    if isinstance(signal, DriveSignal):
                        for channel_id in binding.channel_ids:
                            _claim_interval(
                                intervals=output_intervals,
                                channel_id=channel_id,
                                start_sample=timing.start_sample,
                                sample_count=timing.sample_count,
                                event_id=event.id.value,
                                entry_id=entry.id,
                                overlap_code=("list_mode_physical_output_overlap"),
                                resource_label="AWG channel",
                                issues=issues,
                            )
                case Acquire():
                    binding = _acquisition_binding_for_placement(
                        self.target,
                        placements_by_signal[signal_key(instruction.signal)],
                    )
                    if timing.sample_count > 0:
                        _claim_interval(
                            intervals=acquisition_intervals,
                            channel_id=binding.demodulator_slot_id,
                            start_sample=timing.start_sample,
                            sample_count=timing.sample_count,
                            event_id=event.id.value,
                            entry_id=entry.id,
                            overlap_code="list_mode_physical_acquisition_overlap",
                            resource_label="demodulator slot",
                            issues=issues,
                        )
                        acquisitions.append(
                            DigitizerAcquisitionWindow(
                                event_id=event.id,
                                slot_id=instruction.slot_id,
                                signal=instruction.signal,
                                input_id=binding.input_id,
                                demodulator_slot_id=binding.demodulator_slot_id,
                                intent=AcquisitionIntent(
                                    semantics_id=INTEGRATED_IQ_SEMANTICS_ID,
                                    output_representation="integrated_iq",
                                    demodulation_frequency_hz=(
                                        binding.demodulation_frequency_hz
                                    ),
                                    integration_weight="rectangular",
                                    normalization="single_sideband_amplitude",
                                ),
                                lowering=(
                                    TargetAcquisitionLowering()
                                    if self.target.digitizer_result_representation
                                    == "raw_trace"
                                    else DeviceAcquisitionLowering()
                                ),
                                start_sample=timing.start_sample,
                                sample_count=timing.sample_count,
                            )
                        )
                case Delay() | ShiftPhase():
                    pass

        active_lanes = tuple(
            sorted(
                {
                    lane
                    for render_event in waveform_plan.render_events
                    for lane in (
                        render_event.binding.i_lane,
                        render_event.binding.q_lane,
                    )
                }
            )
        )
        return _PlannedProgram(
            waveform_plan=waveform_plan,
            lane_channels=lane_channels,
            active_lanes=active_lanes,
            acquisitions=tuple(acquisitions),
        )

    def _render_entry(
        self,
        plan: _EntryPlan,
        *,
        issues: list[TargetCompilationIssue],
    ) -> ListModeEntry:
        rendered = Float64ReferenceRenderer().render(plan.waveform_plan)
        for lane in plan.active_lanes:
            peak_magnitude = rendered.lane_peaks[lane]
            if peak_magnitude > self.target.max_abs_amplitude:
                _entry_issue(
                    issues,
                    plan.source.id,
                    code="list_mode_amplitude_limit_exceeded",
                    message=(
                        f"final waveform on channel "
                        f"{plan.lane_channels[lane].value!r} has magnitude "
                        f"{peak_magnitude!r}; target limit is "
                        f"{self.target.max_abs_amplitude!r}"
                    ),
                )
        return ListModeEntry(
            list_index=plan.list_index,
            entry_id=plan.source.id,
            program_id=plan.source.program.id,
            sample_count=plan.sample_count,
            waveforms=tuple(
                AwgChannelWaveform(
                    channel_id=plan.lane_channels[lane],
                    samples=rendered.buffers[lane],
                )
                for lane in plan.active_lanes
            ),
            acquisitions=plan.acquisitions,
            event_timings=plan.waveform_plan.event_timings,
        )


def _compile_phase_template_sweep(
    plans: tuple[_EntryPlan, ...],
) -> tuple[tuple[AwgPhaseTemplate, ...], tuple[ListModeEntry, ...]] | None:
    parameterized = factor_phase_parameterized_waveforms(
        tuple(plan.waveform_plan for plan in plans)
    )
    if parameterized is None:
        return None
    reference = plans[0]
    renderer = Float64ReferenceRenderer()
    templates: list[AwgPhaseTemplate] = []
    for index, event in enumerate(parameterized.template.render_events):
        isolated = renderer.render(
            replace(parameterized.template, render_events=(event,))
        )
        selected = slice(event.timing.start_sample, event.timing.end_sample)
        physical_i = isolated.buffers[event.binding.i_lane][selected]
        physical_q = isolated.buffers[event.binding.q_lane][selected]
        mixer = event.binding.mixer
        determinant = mixer.ii * mixer.qq - mixer.iq * mixer.qi
        if math.isclose(determinant, 0.0, abs_tol=1e-15):
            return None
        templates.append(
            AwgPhaseTemplate(
                id=f"event-{index}",
                i_channel_id=reference.lane_channels[event.binding.i_lane],
                q_channel_id=reference.lane_channels[event.binding.q_lane],
                start_sample=event.timing.start_sample,
                sample_count=event.timing.sample_count,
                logical_i=(mixer.qq * physical_i - mixer.iq * physical_q) / determinant,
                logical_q=(-mixer.qi * physical_i + mixer.ii * physical_q)
                / determinant,
                mixer=IqMixerCalibration(
                    ii=mixer.ii,
                    iq=mixer.iq,
                    qi=mixer.qi,
                    qq=mixer.qq,
                    i_offset_v=0.0,
                    q_offset_v=0.0,
                ),
            )
        )
    entries = tuple(
        ListModeEntry(
            list_index=plan.list_index,
            entry_id=plan.source.id,
            program_id=plan.source.program.id,
            sample_count=plan.sample_count,
            waveforms=(),
            acquisitions=plan.acquisitions,
            event_timings=plan.waveform_plan.event_timings,
            phase_template_uses=tuple(
                AwgPhaseTemplateUse(
                    template_id=template.id,
                    phase_radians=phase,
                )
                for template, phase in zip(
                    templates,
                    phase_row,
                    strict=True,
                )
            ),
        )
        for plan, phase_row in zip(
            plans,
            parameterized.phase_rows,
            strict=True,
        )
    )
    return tuple(templates), entries


def _materialized_waveform_bytes(
    entries: tuple[ListModeEntry, ...],
    phase_templates: tuple[AwgPhaseTemplate, ...],
) -> int:
    if not phase_templates:
        return sum(
            waveform.samples.nbytes for entry in entries for waveform in entry.waveforms
        )
    template_by_id = {template.id: template for template in phase_templates}
    return sum(
        entry.sample_count
        * 8
        * len(
            {
                channel_id
                for use in entry.phase_template_uses
                for channel_id in template_by_id[use.template_id].channel_ids
            }
        )
        for entry in entries
    )


def _compilation_key(
    compiler_id: TargetCompilerId,
    placement_provider_fingerprint: str,
    snapshot: ListModeDeviceSnapshot,
    request: TargetCompileRequest,
) -> ListModeCompilationKey:
    scheduled_program_fingerprints = tuple(
        sha256_json_hash(content_fingerprint(entry.program))
        for entry in request.entries
    )
    semantic_program_fingerprint = sha256_json_hash(
        {
            "schema": "reference_lab.list_mode_semantic_program.v1",
            "scheduled_program_fingerprints": scheduled_program_fingerprints,
        }
    )
    placement_fingerprint = sha256_json_hash(
        {
            "schema": "reference_lab.list_mode_placement_key.v2",
            "semantic_program_fingerprint": semantic_program_fingerprint,
            "device_snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "placement_provider_fingerprint": placement_provider_fingerprint,
        }
    )
    artifact_layout_fingerprint = sha256_json_hash(
        {
            "schema": "reference_lab.list_mode_artifact_layout_key.v1",
            "compiler_id": compiler_id.value,
            "placement_fingerprint": placement_fingerprint,
            "source_entry_ids": [entry.id.value for entry in request.entries],
            "repetitions": request.repetitions,
            "waveform_semantics_id": SAMPLED_WAVEFORM_SEMANTICS_ID,
        }
    )
    return ListModeCompilationKey(
        compiler_id=compiler_id,
        placement_provider_fingerprint=placement_provider_fingerprint,
        device_snapshot_fingerprint=snapshot.snapshot_fingerprint,
        scheduled_program_fingerprints=scheduled_program_fingerprints,
        semantic_program_fingerprint=semantic_program_fingerprint,
        placement_fingerprint=placement_fingerprint,
        artifact_layout_fingerprint=artifact_layout_fingerprint,
    )


def _require_static_schedules(request: TargetCompileRequest) -> None:
    issues: list[TargetCompilationIssue] = []
    for entry in request.entries:
        if isinstance(entry.program.body, ScheduledBlock):
            continue
        _entry_capability_issue(
            issues,
            entry.id,
            code="list_mode_realtime_control_unsupported",
            message=(
                f"entry {entry.id.value!r} contains target-visible real-time "
                "control; this list-mode target accepts one static scheduled block"
            ),
        )
    if issues:
        raise TargetCompilationError(tuple(issues))


def _scheduled_program(entry: TargetCompileEntry) -> ScheduledPulseProgram:
    body = entry.program.body
    if not isinstance(body, ScheduledBlock):
        raise AssertionError("list-mode real-time capability preflight was skipped")
    return body.program


def _semantic_program_plan(request: TargetCompileRequest) -> _SemanticProgramPlan:
    return _SemanticProgramPlan(
        event_count=sum(
            len(_scheduled_program(entry).events) for entry in request.entries
        ),
        acquisition_count=len(request.acquisition_addresses),
        selected_signals=tuple(
            sorted(
                {
                    signal_key(event.instruction.signal)
                    for entry in request.entries
                    for event in _scheduled_program(entry).events
                    if isinstance(
                        event.instruction.signal,
                        DriveSignal | ReadoutSignal | AcquireSignal,
                    )
                }
            )
        ),
    )


def _bind_entry_plans(
    request: TargetCompileRequest,
    programs: tuple[_PlannedProgram, ...],
) -> tuple[_EntryPlan, ...]:
    if len(programs) != len(request.entries):
        raise AssertionError("placement programs must match target entries")
    return tuple(
        _EntryPlan(
            list_index=list_index,
            source=entry,
            waveform_plan=program.waveform_plan,
            lane_channels=program.lane_channels,
            active_lanes=program.active_lanes,
            acquisitions=program.acquisitions,
        )
        for list_index, (entry, program) in enumerate(
            zip(request.entries, programs, strict=True)
        )
    )


def _unplaced_signal_issues(
    request: TargetCompileRequest,
    error: ListModePlacementError,
    *,
    issues: list[TargetCompilationIssue],
) -> None:
    missing_signals = set(error.missing_signals)
    for entry in request.entries:
        entry_signals = {
            signal_key(event.instruction.signal)
            for event in _scheduled_program(entry).events
            if isinstance(
                event.instruction.signal,
                DriveSignal | ReadoutSignal | AcquireSignal,
            )
        }
        for signal in sorted(missing_signals & entry_signals):
            role = signal[0]
            _entry_issue(
                issues,
                entry.id,
                code=(
                    "list_mode_acquisition_signal_unbound"
                    if role == "acquire"
                    else "list_mode_output_signal_unbound"
                ),
                message=(f"{role} signal {'/'.join(signal)} has no physical placement"),
            )


def _project_compilation_budget(
    target: ListModeTarget,
    request: TargetCompileRequest,
    entries: tuple[ListModeEntry, ...],
    phase_templates: tuple[AwgPhaseTemplate, ...],
    *,
    waveform_bytes: int,
) -> ListModeCompilationBudget:
    template_by_id = {template.id: template for template in phase_templates}

    def entry_waveform_bytes(entry: ListModeEntry) -> int:
        if not entry.phase_template_uses:
            return sum(waveform.samples.nbytes for waveform in entry.waveforms)
        return (
            entry.sample_count
            * 8
            * len(
                {
                    channel_id
                    for use in entry.phase_template_uses
                    for channel_id in template_by_id[use.template_id].channel_ids
                }
            )
        )

    largest_entry_bytes = max(entry_waveform_bytes(entry) for entry in entries)
    largest_entry_events = max(len(entry.event_timings) for entry in entries)
    largest_entry_acquisitions = max(len(entry.acquisitions) for entry in entries)
    result_bytes = (
        sum(len(entry.acquisitions) for entry in entries)
        * request.repetitions
        * _RESULT_BYTES_PER_VALUE
    )
    result_bytes_per_shot = (
        sum(len(entry.acquisitions) for entry in entries) * _RESULT_BYTES_PER_VALUE
    )
    largest_entry_result_bytes = (
        largest_entry_acquisitions * request.repetitions * _RESULT_BYTES_PER_VALUE
    )
    largest_entry_result_bytes_per_shot = (
        largest_entry_acquisitions * _RESULT_BYTES_PER_VALUE
    )
    point_capacities = {
        "list_entries": target.max_list_entries,
        "waveform_memory_bytes": max(
            1,
            target.max_program_waveform_bytes // max(largest_entry_bytes, 1),
        ),
        "event_count": max(
            1,
            target.max_program_event_count // max(largest_entry_events, 1),
        ),
        "acquisition_count": max(
            1,
            target.max_program_acquisition_count // max(largest_entry_acquisitions, 1),
        ),
        "result_bytes": max(
            1,
            target.max_result_bytes // max(largest_entry_result_bytes, 1),
        ),
        "result_chunk_bytes": max(
            1,
            target.max_result_chunk_bytes
            // max(largest_entry_result_bytes_per_shot, 1),
        ),
    }
    next_batch_max_points = min(point_capacities.values())
    dimensions = (
        ListModeBudgetDimension(
            id="list_entries",
            scope="batch",
            usage=len(entries),
            limit=target.max_list_entries,
            projected_point_capacity=point_capacities["list_entries"],
        ),
        ListModeBudgetDimension(
            id="waveform_memory_bytes",
            scope="batch",
            usage=waveform_bytes,
            limit=target.max_program_waveform_bytes,
            projected_point_capacity=point_capacities["waveform_memory_bytes"],
        ),
        ListModeBudgetDimension(
            id="event_count",
            scope="batch",
            usage=sum(len(entry.event_timings) for entry in entries),
            limit=target.max_program_event_count,
            projected_point_capacity=point_capacities["event_count"],
        ),
        ListModeBudgetDimension(
            id="acquisition_count",
            scope="batch",
            usage=sum(len(entry.acquisitions) for entry in entries),
            limit=target.max_program_acquisition_count,
            projected_point_capacity=point_capacities["acquisition_count"],
        ),
        ListModeBudgetDimension(
            id="result_bytes",
            scope="invocation",
            usage=result_bytes,
            limit=target.max_result_bytes,
            projected_point_capacity=point_capacities["result_bytes"],
        ),
        ListModeBudgetDimension(
            id="result_chunk_bytes",
            scope="invocation",
            usage=result_bytes_per_shot,
            limit=target.max_result_chunk_bytes,
            projected_point_capacity=point_capacities["result_chunk_bytes"],
            projected_shot_capacity=max(
                1,
                target.max_result_chunk_bytes // max(result_bytes_per_shot, 1),
            ),
        ),
        ListModeBudgetDimension(
            id="samples_per_entry",
            scope="entry",
            usage=max(entry.sample_count for entry in entries),
            limit=target.max_samples_per_entry,
        ),
        ListModeBudgetDimension(
            id="repetitions",
            scope="invocation",
            usage=request.repetitions,
            limit=target.max_repetitions,
        ),
    )
    return ListModeCompilationBudget(
        dimensions=dimensions,
        next_batch_max_points=next_batch_max_points,
        limiting_dimensions=tuple(
            dimension_id
            for dimension_id, capacity in point_capacities.items()
            if capacity == next_batch_max_points
        ),
    )


def _project_program_placement(
    request: TargetCompileRequest,
    plan: _PlacementPlan,
) -> ListModeProgramPlacement:
    decision = plan.decision
    placements_by_signal = {
        placement.signal: placement for placement in decision.placements
    }
    constraint_ids_by_signal = dict(decision.constraint_ids_by_signal)
    candidate_ids_by_signal = dict(decision.candidate_ids_by_signal)
    candidate_counts_by_signal = dict(decision.candidate_counts_by_signal)
    events: list[ListModeEventPlacement] = []
    for entry in request.entries:
        for event in _scheduled_program(entry).events:
            placement = placements_by_signal[
                signal_key(
                    cast("OutputSignal | AcquireSignal", event.instruction.signal)
                )
            ]
            events.append(
                ListModeEventPlacement(
                    entry_id=entry.id,
                    event_id=event.id,
                    signal=placement,
                    constraint_ids=tuple(constraint_ids_by_signal[placement.signal]),
                    candidate_ids=tuple(candidate_ids_by_signal[placement.signal]),
                    candidate_count=candidate_counts_by_signal[placement.signal],
                )
            )
    return ListModeProgramPlacement(
        provider_id=decision.provider_id,
        provider_fingerprint=decision.provider_fingerprint,
        device_snapshot_fingerprint=decision.device_snapshot_fingerprint,
        events=tuple(events),
        candidates=decision.candidates,
        candidate_count=decision.candidate_count,
        candidates_truncated=(len(decision.candidates) < decision.candidate_count),
        constraints=decision.constraints,
    )


def _project_physical_footprint(
    entries: tuple[ListModeEntry, ...],
    phase_templates: tuple[AwgPhaseTemplate, ...],
    *,
    timing_instrument_id: str,
    waveform_bytes: int,
    result_bytes: int,
) -> ListModePhysicalFootprint:
    output_channels = {
        waveform.channel_id for entry in entries for waveform in entry.waveforms
    } | {channel for template in phase_templates for channel in template.channel_ids}
    acquisition_inputs = {
        window.input_id for entry in entries for window in entry.acquisitions
    }
    waveform_outputs = tuple(
        ListModePhysicalEndpoint(
            kind="waveform_output",
            instrument_id=channel.instrument_id,
            channel_id=channel.value,
            component_path=channel.component_path,
        )
        for channel in sorted(output_channels)
    )
    selected_acquisition_inputs = tuple(
        ListModePhysicalEndpoint(
            kind="acquisition_input",
            instrument_id=input_id.instrument_id,
            channel_id=input_id.value,
            component_path=input_id.component_path,
        )
        for input_id in sorted(acquisition_inputs)
    )
    instrument_ids = tuple(
        sorted(
            {
                *(endpoint.instrument_id for endpoint in waveform_outputs),
                *(endpoint.instrument_id for endpoint in selected_acquisition_inputs),
                timing_instrument_id,
            }
        )
    )
    return ListModePhysicalFootprint(
        instrument_ids=instrument_ids,
        waveform_outputs=waveform_outputs,
        acquisition_inputs=selected_acquisition_inputs,
        timing_instrument_id=timing_instrument_id,
        waveform_bytes=waveform_bytes,
        result_bytes=result_bytes,
        event_count=sum(len(entry.event_timings) for entry in entries),
        acquisition_count=sum(len(entry.acquisitions) for entry in entries),
    )


def _sampled_output_projection(
    target: ListModeTarget,
    program: ScheduledPulseProgram,
    placements_by_signal: dict[tuple[str, str, str], ListModeSignalPlacement],
) -> tuple[tuple[AwgChannelId, ...], tuple[SampledOutputBinding, ...]]:
    signals = tuple(
        sorted(
            {
                instruction.signal
                for event in program.events
                if isinstance(instruction := event.instruction, Play)
                and isinstance(instruction.signal, DriveSignal | ReadoutSignal)
            },
            key=signal_key,
        )
    )
    selected_bindings = tuple(
        (
            signal,
            _output_binding_for_placement(
                target,
                placements_by_signal[signal_key(signal)],
            ),
        )
        for signal in signals
    )
    lane_channels = tuple(
        sorted(
            {
                channel_id
                for _, binding in selected_bindings
                for channel_id in binding.channel_ids
            }
        )
    )
    lane_by_channel = {
        channel_id: lane for lane, channel_id in enumerate(lane_channels)
    }
    return lane_channels, tuple(
        SampledOutputBinding(
            signal=signal,
            i_lane=lane_by_channel[binding.i_channel_id],
            q_lane=lane_by_channel[binding.q_channel_id],
            intermediate_frequency_hz=binding.intermediate_frequency_hz,
            mixer=IqMatrix(
                ii=binding.mixer.ii,
                iq=binding.mixer.iq,
                qi=binding.mixer.qi,
                qq=binding.mixer.qq,
            ),
        )
        for signal, binding in selected_bindings
    )


def _output_binding_for_placement(
    target: ListModeTarget,
    placement: ListModeSignalPlacement,
) -> IqOutputBinding:
    for binding in target.output_bindings:
        if (
            placement.lo_group_id == binding.lo_group_id
            and placement.endpoints
            == tuple(
                ListModePhysicalEndpoint(
                    kind="waveform_output",
                    instrument_id=channel.instrument_id,
                    channel_id=channel.value,
                    component_path=channel.component_path,
                )
                for channel in binding.channel_ids
            )
        ):
            return binding
    raise ValueError(
        f"placement for {'/'.join(placement.signal)} has no configured "
        "output calibration"
    )


def _acquisition_binding_for_placement(
    target: ListModeTarget,
    placement: ListModeSignalPlacement,
) -> AcquisitionBinding:
    for binding in target.acquisition_bindings:
        if (
            placement.demodulator_slot_id == binding.demodulator_slot_id.value
            and placement.endpoints
            == (
                ListModePhysicalEndpoint(
                    kind="acquisition_input",
                    instrument_id=binding.input_id.instrument_id,
                    channel_id=binding.input_id.value,
                    component_path=binding.input_id.component_path,
                ),
            )
        ):
            return binding
    raise ValueError(
        "placement for "
        f"{'/'.join(placement.signal)} has no configured acquisition calibration"
    )


def _claim_interval[ChannelIdT: AwgChannelId | DemodulatorSlotId](
    *,
    intervals: dict[ChannelIdT, list[tuple[int, int, str]]],
    channel_id: ChannelIdT,
    start_sample: int,
    sample_count: int,
    event_id: str,
    entry_id: TargetCompileEntryId,
    overlap_code: str,
    resource_label: str,
    issues: list[TargetCompilationIssue],
) -> None:
    end_sample = start_sample + sample_count
    selected = intervals.setdefault(channel_id, [])
    for other_start, other_end, other_event_id in selected:
        if start_sample < other_end and other_start < end_sample:
            _entry_issue(
                issues,
                entry_id,
                code=overlap_code,
                message=(
                    f"events {other_event_id!r} and {event_id!r} overlap on "
                    f"{resource_label} {channel_id.value!r}"
                ),
            )
    selected.append((start_sample, end_sample, event_id))


def _entry_issue(
    issues: list[TargetCompilationIssue],
    entry_id: TargetCompileEntryId,
    *,
    code: str,
    message: str,
) -> None:
    _issue(
        issues,
        dimension=TargetCompilationIssueDimension.PROGRAM,
        code=code,
        message=message,
        entry_id=entry_id,
    )


def _entry_capability_issue(
    issues: list[TargetCompilationIssue],
    entry_id: TargetCompileEntryId,
    *,
    code: str,
    message: str,
) -> None:
    _issue(
        issues,
        dimension=TargetCompilationIssueDimension.CAPABILITY,
        code=code,
        message=message,
        entry_id=entry_id,
    )


def _unsupported_issue(
    issues: list[TargetCompilationIssue],
    entry_id: TargetCompileEntryId,
    event_id: str,
) -> None:
    _entry_capability_issue(
        issues,
        entry_id,
        code="list_mode_operation_unsupported",
        message=f"event {event_id!r} is unsupported by list mode",
    )


def _issue(
    issues: list[TargetCompilationIssue],
    *,
    dimension: TargetCompilationIssueDimension,
    code: str,
    message: str,
    entry_id: TargetCompileEntryId | None = None,
) -> None:
    issues.append(
        TargetCompilationIssue(
            dimension=dimension,
            code=code,
            message=message,
            entry_id=entry_id,
        )
    )


def _project_preparation(
    target: ListModeTarget,
    entries: tuple[ListModeEntry, ...],
    phase_templates: tuple[AwgPhaseTemplate, ...],
) -> ListModePreparation:
    """Select only shared device state needed by the rendered entries."""

    channel_ids = {
        waveform.channel_id for entry in entries for waveform in entry.waveforms
    } | {
        channel_id
        for template in phase_templates
        for channel_id in template.channel_ids
    }
    awg_instrument_ids = {channel_id.instrument_id for channel_id in channel_ids}
    return ListModePreparation(
        clocks=tuple(
            clock
            for clock in target.preparation.clocks
            if clock.instrument_id in awg_instrument_ids
        ),
        outputs=tuple(
            output
            for output in target.preparation.outputs
            if output.channel_id in channel_ids
        ),
        timing=target.preparation.timing,
    )


def _project_host_state_requirements(
    target: ListModeTarget,
    entries: tuple[ListModeEntry, ...],
    phase_templates: tuple[AwgPhaseTemplate, ...],
) -> ListModeHostStateRequirements:
    """Expand active channels through the configured physical coupling groups."""

    active_channel_ids = {
        waveform.channel_id for entry in entries for waveform in entry.waveforms
    } | {
        channel_id
        for template in phase_templates
        for channel_id in template.channel_ids
    }
    selected_groups = tuple(
        group
        for group in target.host_state_policy.coupling_groups
        if active_channel_ids & set(group.activation_channels)
    )
    return ListModeHostStateRequirements(
        policy_id=target.host_state_policy.id,
        coupling_group_ids=tuple(group.id for group in selected_groups),
        output_offsets=tuple(
            requirement
            for group in selected_groups
            for requirement in group.output_offsets
        ),
    )


def _artifact_payload(
    *,
    compiler_id: TargetCompilerId,
    target: ListModeTarget,
    request: TargetCompileRequest,
    compilation_key: ListModeCompilationKey,
    compilation_budget: ListModeCompilationBudget,
    device_snapshot: ListModeDeviceSnapshot,
    placement: ListModeProgramPlacement,
    physical_footprint: ListModePhysicalFootprint,
    preparation: ListModePreparation,
    host_state_requirements: ListModeHostStateRequirements,
    entries: tuple[ListModeEntry, ...],
    phase_templates: tuple[AwgPhaseTemplate, ...],
) -> dict[str, object]:
    return {
        "schema": "reference_lab.list_mode_artifact.v13",
        "target": {
            "id": target.id.value,
            "capability_fingerprint": target.capability_fingerprint,
            "configuration_fingerprint": target.configuration_fingerprint,
        },
        "compiler_id": compiler_id.value,
        "repetitions": request.repetitions,
        "sample_rate_hz": target.sample_rate_hz,
        "waveform_semantics_id": SAMPLED_WAVEFORM_SEMANTICS_ID,
        "max_result_chunk_bytes": target.max_result_chunk_bytes,
        "timing_quantization": target.timing_quantization,
        "source_entry_ids": [entry.id.value for entry in request.entries],
        "compilation_key": compilation_key_payload(compilation_key),
        "compilation_budget": compilation_budget_payload(compilation_budget),
        "device_snapshot": device_snapshot_payload(device_snapshot),
        "placement": program_placement_payload(placement),
        "physical_footprint": physical_footprint_payload(physical_footprint),
        "preparation": preparation_payload(preparation),
        "host_state_requirements": host_state_requirements_payload(
            host_state_requirements
        ),
        "phase_templates": [
            awg_phase_template_identity_payload(template)
            for template in phase_templates
        ],
        "entries": [
            {
                "list_index": entry.list_index,
                "entry_id": entry.entry_id.value,
                "program_id": entry.program_id.value,
                "sample_count": entry.sample_count,
                "event_timings": [
                    {
                        "event_id": pulse_event_identity_payload(timing.event_id),
                        "requested_start_seconds": str(timing.requested_start_seconds),
                        "requested_duration_seconds": str(
                            timing.requested_duration_seconds
                        ),
                        "start_sample": timing.start_sample,
                        "sample_count": timing.sample_count,
                        "realized_start_seconds": str(timing.realized_start_seconds),
                        "realized_duration_seconds": str(
                            timing.realized_duration_seconds
                        ),
                        "start_error_seconds": str(timing.start_error_seconds),
                        "duration_error_seconds": str(timing.duration_error_seconds),
                    }
                    for timing in entry.event_timings
                ],
                "waveforms": [
                    awg_waveform_identity_payload(waveform)
                    for waveform in entry.waveforms
                ],
                "phase_template_uses": [
                    {
                        "template_id": use.template_id,
                        "phase_radians": float(use.phase_radians).hex(),
                    }
                    for use in entry.phase_template_uses
                ],
                "acquisitions": [
                    {
                        "event_id": pulse_event_identity_payload(window.event_id),
                        "slot_id": acquisition_slot_identity_payload(window.slot_id),
                        "signal": signal_key(window.signal),
                        "input_id": window.input_id.value,
                        "instrument_id": window.input_id.instrument_id,
                        "component_path": list(window.input_id.component_path),
                        "demodulator_slot_id": window.demodulator_slot_id.value,
                        "intent": {
                            "semantics_id": window.intent.semantics_id,
                            "output_representation": (
                                window.intent.output_representation
                            ),
                            "demodulation_frequency_hz": float(
                                window.intent.demodulation_frequency_hz
                            ).hex(),
                            "integration_weight": window.intent.integration_weight,
                            "normalization": window.intent.normalization,
                        },
                        "lowering": {
                            "execution": window.lowering.execution,
                            "device_result_representation": (
                                window.lowering.device_result_representation
                            ),
                        },
                        "start_sample": window.start_sample,
                        "sample_count": window.sample_count,
                    }
                    for window in entry.acquisitions
                ],
            }
            for entry in entries
        ],
    }


__all__ = ["ListModeTargetCompiler"]
