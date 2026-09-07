"""Bounded, host-visible inspection of compiled list-mode artifacts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import cast

import numpy as np
from numpy.typing import NDArray
from scopecat.inspection import (
    CompiledArtifactInspection,
    CompiledInspectionBounds,
    CompiledInspectionFact,
    CompiledPointInspection,
    CompiledProgramInspection,
    CompiledProgramInspectionInvertedIndexBuilder,
    CompiledProgramInspectionLayerIndex,
    CompiledProgramInspectionLink,
    CompiledProgramInspectionLinkIndex,
    CompiledProgramInspectionNode,
    CompiledProgramInspectionNodeIndex,
    CompiledProgramInspectionQuery,
    CompiledWaveformInspection,
    CompiledWorkEstimate,
)
from scopecat.kernel.content_identity import sha256_json_hash

from reference_lab.targets.list_mode.model import (
    AwgChannelWaveform,
    ListModeArtifact,
    ListModeCompilationTrace,
    ListModeEntry,
    ListModeEventPlacement,
    ListModePlacementCandidate,
    ListModePlacementConstraint,
    acquisition_slot_identity_payload,
    awg_waveform_identity_payload,
    pulse_event_identity_payload,
    signal_key,
)


@dataclass(frozen=True, slots=True)
class ArtifactInspectionBounds:
    """Hard response budgets for one transient artifact inspection."""

    max_entries: int = 1
    max_channels_per_entry: int = 12
    max_samples_per_waveform: int = 256
    max_placement_nodes: int = 128

    def __post_init__(self) -> None:
        if (
            self.max_entries <= 0
            or self.max_channels_per_entry <= 0
            or self.max_placement_nodes <= 0
        ):
            raise ValueError(
                "artifact inspection entry and channel limits must be positive"
            )
        if self.max_samples_per_waveform < 2:
            raise ValueError("waveform previews require at least two samples")


def _inspect_list_mode_artifact_base(
    artifact: ListModeArtifact,
    *,
    bounds: ArtifactInspectionBounds,
    compilation_trace: ListModeCompilationTrace | None = None,
) -> CompiledArtifactInspection:
    selected_bounds = bounds
    selected_entries = artifact.entries[: selected_bounds.max_entries]
    return CompiledArtifactInspection(
        kind="reference_lab.list_mode.v1",
        work_estimates=(
            CompiledWorkEstimate(
                "waveform_playback_time",
                timing := sum(entry.sample_count for entry in artifact.entries)
                / artifact.sample_rate_hz
                * artifact.repetitions,
                timing,
                "s",
                (
                    "Compiled artifact sample counts times repetitions / sample "
                    "rate; excludes trigger waits, transfer, queue and host "
                    "time"
                ),
            ),
            CompiledWorkEstimate(
                "waveform_bytes",
                artifact.physical_footprint.waveform_bytes,
                artifact.physical_footprint.waveform_bytes,
                "bytes",
                "Target waveform-buffer allocation for this compiled artifact",
            ),
            CompiledWorkEstimate(
                "result_bytes",
                artifact.physical_footprint.result_bytes,
                artifact.physical_footprint.result_bytes,
                "bytes",
                "Target result-buffer allocation, not retained dataset storage",
            ),
            CompiledWorkEstimate(
                "batch_point_capacity",
                0,
                artifact.compilation_budget.next_batch_max_points,
                "points",
                "Target next-batch point budget; not a run-wide batch count",
            ),
        ),
        facts=(
            CompiledInspectionFact("semantics_id", artifact.waveform_semantics_id),
            CompiledInspectionFact(
                "sample_rate_hz", artifact.sample_rate_hz, unit="Hz"
            ),
            CompiledInspectionFact("timing_quantization", artifact.timing_quantization),
            CompiledInspectionFact("repetitions", artifact.repetitions),
            CompiledInspectionFact(
                "device_snapshot_fingerprint",
                artifact.device_snapshot.snapshot_fingerprint,
            ),
            CompiledInspectionFact(
                "compilation_key",
                artifact.compilation_key.value,
            ),
            CompiledInspectionFact(
                "semantic_program_fingerprint",
                artifact.compilation_key.semantic_program_fingerprint,
            ),
            CompiledInspectionFact(
                "placement_fingerprint",
                artifact.compilation_key.placement_fingerprint,
            ),
            CompiledInspectionFact(
                "placement_provider_id",
                artifact.placement.provider_id,
            ),
            CompiledInspectionFact(
                "placement_provider_fingerprint",
                artifact.placement.provider_fingerprint,
            ),
            CompiledInspectionFact(
                "next_batch_max_points",
                artifact.compilation_budget.next_batch_max_points,
            ),
            CompiledInspectionFact(
                "limiting_budget_dimensions",
                list(artifact.compilation_budget.limiting_dimensions),
            ),
            CompiledInspectionFact(
                "logical_qubit_count",
                len(artifact.placement.logical_qubit_ids),
            ),
            CompiledInspectionFact(
                "physical_instrument_count",
                len(artifact.physical_footprint.instrument_ids),
            ),
            CompiledInspectionFact(
                "waveform_bytes",
                artifact.physical_footprint.waveform_bytes,
                unit="byte",
            ),
            CompiledInspectionFact(
                "result_bytes",
                artifact.physical_footprint.result_bytes,
                unit="byte",
            ),
            *(
                ()
                if compilation_trace is None
                else (
                    *(
                        fact
                        for stage_id, outcome, seconds, cache in (
                            (
                                "semantic",
                                compilation_trace.semantic,
                                compilation_trace.semantic_seconds,
                                compilation_trace.cache_info.semantic,
                            ),
                            (
                                "placement",
                                compilation_trace.placement,
                                compilation_trace.placement_seconds,
                                compilation_trace.cache_info.placement,
                            ),
                            (
                                "layout",
                                compilation_trace.layout,
                                compilation_trace.layout_seconds,
                                compilation_trace.cache_info.layout,
                            ),
                            (
                                "artifact",
                                compilation_trace.artifact,
                                compilation_trace.artifact_seconds,
                                compilation_trace.cache_info.artifact,
                            ),
                        )
                        for fact in (
                            CompiledInspectionFact(
                                f"compile_seconds.{stage_id}",
                                seconds,
                                unit="s",
                            ),
                            CompiledInspectionFact(
                                f"compile_cache.{stage_id}.outcome",
                                outcome,
                            ),
                            CompiledInspectionFact(
                                f"compile_cache.{stage_id}.retained_bytes",
                                cache.retained_bytes,
                                unit="byte",
                            ),
                            CompiledInspectionFact(
                                f"compile_cache.{stage_id}.max_retained_bytes",
                                cache.max_retained_bytes,
                                unit="byte",
                            ),
                            CompiledInspectionFact(
                                f"compile_cache.{stage_id}.oversize_skips",
                                cache.oversize_skips,
                            ),
                        )
                    ),
                )
            ),
            CompiledInspectionFact(
                "max_abs_boundary_error_seconds",
                str(
                    max(
                        (_max_boundary_error(entry) for entry in artifact.entries),
                        default=Decimal(0),
                    )
                ),
                unit="s",
            ),
        ),
        point_count=len(artifact.entries),
        points_truncated=len(selected_entries) < len(artifact.entries),
        bounds=CompiledInspectionBounds(
            max_points=selected_bounds.max_entries,
            max_waveforms_per_point=selected_bounds.max_channels_per_entry,
            max_samples_per_waveform=selected_bounds.max_samples_per_waveform,
        ),
        points=tuple(
            _inspect_entry(artifact, entry, bounds=selected_bounds)
            for entry in selected_entries
        ),
        program=None,
    )


@dataclass(frozen=True, slots=True)
class ListModeArtifactInspectionSnapshot:
    """Reusable waveform summary and physical placement index for one artifact."""

    base: CompiledArtifactInspection
    program_projector: Callable[
        [CompiledProgramInspectionQuery | None],
        CompiledProgramInspection,
    ]
    physical_layer: CompiledProgramInspectionLayerIndex
    physical_lineage: CompiledProgramInspectionLinkIndex
    max_placement_nodes: int

    def project(
        self,
        query: CompiledProgramInspectionQuery | None = None,
    ) -> CompiledArtifactInspection:
        program = self.program_projector(query)
        return replace(
            self.base,
            program=_with_physical_placement_layer(
                program,
                physical_layer=self.physical_layer,
                physical_lineage=self.physical_lineage,
                max_nodes=self.max_placement_nodes,
            ),
        )


def build_list_mode_artifact_inspection_snapshot(
    artifact: ListModeArtifact,
    *,
    program_projector: Callable[
        [CompiledProgramInspectionQuery | None],
        CompiledProgramInspection,
    ],
    bounds: ArtifactInspectionBounds | None = None,
    compilation_trace: ListModeCompilationTrace | None = None,
) -> ListModeArtifactInspectionSnapshot:
    """Build stable waveform and placement projections once per artifact."""

    selected_bounds = bounds or ArtifactInspectionBounds()
    physical_layer, placements = _physical_placement_layer_index(artifact)
    return ListModeArtifactInspectionSnapshot(
        base=_inspect_list_mode_artifact_base(
            artifact,
            bounds=selected_bounds,
            compilation_trace=compilation_trace,
        ),
        program_projector=program_projector,
        physical_layer=physical_layer,
        physical_lineage=_physical_lineage_index(
            placements,
            artifact.placement.candidates,
        ),
        max_placement_nodes=selected_bounds.max_placement_nodes,
    )


def _physical_placement_layer_index(
    artifact: ListModeArtifact,
) -> tuple[
    CompiledProgramInspectionLayerIndex,
    tuple[ListModeEventPlacement, ...],
]:
    entry_id = artifact.entries[0].entry_id
    placements = tuple(
        event for event in artifact.placement.events if event.entry_id == entry_id
    )
    logical_qubit_ids = tuple(sorted({event.signal.signal[2] for event in placements}))
    physical_resource_ids = tuple(
        sorted(
            {
                endpoint.id
                for candidate in artifact.placement.candidates
                for endpoint in candidate.route.endpoints
            }
        )
    )
    root_id = "physical:placement"
    placement_stop = 1 + len(placements)
    candidate_stop = placement_stop + len(artifact.placement.candidates)
    ordinal_by_id = {
        root_id: 0,
        **{
            f"physical:event:{event.event_id.value}": ordinal
            for ordinal, event in enumerate(placements, start=1)
        },
        **{
            f"physical:{candidate.id}": ordinal
            for ordinal, candidate in enumerate(
                artifact.placement.candidates,
                start=placement_stop,
            )
        },
        **{
            f"physical:constraint:{constraint.id}": ordinal
            for ordinal, constraint in enumerate(
                artifact.placement.constraints,
                start=candidate_stop,
            )
        },
    }
    inverted_index = CompiledProgramInspectionInvertedIndexBuilder()
    inverted_index.add(
        0,
        parent_id=None,
        kind="device",
        entity_ids=logical_qubit_ids,
        resource_ids=physical_resource_ids,
    )
    for ordinal, event in enumerate(placements, start=1):
        inverted_index.add(
            ordinal,
            parent_id=root_id,
            kind="placement",
            entity_ids=(event.signal.signal[2],),
            resource_ids=tuple(endpoint.id for endpoint in event.signal.endpoints),
        )
    for ordinal, candidate in enumerate(
        artifact.placement.candidates,
        start=placement_stop,
    ):
        inverted_index.add(
            ordinal,
            parent_id=root_id,
            kind=f"placement_candidate_{candidate.status}",
            entity_ids=tuple(sorted({candidate.signal[2], candidate.route.signal[2]})),
            resource_ids=tuple(endpoint.id for endpoint in candidate.route.endpoints),
        )
    for ordinal, constraint in enumerate(
        artifact.placement.constraints,
        start=candidate_stop,
    ):
        inverted_index.add(
            ordinal,
            parent_id=root_id,
            kind="placement_constraint",
            entity_ids=constraint.entity_ids,
            resource_ids=constraint.resource_ids,
        )

    def node_at(
        ordinal: int,
        query: CompiledProgramInspectionQuery | None,
    ) -> CompiledProgramInspectionNode:
        if ordinal == 0:
            preferred_entity = query.entity_id if query is not None else None
            entity_ids = _preferred_references(
                logical_qubit_ids,
                preferred_entity,
            )
            return CompiledProgramInspectionNode(
                id=root_id,
                kind="device",
                label=f"device {artifact.target_id.value}",
                child_count=(
                    len(placements)
                    + len(artifact.placement.candidates)
                    + len(artifact.placement.constraints)
                ),
                entity_ids=entity_ids,
                entity_count=len(logical_qubit_ids),
                entity_ids_truncated=len(entity_ids) < len(logical_qubit_ids),
                resource_ids=physical_resource_ids,
                facts=(
                    CompiledInspectionFact(
                        "snapshot_fingerprint",
                        artifact.device_snapshot.snapshot_fingerprint,
                    ),
                    CompiledInspectionFact(
                        "configured_signal_count",
                        len(artifact.device_snapshot.signal_placements),
                    ),
                    CompiledInspectionFact(
                        "placement_constraint_count",
                        len(artifact.placement.constraints),
                    ),
                    CompiledInspectionFact(
                        "placement_candidate_count",
                        artifact.placement.candidate_count,
                    ),
                    CompiledInspectionFact(
                        "materialized_candidate_count",
                        len(artifact.placement.candidates),
                    ),
                    CompiledInspectionFact(
                        "placement_candidates_truncated",
                        artifact.placement.candidates_truncated,
                    ),
                    CompiledInspectionFact(
                        "materialized_rejected_candidate_count",
                        sum(
                            candidate.status == "rejected"
                            for candidate in artifact.placement.candidates
                        ),
                    ),
                ),
            )
        if ordinal < placement_stop:
            return _physical_event_node(placements[ordinal - 1], parent_id=root_id)
        if ordinal < candidate_stop:
            return _physical_candidate_node(
                artifact.placement.candidates[ordinal - placement_stop],
                parent_id=root_id,
            )
        return _physical_constraint_node(
            artifact.placement.constraints[ordinal - candidate_stop],
            parent_id=root_id,
        )

    return (
        CompiledProgramInspectionLayerIndex(
            id="physical",
            label="Physical placement",
            kind="physical",
            root_ids=(root_id,),
            nodes=CompiledProgramInspectionNodeIndex(
                node_count=(
                    1
                    + len(placements)
                    + len(artifact.placement.candidates)
                    + len(artifact.placement.constraints)
                ),
                node_at=node_at,
                ordinal_by_id=ordinal_by_id.get,
                inverted_index=inverted_index.build(),
            ),
            facts=(
                CompiledInspectionFact(
                    "instrument_count",
                    len(artifact.physical_footprint.instrument_ids),
                ),
                CompiledInspectionFact(
                    "waveform_output_count",
                    len(artifact.physical_footprint.waveform_outputs),
                ),
                CompiledInspectionFact(
                    "acquisition_input_count",
                    len(artifact.physical_footprint.acquisition_inputs),
                ),
                *(
                    CompiledInspectionFact(
                        f"budget.{dimension.id}",
                        {
                            "scope": dimension.scope,
                            "usage": dimension.usage,
                            "limit": dimension.limit,
                            "projected_point_capacity": (
                                dimension.projected_point_capacity
                            ),
                            "projected_shot_capacity": (
                                dimension.projected_shot_capacity
                            ),
                        },
                    )
                    for dimension in artifact.compilation_budget.dimensions
                ),
            ),
        ),
        placements,
    )


def _preferred_references(
    values: tuple[str, ...],
    preferred: str | None,
    *,
    limit: int = 64,
) -> tuple[str, ...]:
    if preferred is None or preferred in values[:limit]:
        return values[:limit]
    if preferred not in values:
        return values[:limit]
    return (preferred, *values[: limit - 1])


def _physical_event_node(
    event: ListModeEventPlacement,
    *,
    parent_id: str,
) -> CompiledProgramInspectionNode:
    return CompiledProgramInspectionNode(
        id=f"physical:event:{event.event_id.value}",
        kind="placement",
        label=(
            f"{event.signal.signal[0]}({event.signal.signal[2]}) → "
            + ", ".join(endpoint.id for endpoint in event.signal.endpoints)
        ),
        parent_id=parent_id,
        entity_ids=(event.signal.signal[2],),
        resource_ids=tuple(endpoint.id for endpoint in event.signal.endpoints),
        facts=tuple(
            fact
            for fact in (
                CompiledInspectionFact("lo_group_id", event.signal.lo_group_id)
                if event.signal.lo_group_id is not None
                else None,
                CompiledInspectionFact(
                    "demodulator_slot_id",
                    event.signal.demodulator_slot_id,
                )
                if event.signal.demodulator_slot_id is not None
                else None,
                CompiledInspectionFact("constraint_ids", list(event.constraint_ids)),
                CompiledInspectionFact("candidate_ids", list(event.candidate_ids)),
                CompiledInspectionFact("candidate_count", event.candidate_count),
            )
            if fact is not None
        ),
    )


def _physical_candidate_node(
    candidate: ListModePlacementCandidate,
    *,
    parent_id: str,
) -> CompiledProgramInspectionNode:
    return CompiledProgramInspectionNode(
        id=f"physical:{candidate.id}",
        kind=f"placement_candidate_{candidate.status}",
        label=(
            f"{candidate.status} {candidate.signal[0]}({candidate.signal[2]}) → "
            + ", ".join(endpoint.id for endpoint in candidate.route.endpoints)
        ),
        parent_id=parent_id,
        entity_ids=tuple(sorted({candidate.signal[2], candidate.route.signal[2]})),
        resource_ids=tuple(endpoint.id for endpoint in candidate.route.endpoints),
        facts=(
            CompiledInspectionFact("status", candidate.status),
            CompiledInspectionFact("requested_signal", list(candidate.signal)),
            CompiledInspectionFact("configured_signal", list(candidate.route.signal)),
            CompiledInspectionFact(
                "rejection_codes",
                [rejection.code for rejection in candidate.rejections],
            ),
            CompiledInspectionFact(
                "rejection_reasons",
                [rejection.message for rejection in candidate.rejections],
            ),
        ),
        warnings=tuple(rejection.message for rejection in candidate.rejections),
    )


def _physical_constraint_node(
    constraint: ListModePlacementConstraint,
    *,
    parent_id: str,
) -> CompiledProgramInspectionNode:
    return CompiledProgramInspectionNode(
        id=f"physical:constraint:{constraint.id}",
        kind="placement_constraint",
        label=constraint.label,
        parent_id=parent_id,
        entity_ids=constraint.entity_ids,
        resource_ids=constraint.resource_ids,
        facts=(
            CompiledInspectionFact("constraint_kind", constraint.kind),
            CompiledInspectionFact("signal_count", len(constraint.signals)),
            CompiledInspectionFact(
                "signals",
                [list(signal) for signal in constraint.signals],
            ),
        ),
    )


def _with_physical_placement_layer(
    program: CompiledProgramInspection,
    *,
    physical_layer: CompiledProgramInspectionLayerIndex,
    physical_lineage: CompiledProgramInspectionLinkIndex,
    max_nodes: int,
) -> CompiledProgramInspection:
    layer, _selection = physical_layer.project(
        query=program.query,
        default_limit=max_nodes,
        snapshot_id=program.snapshot_id,
    )
    layers = (*program.layers, layer)
    lineage = physical_lineage.project(layers, max_links=max_nodes)
    return replace(
        program,
        layers=layers,
        links=(*program.links, *lineage.links),
        warnings=(
            *program.warnings,
            *(
                ("physical lineage links exceeded the inspection response budget",)
                if lineage.truncated
                else ()
            ),
        ),
    )


def _physical_lineage_index(
    placements: tuple[ListModeEventPlacement, ...],
    candidates: tuple[ListModePlacementCandidate, ...],
) -> CompiledProgramInspectionLinkIndex:
    candidate_by_id = {candidate.id: candidate for candidate in candidates}
    placement_links = [
        CompiledProgramInspectionLink(
            source_layer_id="scheduled",
            source_node_id=f"scheduled:event:{event.event_id.value}",
            target_layer_id="physical",
            target_node_id=f"physical:event:{event.event_id.value}",
            relation="placed_on",
        )
        for event in placements
    ]
    candidate_links = [
        CompiledProgramInspectionLink(
            source_layer_id="physical",
            source_node_id=f"physical:event:{event.event_id.value}",
            target_layer_id="physical",
            target_node_id=f"physical:{candidate.id}",
            relation=(
                "selected_route" if candidate.status == "selected" else "rejected_route"
            ),
        )
        for event in placements
        for candidate_id in event.candidate_ids
        if candidate_id in candidate_by_id
        for candidate in (candidate_by_id[candidate_id],)
    ]
    return CompiledProgramInspectionLinkIndex.from_links(
        (*placement_links, *candidate_links)
    )


def point_realization_fingerprint(
    artifact: ListModeArtifact,
    entry: ListModeEntry,
) -> str:
    """Identify one realization independently of target batch partitioning."""

    return _point_realization_fingerprint(
        artifact,
        entry,
        artifact.entry_waveforms(entry),
    )


def _point_realization_fingerprint(
    artifact: ListModeArtifact,
    entry: ListModeEntry,
    waveforms: tuple[AwgChannelWaveform, ...],
) -> str:
    return sha256_json_hash(
        {
            "schema": "reference_lab.list_mode_point_realization.v1",
            "target_id": artifact.target_id.value,
            "compiler_id": artifact.compiler_id.value,
            "capability_fingerprint": artifact.capability_fingerprint,
            "configuration_fingerprint": artifact.configuration_fingerprint,
            "repetitions": artifact.repetitions,
            "sample_rate_hz": artifact.sample_rate_hz,
            "waveform_semantics_id": artifact.waveform_semantics_id,
            "timing_quantization": artifact.timing_quantization,
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
                    "realized_duration_seconds": str(timing.realized_duration_seconds),
                    "start_error_seconds": str(timing.start_error_seconds),
                    "duration_error_seconds": str(timing.duration_error_seconds),
                }
                for timing in entry.event_timings
            ],
            "waveforms": [
                awg_waveform_identity_payload(waveform) for waveform in waveforms
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
                        "output_representation": window.intent.output_representation,
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
    )


def _inspect_entry(
    artifact: ListModeArtifact,
    entry: ListModeEntry,
    *,
    bounds: ArtifactInspectionBounds,
) -> CompiledPointInspection:
    waveforms = artifact.entry_waveforms(entry)
    selected_waveforms = waveforms[: bounds.max_channels_per_entry]
    return CompiledPointInspection(
        realization_fingerprint=_point_realization_fingerprint(
            artifact,
            entry,
            waveforms,
        ),
        target_entry_id=entry.entry_id.value,
        facts=(
            CompiledInspectionFact("list_index", entry.list_index),
            CompiledInspectionFact("program_id", entry.program_id.value),
            CompiledInspectionFact("sample_count", entry.sample_count),
            CompiledInspectionFact("event_count", len(entry.event_timings)),
            CompiledInspectionFact("acquisition_count", len(entry.acquisitions)),
            CompiledInspectionFact(
                "max_abs_boundary_error_seconds",
                str(_max_boundary_error(entry)),
                unit="s",
            ),
        ),
        waveform_count=len(waveforms),
        waveforms_truncated=len(selected_waveforms) < len(waveforms),
        waveforms=tuple(
            _preview_waveform(
                waveform,
                max_samples=bounds.max_samples_per_waveform,
            )
            for waveform in selected_waveforms
        ),
    )


def _max_boundary_error(entry: ListModeEntry) -> Decimal:
    return max(
        (
            max(
                abs(timing.start_error_seconds),
                abs(timing.start_error_seconds + timing.duration_error_seconds),
            )
            for timing in entry.event_timings
        ),
        default=Decimal(0),
    )


def _preview_waveform(
    waveform: AwgChannelWaveform,
    *,
    max_samples: int,
) -> CompiledWaveformInspection:
    samples = waveform.samples
    sample_indices = _minmax_indices(samples, max_samples=max_samples)
    selected_samples = tuple(
        float(cast("np.float64", samples[index])) for index in sample_indices
    )
    return CompiledWaveformInspection(
        channel_id=waveform.channel_id.value,
        instrument_id=waveform.channel_id.instrument_id,
        peak_abs=(
            float(cast("np.float64", np.max(np.abs(samples)))) if samples.size else 0.0
        ),
        rms=(
            float(cast("np.float64", np.sqrt(np.mean(np.square(samples)))))
            if samples.size
            else 0.0
        ),
        source_sample_count=int(samples.size),
        samples_sha256=waveform.samples_sha256,
        sample_indices=sample_indices,
        samples=selected_samples,
        downsampling="none" if samples.size <= max_samples else "minmax",
    )


def _minmax_indices(
    samples: NDArray[np.float64],
    *,
    max_samples: int,
) -> tuple[int, ...]:
    if samples.size <= max_samples:
        return tuple(range(samples.size))
    bucket_count = max_samples // 2
    selected: list[int] = []
    for bucket in range(bucket_count):
        start = bucket * samples.size // bucket_count
        end = (bucket + 1) * samples.size // bucket_count
        local = samples[start:end]
        minimum = start + int(np.argmin(local))
        maximum = start + int(np.argmax(local))
        selected.extend(sorted({minimum, maximum}))
    return tuple(selected)


__all__ = [
    "ArtifactInspectionBounds",
    "ListModeArtifactInspectionSnapshot",
    "build_list_mode_artifact_inspection_snapshot",
    "point_realization_fingerprint",
]
