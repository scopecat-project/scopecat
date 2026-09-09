"""Read models returned to GUI and Python clients."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.config.registry.records import (
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
)
from scopecat.control.models import (
    AdaptiveRegionSpec,
    ControlRunState,
    PointCoordinateSpec,
    PointCoordinateValue,
    ResourceOwnerKind,
    RunResourceRequirement,
)
from scopecat.daemon.health import DaemonHealth
from scopecat.daemon.points import RunPointPlanView
from scopecat.kernel.entity import EntityRef, entity_identity
from scopecat.kernel.problems import Problem
from scopecat.measurements.datasets import (
    MAX_MEASUREMENT_PAGE_SIZE,
    MAX_MEASUREMENT_SLICE_SIZE,
    MAX_MEASUREMENT_TRACE_SAMPLES,
    MAX_MEASUREMENT_TRACE_SERIES,
)
from scopecat.measurements.entity_selection import MeasurementEntitySelection
from scopecat.measurements.traces import (
    TraceDownsampling,
    TraceLayout,
    TraceValueMode,
)
from scopecat.records.analysis import AnalysisRecord
from scopecat.records.config import (
    ConfigContentHash,
    ConfigProfileSnapshot,
    config_content_hash,
)
from scopecat.records.config_context import ConfigValueOrigin, ContextRunConfigSource
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementDatasetSchema,
    MeasurementEntityAcquisition,
    MeasurementRecord,
    MeasurementUnavailableReason,
)
from scopecat.records.parameter_change import (
    ParameterChangeApprovalRecord,
    ParameterChangeProposal,
    ParameterValueDelta,
)
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleId, SampleRecord, SampleRevision
from scopecat.sdk.instruments.contracts import InstrumentDescription


class _ViewModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class ConfigRegistryPage(_ViewModel):
    """Newest-first page of saved revisions and the current activation head."""

    entries: tuple[ConfigRegistryEntry, ...] = ()
    activation: ConfigRegistryActivationRecord | None = None
    next_cursor: int | None = Field(default=None, ge=1)


class ConfigActivationPage(_ViewModel):
    """Newest-first page of default configuration changes."""

    items: tuple[ConfigRegistryActivationRecord, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class ActiveConfigView(_ViewModel):
    """The active registry identity and its resolved immutable snapshot."""

    entry: ConfigRegistryEntry
    activation: ConfigRegistryActivationRecord
    config: ConfigProfileSnapshot


class ConfigEntryView(_ViewModel):
    """One immutable configuration and its most recent activation, if any."""

    entry: ConfigRegistryEntry
    config: ConfigProfileSnapshot
    latest_activation: ConfigRegistryActivationRecord | None = None


class ConfigDraftPreview(_ViewModel):
    """Normalized result of transient typed edits against an active config."""

    valid: bool
    base_entry: ConfigRegistryEntry
    base_generation: int
    base_content_hash: ConfigContentHash
    config: ConfigProfileSnapshot | None = None
    result_content_hash: ConfigContentHash | None = None
    deltas: tuple[ParameterValueDelta, ...] = ()
    problems: tuple[Problem, ...] = ()


class SampleSummary(_ViewModel):
    """One sample's active revision and bounded run-history aggregates."""

    record: SampleRecord
    revision: SampleRevision
    run_count: int = Field(default=0, ge=0)
    last_run_at: datetime | None = None


