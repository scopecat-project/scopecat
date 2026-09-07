"""Stable user-facing facts available before an experiment runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scopecat.inspection import (
    PLANNED_INSTRUMENT_SETTING_LIMIT,
    CompiledArtifactInspection,
    PlannedInstrumentSetting,
)
from scopecat.program.measurement_types import MeasurementVariableRole
from scopecat.records.measurement import MeasurementDatasetSchema


@dataclass(frozen=True)
class ExperimentPreviewPoint:
    point_index: int | None
    coordinates: dict[str, object]
    proposal_fingerprint: str | None = None
    source: Literal["author", "optimizer", "operator"] = "author"

    @property
    def is_planned(self) -> bool:
        return self.point_index is not None


@dataclass(frozen=True)
class ExperimentPreviewRecord:
    id: str
    role: MeasurementVariableRole
    recording_group_id: str | None
    unit: str | None
    dtype: str
    dims: tuple[str, ...]
    shape: tuple[int | None, ...]


@dataclass(frozen=True)
class ExperimentPreviewTransientProduct:
    """A demanded compute input whose product is not selected for retention."""

    id: str
    unit: str | None
    dtype: str
    dims: tuple[str, ...]
    shape: tuple[int | None, ...]


@dataclass(frozen=True)
class ExperimentPreviewPointGroup:
    """One sampled recovery group in preferred traversal order."""

    id: str
    key: dict[str, object]
    point_indices: tuple[int, ...]


@dataclass(frozen=True)
class ExperimentPreviewPointGrouping:
    """Named grouping policy and its resolved point partition."""

    id: str
    varying_coordinate_ids: tuple[str, ...]
    scheduling: Literal["prefer_together"]
    on_interruption: Literal["restart_group"]
    group_count: int
    groups: tuple[ExperimentPreviewPointGroup, ...]
    groups_truncated: bool


@dataclass(frozen=True)
class ExperimentPreviewPointSchedule:
    """Resolved composition of base traversal and optional grouping."""

    traversal: Literal["forward", "snake"]
    grouping: ExperimentPreviewPointGrouping | None


@dataclass(frozen=True)
class ExperimentPreviewCompute:
    """Why one live compute runs at its compiler-selected placement."""

    id: str
    placement: Literal["host", "observation"]
    implementation: str
    deterministic: bool
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    demanded_by: tuple[str, ...]
    captures: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExperimentPreviewBinding:
    """One user value classified by ownership rather than authoring syntax."""

    id: str
    kind: Literal["input", "coordinate", "parameter"]
    owner: Literal["invocation", "point-plan", "configuration"]
    origin: Literal["default", "override", "values", "range", "around"] | None


@dataclass(frozen=True)
class ExperimentPreviewBindingRef:
    """Typed identity of one value in the preview binding graph."""

    id: str
    kind: Literal["input", "coordinate", "parameter"]


@dataclass(frozen=True)
class ExperimentPreviewBindingEdge:
    """One parameter relationship without delimiter-encoded provenance."""

    source: ExperimentPreviewBindingRef
    target: ExperimentPreviewBindingRef
    relation: Literal["centers", "overlays"]


@dataclass(frozen=True)
class ExperimentPreviewDomainInspection:
    """One target-owned, non-durable inspection for the selected point."""

    operation_id: str
    point_index: int | None
    target_id: str
    artifact_id: str
    artifact_fingerprint: str
    content: CompiledArtifactInspection


@dataclass(frozen=True)
class ExperimentPreview:
    """Stable experiment shape that a user can review before execution."""

    experiment_id: str
    experiment_kind: str
    schema: MeasurementDatasetSchema | None
    coordinate_ids: tuple[str, ...]
    total_point_count: int | None
    initial_point_count: int
    point_limit: int
    points: tuple[ExperimentPreviewPoint, ...]
    points_truncated: bool
    records: tuple[ExperimentPreviewRecord, ...]
    sampled_point_limit: int = 64
    selected_point_limit: int = 1
    transient_products: tuple[ExperimentPreviewTransientProduct, ...] = ()
    point_schedule: ExperimentPreviewPointSchedule | None = None
    selected_point: ExperimentPreviewPoint | None = None
    domain_inspections: tuple[ExperimentPreviewDomainInspection, ...] = ()
    planned_settings: tuple[PlannedInstrumentSetting, ...] = ()
    planned_setting_limit: int = PLANNED_INSTRUMENT_SETTING_LIMIT
    planned_settings_truncated: bool = False
    computes: tuple[ExperimentPreviewCompute, ...] = ()
    bindings: tuple[ExperimentPreviewBinding, ...] = ()
    binding_edges: tuple[ExperimentPreviewBindingEdge, ...] = ()

    @property
    def point_count(self) -> int | None:
        return self.total_point_count

    @property
    def primary_observables(self) -> tuple[str, ...]:
        if self.schema is not None:
            return tuple(self.schema.primary_observables)
        return tuple(
            record.id for record in self.records if record.role == "observable"
        )

    @property
    def host_compute_ids(self) -> tuple[str, ...]:
        return tuple(
            compute.id for compute in self.computes if compute.placement == "host"
        )

    @property
    def observation_compute_ids(self) -> tuple[str, ...]:
        return tuple(
            compute.id
            for compute in self.computes
            if compute.placement == "observation"
        )