class SamplePage(_ViewModel):
    """Newest-first bounded sample registry page."""

    items: tuple[SampleSummary, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class SampleRevisionPage(_ViewModel):
    """Newest-first page of immutable revisions for one sample."""

    sample_id: SampleId
    items: tuple[SampleRevision, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class SampleView(SampleSummary):
    """Sample detail for the active immutable revision."""


class VirtualInstrumentConnectionSummary(_ViewModel):
    kind: Literal["virtual"] = "virtual"


class DriverManagedInstrumentConnectionSummary(_ViewModel):
    kind: Literal["driver_managed"] = "driver_managed"


class TcpipSocketInstrumentConnectionSummary(_ViewModel):
    kind: Literal["tcpip_socket"] = "tcpip_socket"
    host: str
    port: int


class SerialInstrumentConnectionSummary(_ViewModel):
    kind: Literal["serial"] = "serial"
    port: str
    baud_rate: int


type InstrumentConnectionSummary = Annotated[
    (
        VirtualInstrumentConnectionSummary
        | DriverManagedInstrumentConnectionSummary
        | TcpipSocketInstrumentConnectionSummary
        | SerialInstrumentConnectionSummary
    ),
    Field(discriminator="kind"),
]


class InstrumentView(_ViewModel):
    """Instrument status without exposing configuration policy or driver options."""

    instrument_id: str
    driver_id: str
    connection: InstrumentConnectionSummary
    description: InstrumentDescription | None = None
    availability: Literal["available", "active", "quarantined", "unavailable"]
    owner_kind: ResourceOwnerKind | None = None
    owner_id: str | None = None
    owner_actor: str | None = None
    problems: tuple[Problem, ...] = ()

    @model_validator(mode="after")
    def validate_availability(self) -> InstrumentView:
        if self.availability == "available":
            if (
                self.owner_kind is not None
                or self.owner_id is not None
                or self.owner_actor is not None
            ):
                raise ValueError("available instrument cannot have an owner")
        elif self.availability in {"active", "quarantined"}:
            if self.owner_kind is None or self.owner_id is None:
                raise ValueError("owned instrument requires an owner")
            if self.owner_kind == "instrument_session" and self.owner_actor is None:
                raise ValueError("interactive instrument owner requires an actor")
            if self.owner_kind == "run" and self.owner_actor is not None:
                raise ValueError("run-owned instrument cannot expose an actor")
        return self


class InstrumentListView(_ViewModel):
    config_entry_id: str
    items: tuple[InstrumentView, ...] = ()
    problems: tuple[Problem, ...] = ()


class RunPlanView(_ViewModel):
    """Experiment facts without scheduler or backend identities."""

    experiment_id: str = Field(min_length=1)
    experiment_kind: str = Field(min_length=1)
    point_plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    measurement_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    point_count: int | None = Field(default=None, ge=0)
    initial_point_count: int = Field(ge=0)
    point_limit: int = Field(ge=0)
    adaptive_coordinate_ids: tuple[str, ...] = ()
    adaptive_scope: Literal["per_region", "global"] | None = None
    per_region_point_limit: int | None = Field(default=None, ge=1)
    adaptive_region_count: int = Field(default=0, ge=0)
    adaptive_regions: tuple[AdaptiveRegionSpec, ...] = ()
    adaptive_regions_truncated: bool = False
    coordinates: tuple[PointCoordinateSpec, ...] = ()
    sampled_points: tuple[dict[str, PointCoordinateValue], ...] = ()
    sampled_points_truncated: bool = False
    record_ids: tuple[str, ...] = ()
    run_resource_requirements: tuple[RunResourceRequirement, ...] = ()

    @property
    def coordinate_ids(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self.coordinates)


class RunAdmissionView(_ViewModel):
    run_id: str = Field(min_length=1)
    run_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan: RunPlanView
    display_name: str | None = Field(default=None, min_length=1)
    tags: tuple[str, ...] = ()
    description: str | None = Field(default=None, min_length=1)
    admitted_at: datetime


class RunControlView(_ViewModel):
    """Run state without durable scheduler internals."""

    sequence: int = Field(ge=1)
    admission: RunAdmissionView
    state: ControlRunState
    updated_at: datetime
    attention_reason: str | None = None
    cancellation_requested_at: datetime | None = None
    completed_point_count: int = Field(ge=0)
    point_plan: RunPointPlanView

    @property
    def run_id(self) -> str:
        return self.admission.run_id


class RunResourceBlocker(_ViewModel):
    """Current competing owner, without authority or canonical resource keys."""

    owner_kind: ResourceOwnerKind
    owner_id: str
    status: Literal["active", "quarantined"]


class RunResourceView(_ViewModel):
    """Logical resource state and competing owner, without execution authority."""

    resource: RunResourceRequirement
    status: Literal["required", "blocked", "active", "quarantined", "released"]
    expires_at: datetime | None = None
    blocked_by: RunResourceBlocker | None = None


class RunSummary(_ViewModel):
    """Scheduler state paired with the durable run snapshot."""

    control: RunControlView
    snapshot: RunSnapshot

    @property
    def run_id(self) -> str:
        return self.control.run_id


class RunSummaryPage(_ViewModel):
    """Keyset page retaining scheduler state and terminal outcomes."""

    items: tuple[RunSummary, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class RunDetail(RunSummary):
    """Run summary with scheduler resource state."""

    resources: tuple[RunResourceView, ...] = ()


class WorkerDiagnosticLink(_ViewModel):
    generation: str
    request_id: int | None = None
    operation: str | None = None
    instrument_id: str | None = None
    retention: Literal["retained", "unavailable_active_quota"]
    href: str | None = None


class RunFailureEvidence(_ViewModel):
    primary: Problem | None = None
    secondary: tuple[Problem, ...] = ()
    diagnostics: tuple[WorkerDiagnosticLink, ...] = ()
    terminal_persistence: Literal["confirmed", "unconfirmed"]
    truncated: bool = False


class RunConfigView(_ViewModel):
    """The immutable configuration snapshot accepted with one run."""

    run_id: str
    config_content_hash: ConfigContentHash
    config: ConfigProfileSnapshot

    @model_validator(mode="after")
    def validate_identity(self) -> RunConfigView:
        if config_content_hash(self.config) != self.config_content_hash:
            raise ValueError("run config view content hash is inconsistent")
        return self


class RunRequestView(_ViewModel):
    """The operator request accepted with one run."""

    run_id: str
    request: RunRequest


class RunAnalysisView(_ViewModel):
    """One persisted run analysis and its catalog identity."""

    run_id: str
    entry: ContentEntry
    analysis: AnalysisRecord
    published_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> RunAnalysisView:
        if (
            self.entry.role != "record"
            or self.entry.kind != "analysis"
            or self.analysis.subject.kind != "run"
            or self.analysis.subject.run_id != self.run_id
        ):
            raise ValueError("run analysis view identity is inconsistent")
        return self


class RunAnalysisSummary(_ViewModel):
    """Bounded list projection for one run-owned analysis publication."""

    run_id: str
    entry: ContentEntry
    title: str
    key: str | None = None
    revision: int = Field(ge=1)
    publication_hash: str
    published_at: datetime
    step_id: str | None = None
    input_count: int = Field(ge=0)
    output_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_identity(self) -> RunAnalysisSummary:
        if self.entry.role != "record" or self.entry.kind != "analysis":
            raise ValueError("run analysis summary identity is inconsistent")
        return self


class RunAnalysisPage(_ViewModel):
    """Newest-first keyset page of run analysis summaries."""

    run_id: str
    items: tuple[RunAnalysisSummary, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_identity(self) -> RunAnalysisPage:
        if any(item.run_id != self.run_id for item in self.items):
            raise ValueError("run analysis page contains a different run")
        return self


class RunContentPage(_ViewModel):
    """Newest-first keyset page from one run's content catalog."""

    run_id: str
    items: tuple[ContentEntry, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class ProjectAnalysisView(_ViewModel):
    """One project-level analysis record."""

    entry: ContentEntry
    analysis: AnalysisRecord
    published_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> ProjectAnalysisView:
        if (
            self.entry.role != "record"
            or self.entry.kind != "analysis"
            or self.analysis.subject.kind != "project"
        ):
            raise ValueError("project analysis view identity is inconsistent")
        return self


class ProjectAnalysisSummary(_ViewModel):
    """Bounded list projection for one project-level analysis publication."""

    entry: ContentEntry
    title: str
    key: str
    revision: int = Field(ge=1)
    publication_hash: str
    published_at: datetime
    step_id: str | None = None
    input_count: int = Field(ge=0)
    output_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_identity(self) -> ProjectAnalysisSummary:
        if self.entry.role != "record" or self.entry.kind != "analysis":
            raise ValueError("project analysis summary identity is inconsistent")
        return self


class ProjectAnalysisPage(_ViewModel):
    """Newest-first keyset page of project analysis summaries."""

    items: tuple[ProjectAnalysisSummary, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class SampleAnalysisView(_ViewModel):
    """One longitudinal analysis record scoped to a stable sample."""

    sample_id: SampleId
    entry: ContentEntry
    analysis: AnalysisRecord
    published_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> SampleAnalysisView:
        if (
            self.entry.role != "record"
            or self.entry.kind != "analysis"
            or self.analysis.subject.kind != "sample"
            or self.analysis.subject.sample_id != self.sample_id
        ):
            raise ValueError("sample analysis view identity is inconsistent")
        return self


class SampleAnalysisSummary(ProjectAnalysisSummary):
    """Bounded list projection for one sample analysis publication."""

    sample_id: SampleId


class SampleAnalysisPage(_ViewModel):
    """Newest-first keyset page for one sample's analyses."""

    sample_id: SampleId
    items: tuple[SampleAnalysisSummary, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class ProjectAnalysisContentPage(_ViewModel):
    """Newest-first keyset page of project-analysis-owned content."""

    analysis_id: str
    items: tuple[ContentEntry, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class AnalysisContentBytesView(_ViewModel):
    """Exact bytes for one project analysis-owned content entry."""

    analysis_id: str
    entry: ContentEntry
    content_base64: str

    @model_validator(mode="after")
    def validate_content(self) -> AnalysisContentBytesView:
        owns_record = (
            self.entry.role == "record"
            and self.entry.kind == "analysis"
            and self.entry.id == self.analysis_id
        )
        if not owns_record and self.entry.produced_by != self.analysis_id:
            raise ValueError("analysis content producer is inconsistent")
        _validate_base64(self.content_base64)
        return self

    def content_bytes(self) -> bytes:
        return b64decode(self.content_base64, validate=True)


class RunArtifactBytesView(_ViewModel):
    run_id: str
    artifact: ContentEntry
    content_base64: str

    @model_validator(mode="after")
    def validate_content(self) -> RunArtifactBytesView:
        _require_entry_role(self.artifact, "artifact")
        _validate_base64(self.content_base64)
        return self

    def content_bytes(self) -> bytes:
        return b64decode(self.content_base64, validate=True)


class RunDatasetBytesView(_ViewModel):
    run_id: str
    dataset: ContentEntry
    content_base64: str

    @model_validator(mode="after")
    def validate_content(self) -> RunDatasetBytesView:
        _require_entry_role(self.dataset, "dataset")
        _validate_base64(self.content_base64)
        return self

    def content_bytes(self) -> bytes:
        return b64decode(self.content_base64, validate=True)


class ParameterProposalView(_ViewModel):
    """One proposal and its optional immutable operator approval."""

    proposal: ParameterChangeProposal
    approval: ParameterChangeApprovalRecord | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> ParameterProposalView:
        if self.approval is not None and (
            self.approval.run_id != self.proposal.source_run_id
            or self.approval.proposal_id != self.proposal.id
        ):
            raise ValueError("parameter proposal approval identity is inconsistent")
        return self


class ParameterProposalPage(_ViewModel):
    run_id: str
    items: tuple[ParameterProposalView, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_identity(self) -> ParameterProposalPage:
        if any(item.proposal.source_run_id != self.run_id for item in self.items):
            raise ValueError("parameter proposal page contains a different run")
        return self


class MeasurementArrowColumn(_ViewModel):
    """One external Arrow column bound to a durable measurement variable."""

    name: Annotated[str, Field(min_length=1)]
    variable_id: Annotated[str, Field(min_length=1)]


class MeasurementArrowQuery(_ViewModel):
    """Atomic projection and finite page requested from the Arrow read path."""

    columns: tuple[MeasurementArrowColumn, ...] = Field(min_length=1)
    entity_selection: MeasurementEntitySelection | None = None
    units: dict[str, Annotated[str, Field(min_length=1)]] = Field(default_factory=dict)
    diagnostics: Literal["none", "reason", "full"] = "reason"
    include_identity: bool = True
    layout: Literal["points", "observations"] = "points"
    limit: Annotated[int, Field(ge=1, le=MAX_MEASUREMENT_PAGE_SIZE)] = 100
    offset: Annotated[int, Field(ge=0)] = 0
    snapshot_size: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> MeasurementArrowQuery:
        if (
            self.entity_selection is not None
            and len(self.entity_selection.entities) > MAX_MEASUREMENT_TRACE_SERIES
        ):
            raise ValueError(
                "measurement Arrow entity selection supports at most "
                f"{MAX_MEASUREMENT_TRACE_SERIES} entities per query"
            )
        names = tuple(column.name for column in self.columns)
        if len(names) != len(set(names)):
            raise ValueError("measurement Arrow column names must be unique")
        unknown_units = set(self.units) - set(names)
        if unknown_units:
            raise ValueError(
                "measurement Arrow units reference unknown columns: "
                + ", ".join(sorted(unknown_units))
            )
        return self


class MeasurementPreview(_ViewModel):
    """One bounded JSON preview for the operator UI, not a data paging API."""

    items: tuple[MeasurementRecord, ...] = ()
    dataset_schema: MeasurementDatasetSchema | None = None
    truncated: bool = False


class MeasurementLivePreview(_ViewModel):
    """Latest daemon-received measurement, whether or not it is durable yet."""

    active: bool = False
    latest: MeasurementRecord | None = None
    received_record_count: int = Field(default=0, ge=0)
    durable_record_count: int = Field(default=0, ge=0)


class MeasurementSliceQuery(_ViewModel):
    """One bounded product-grid slice selected by authored axis indices."""

    fixed_axis_indices: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict
    )
    limit: Annotated[int, Field(ge=1, le=MAX_MEASUREMENT_SLICE_SIZE)] = (
        MAX_MEASUREMENT_SLICE_SIZE
    )
    offset: Annotated[int, Field(ge=0)] = 0
    variable_ids: list[Annotated[str, Field(min_length=1)]] | None = None
    include_schema: bool = False

    @model_validator(mode="after")
    def validate_axis_ids(self) -> MeasurementSliceQuery:
        if any(not axis_id for axis_id in self.fixed_axis_indices):
            raise ValueError("measurement slice axis ids must be non-empty")
        return self


class MeasurementSlice(_ViewModel):
    """One logical-point window within a semantic product-grid slice."""

    items: tuple[MeasurementRecord, ...] = ()
    dataset_schema: MeasurementDatasetSchema | None = None
    selected_point_count: Annotated[int, Field(ge=0)]
    offset: Annotated[int, Field(ge=0)] = 0
    window_point_count: Annotated[int, Field(ge=0)] = 0
    next_offset: Annotated[int, Field(ge=1)] | None = None
    previous_offset: Annotated[int, Field(ge=0)] | None = None
    truncated: bool = False


class MeasurementTracePreviewQuery(_ViewModel):
    """Select one bounded, response-ready point/entity-local trace preview."""

    recording_group_id: Annotated[str, Field(min_length=1)] | None = None
    observable_id: Annotated[str, Field(min_length=1)] | None = None
    coordinate_id: Annotated[str, Field(min_length=1)] | None = None
    fixed_axis_indices: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict
    )
    entities: tuple[EntityRef, ...] | None = Field(
        default=None, min_length=1, max_length=MAX_MEASUREMENT_TRACE_SERIES
    )
    entity_indices: tuple[Annotated[int, Field(ge=0)], ...] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_MEASUREMENT_TRACE_SERIES,
    )
    max_series: Annotated[
        int,
        Field(ge=1, le=MAX_MEASUREMENT_TRACE_SERIES),
    ] = MAX_MEASUREMENT_TRACE_SERIES
    max_samples: Annotated[
        int,
        Field(ge=2, le=MAX_MEASUREMENT_TRACE_SAMPLES),
    ] = MAX_MEASUREMENT_TRACE_SAMPLES
    value_mode: TraceValueMode | None = None
    downsampling: TraceDownsampling = "minmax"

    @model_validator(mode="after")
    def validate_selection(self) -> MeasurementTracePreviewQuery:
        if self.recording_group_id is None and self.observable_id is None:
            raise ValueError(
                "trace preview requires a recording_group_id or observable_id"
            )
        if any(not axis_id for axis_id in self.fixed_axis_indices):
            raise ValueError("trace preview axis ids must be non-empty")
        if self.entities is not None:
            if self.entity_indices is not None:
                raise ValueError(
                    "trace selection accepts either entities or entity indices"
                )
            identities = tuple(entity_identity(entity) for entity in self.entities)
            if len(identities) != len(set(identities)):
                raise ValueError("trace entity identities must be unique")
        if self.entity_indices is not None and len(self.entity_indices) != len(
            set(self.entity_indices)
        ):
            raise ValueError("trace preview entity indices must be unique")
        return self


class MeasurementTraceSeries(_ViewModel):
    """One directly plottable numeric trace series."""

    point_index: Annotated[int, Field(ge=0)]
    logical_point_id: str | None = None
    label: str = Field(min_length=1)
    entity_index: Annotated[int, Field(ge=0)] | None = None
    entity: EntityRef | None = None
    x: tuple[float, ...] = Field(min_length=1)
    y: tuple[float, ...] = Field(min_length=1)
    source_sample_count: Annotated[int, Field(ge=1)]
    available_sample_count: Annotated[int, Field(ge=1)]
    unavailable_reasons: tuple[MeasurementUnavailableReason, ...] = ()
    evidence: InstrumentAcquisitionEvidence | None = None

    @model_validator(mode="after")
    def validate_samples(self) -> MeasurementTraceSeries:
        if len(self.x) != len(self.y):
            raise ValueError("trace preview x and y lengths must match")
        if len(self.y) > self.source_sample_count:
            raise ValueError("trace preview exceeds its source sample count")
        if len(self.y) > self.available_sample_count:
            raise ValueError("trace preview exceeds its available sample count")
        if self.available_sample_count > self.source_sample_count:
            raise ValueError(
                "trace preview availability exceeds its source sample count"
            )
        return self


class MeasurementTraceFailure(_ViewModel):
    """One bounded point/entity trace selection with no plottable samples."""

    point_index: Annotated[int, Field(ge=0)]
    logical_point_id: str | None = None
    label: str = Field(min_length=1)
    entity_index: Annotated[int, Field(ge=0)] | None = None
    entity: EntityRef | None = None
    reasons: tuple[MeasurementUnavailableReason, ...] = Field(min_length=1)
    evidence: InstrumentAcquisitionEvidence | None = None


class MeasurementTracePreview(_ViewModel):
    """Bounded numeric series for one selected point/entity-local observable.

    ``selected_series_count`` is the authored domain selection size. It does
    not promise that every selected point is durable yet or has an available
    observable value; ``returned_series_count`` counts response series only.
    """

    fixed_axis_indices: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict
    )
    dimension_id: str = Field(min_length=1)
    recording_group_id: str | None = Field(default=None, min_length=1)
    coordinate_id: str = Field(min_length=1)
    observable_id: str = Field(min_length=1)
    coordinate_label: str | None = None
    observable_label: str | None = None
    coordinate_unit: str | None = None
    observable_unit: str | None = None
    entity_dimension_id: str | None = Field(default=None, min_length=1)
    entity_acquisition: MeasurementEntityAcquisition | None = None
    layout: TraceLayout
    value_mode: TraceValueMode
    value_unit: str | None = None
    downsampling: TraceDownsampling
    series: tuple[MeasurementTraceSeries, ...] = ()
    failures: tuple[MeasurementTraceFailure, ...] = ()
    selected_series_count: Annotated[int, Field(ge=0)]
    inspected_series_count: Annotated[int, Field(ge=0)]
    returned_series_count: Annotated[int, Field(ge=0)]
    truncated_series: bool = False
    source_sample_count: Annotated[int, Field(ge=0)]
    returned_sample_count: Annotated[int, Field(ge=0)]
    samples_reduced: bool = False

    @model_validator(mode="after")
    def validate_counts(self) -> MeasurementTracePreview:
        if self.returned_series_count != len(self.series):
            raise ValueError("trace preview returned series count is inconsistent")
        if len(self.series) + len(self.failures) > self.inspected_series_count:
            raise ValueError("trace preview results exceed its inspected series count")
        if self.inspected_series_count > self.selected_series_count:
            raise ValueError("trace preview inspects more than its domain selection")
        if self.truncated_series != (
            self.inspected_series_count < self.selected_series_count
        ):
            raise ValueError("trace preview truncation flag is inconsistent")
        if self.source_sample_count != sum(
            item.source_sample_count for item in self.series
        ):
            raise ValueError("trace preview source sample count is inconsistent")
        if self.returned_sample_count != sum(len(item.y) for item in self.series):
            raise ValueError("trace preview returned sample count is inconsistent")
        if self.returned_sample_count > self.source_sample_count:
            raise ValueError("trace preview returns more samples than its sources")
        if self.samples_reduced != (
            self.returned_sample_count < self.source_sample_count
        ):
            raise ValueError("trace preview sample reduction flag is inconsistent")
        return self


def _require_entry_role(
    entry: ContentEntry,
    role: Literal["artifact", "dataset", "record"],
) -> None:
    if entry.role != role:
        raise ValueError(f"run content view requires a {role}")


def _validate_base64(value: str) -> None:
    try:
        b64decode(value, validate=True)
    except (BinasciiError, ValueError) as error:
        raise ValueError("content_base64 must be valid base64") from error


__all__ = [
    "ActiveConfigView",
    "AnalysisContentBytesView",
    "ConfigActivationPage",
    "ConfigDraftPreview",
    "ConfigEntryView",
    "ConfigRegistryPage",
    "DaemonHealth",
    "DriverManagedInstrumentConnectionSummary",
    "InstrumentConnectionSummary",
    "InstrumentListView",
    "InstrumentView",
    "MeasurementArrowColumn",
    "MeasurementArrowQuery",
    "MeasurementLivePreview",
    "MeasurementPreview",
    "MeasurementSlice",
    "MeasurementSliceQuery",
    "MeasurementTracePreview",
    "MeasurementTracePreviewQuery",
    "MeasurementTraceSeries",
    "ParameterProposalPage",
    "ParameterProposalView",
    "ProjectAnalysisContentPage",
    "ProjectAnalysisPage",
    "ProjectAnalysisSummary",
    "ProjectAnalysisView",
    "RunAdmissionView",
    "RunAnalysisPage",
    "RunAnalysisSummary",
    "RunAnalysisView",
    "RunArtifactBytesView",
    "RunConfigView",
    "RunControlView",
    "RunDatasetBytesView",
    "RunDetail",
    "RunPlanView",
    "RunRequestView",
    "RunResourceBlocker",
    "RunResourceView",
    "RunSummary",
    "RunSummaryPage",
    "SampleAnalysisPage",
    "SampleAnalysisSummary",
    "SampleAnalysisView",
    "SamplePage",
    "SampleRevisionPage",
    "SampleSummary",
    "SampleView",
    "SerialInstrumentConnectionSummary",
    "TcpipSocketInstrumentConnectionSummary",
    "VirtualInstrumentConnectionSummary",
]


class ConfigContextResolution(_ViewModel):
    """Effective trial parameters, exact identity, and unknown-value diagnostics."""

    config: ConfigProfileSnapshot
    config_source: ContextRunConfigSource
    value_origins: tuple[ConfigValueOrigin, ...] = ()
    missing_values: tuple[str, ...] = ()
