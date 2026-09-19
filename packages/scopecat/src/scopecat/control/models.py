"""Durable records owned by the project control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.records.setup import SetupRevisionRef

type PointCoordinateValue = bool | int | float | str | Quantity | EntityRef | None
type PointCoordinateKind = Literal[
    "bool",
    "int",
    "float",
    "string",
    "quantity",
    "entity",
]

type ControlRunState = Literal[
    "queued",
    "leased",
    "attention_required",
    "closed",
]
type ResourceKind = Literal["instrument"]
type ResourceClaimStatus = Literal["active", "quarantined"]
type ResourceOwnerKind = Literal["run", "instrument_session"]
type InventoryMigrationBlockerState = Literal[
    "queued",
    "leased",
    "attention_required",
    "active",
    "quarantined",
]
type InstrumentSessionState = Literal["active", "attention_required", "closed"]
type InstrumentOperationKind = Literal["apply", "invoke", "collect"]
type InstrumentSessionEndStatus = Literal["closed", "aborted"]
type RunExecutionSegmentResult = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
]
type RunExecutionSegmentCertainty = Literal["known", "indeterminate"]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


class _ControlModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class ResourceKey(_ControlModel):
    """Canonical exclusive resource identity used by the scheduler."""

    kind: ResourceKind = "instrument"
    id: str = Field(min_length=1)

    @classmethod
    def instrument(cls, exclusivity_key: str) -> ResourceKey:
        return cls(kind="instrument", id=exclusivity_key)


class RunResourceRequirement(_ControlModel):
    """Logical resource identity requested by a run plan."""

    kind: ResourceKind = "instrument"
    id: str = Field(min_length=1)


class RunDomainTargetRequirement(_ControlModel):
    """Domain target identity and this run's exact logical instrument footprint."""

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    instrument_ids: tuple[str, ...] = ()

    @field_validator("instrument_ids")
    @classmethod
    def validate_instrument_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not instrument_id for instrument_id in value):
            raise ValueError("domain target instrument ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("domain target instrument ids must be unique")
        return tuple(sorted(value))


class PointCoordinateSpec(_ControlModel):
    """Admissibility and authored sampling facts for one point coordinate."""

    id: str = Field(min_length=1)
    kind: PointCoordinateKind
    dimension: str | None = None
    unit: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    finite: bool = True
    choices: tuple[str, ...] | None = None
    entity_kind: str | None = None
    sampled_values: tuple[PointCoordinateValue, ...] = ()
    sampled_values_truncated: bool = False


class AdaptiveRegionSpec(_ControlModel):
    """One stable outer-domain region admitted for adaptive extension."""

    id: str = Field(min_length=1)
    coordinates: dict[str, PointCoordinateValue]
    initial_point_count: int = Field(ge=0)


class RunPlanSummary(_ControlModel):
    """Bounded scheduling and presentation facts for an in-process plan."""

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
    domain_target_requirement: RunDomainTargetRequirement | None = None
    host_instrument_order: tuple[str, ...] = ()
    host_provider_id: str | None = Field(default=None, min_length=1)
    host_contract_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator(
        "record_ids",
        "host_instrument_order",
    )
    @classmethod
    def validate_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value):
            raise ValueError("run plan ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("run plan summary ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_coordinate_contract(self) -> RunPlanSummary:
        coordinate_ids = self.coordinate_ids
        if len(coordinate_ids) != len(set(coordinate_ids)):
            raise ValueError("run point coordinate ids must be unique")
        expected = set(coordinate_ids)
        if any(set(point) != expected for point in self.sampled_points):
            raise ValueError("sampled points must contain every coordinate")
        if not set(self.adaptive_coordinate_ids).issubset(expected):
            raise ValueError("adaptive coordinates must reference admitted axes")
        if len(self.adaptive_coordinate_ids) != len(set(self.adaptive_coordinate_ids)):
            raise ValueError("adaptive coordinate ids must be unique")
        adaptive = self.point_count is None
        if adaptive != (self.adaptive_scope is not None):
            raise ValueError("open point plans require an adaptive scope")
        if not adaptive and (
            self.adaptive_coordinate_ids
            or self.per_region_point_limit is not None
            or self.adaptive_region_count
            or self.adaptive_regions
            or self.adaptive_regions_truncated
        ):
            raise ValueError("static point plans cannot declare adaptive axes")
        if adaptive:
            if self.adaptive_region_count < 1:
                raise ValueError("adaptive point plans require at least one region")
            if len(self.adaptive_regions) > self.adaptive_region_count:
                raise ValueError("adaptive region sample exceeds its total count")
            if self.adaptive_regions_truncated != (
                len(self.adaptive_regions) < self.adaptive_region_count
            ):
                raise ValueError("adaptive region truncation must match its sample")
            outer_ids = expected - set(self.adaptive_coordinate_ids)
            if any(
                set(region.coordinates) != outer_ids for region in self.adaptive_regions
            ):
                raise ValueError("adaptive regions must identify every outer axis")
        return self

    @property
    def coordinate_ids(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self.coordinates)

    @field_validator("run_resource_requirements")
    @classmethod
    def validate_unique_requirements(
        cls,
        value: tuple[RunResourceRequirement, ...],
    ) -> tuple[RunResourceRequirement, ...]:
        identities = tuple((requirement.kind, requirement.id) for requirement in value)
        if len(identities) != len(set(identities)):
            raise ValueError("run plan resource requirements must be unique")
        return value

    @model_validator(mode="after")
    def validate_resource_alignment(self) -> RunPlanSummary:
        if self.initial_point_count > self.point_limit:
            raise ValueError("initial point count cannot exceed the point limit")
        if self.point_count is not None and self.point_count != self.point_limit:
            raise ValueError("a closed point count must equal the point limit")
        if (
            self.point_count is not None
            and self.initial_point_count != self.point_count
        ):
            raise ValueError("a closed point plan is entirely initial")
        domain = self.domain_target_requirement
        if domain is not None:
            instrument_ids = {
                requirement.id
                for requirement in self.run_resource_requirements
                if requirement.kind == "instrument"
            }
            missing = sorted(set(domain.instrument_ids) - instrument_ids)
            if missing:
                raise ValueError(
                    "run plan omits domain target instruments: " + ", ".join(missing)
                )

        required = {
            requirement.id
            for requirement in self.run_resource_requirements
            if requirement.kind == "instrument"
        }
        # Domain-owned instruments are required without a daemon-hosted driver.
        if not set(self.host_instrument_order).issubset(required):
            raise ValueError(
                "run plan host instrument order must reference instrument requirements"
            )
        has_host = bool(self.host_instrument_order)
        if has_host != (self.host_provider_id is not None):
            raise ValueError("host provider identity must match hosted instruments")
        if has_host != (self.host_contract_fingerprint is not None):
            raise ValueError("host contract fingerprint must match hosted instruments")
        return self


class RunAdmissionRecord(_ControlModel):
    """Scheduler facts committed with an accepted run skeleton."""

    submission_id: str = Field(min_length=1)
    submission_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(min_length=1)
    plan: RunPlanSummary
    display_name: str | None = Field(default=None, min_length=1)
    tags: tuple[str, ...] = ()
    description: str | None = Field(default=None, min_length=1)
    resource_claims: tuple[ResourceKey, ...]
    admitted_at: datetime = Field(default_factory=utc_now)

    @field_validator("resource_claims")
    @classmethod
    def validate_unique_claims(
        cls,
        value: tuple[ResourceKey, ...],
    ) -> tuple[ResourceKey, ...]:
        identities = tuple((claim.kind, claim.id) for claim in value)
        if len(identities) != len(set(identities)):
            raise ValueError("run admission resource claims must be unique")
        return value

    @model_validator(mode="after")
    def validate_resource_claim_alignment(self) -> RunAdmissionRecord:
        logical = self.plan.run_resource_requirements
        if len(logical) != len(self.resource_claims):
            raise ValueError(
                "run admission claims must align with logical plan requirements"
            )
        for logical_requirement, canonical_claim in zip(
            logical,
            self.resource_claims,
            strict=True,
        ):
            if logical_requirement.kind != canonical_claim.kind:
                raise ValueError(
                    "run admission claims must align with logical plan requirements"
                )
        return self

    def is_retry_of(self, other: RunAdmissionRecord) -> bool:
        return (
            self.submission_id == other.submission_id
            and self.submission_content_hash == other.submission_content_hash
        )

    @property
    def experiment_id(self) -> str:
        return self.plan.experiment_id


class ControlRun(_ControlModel):
    """Current scheduler state plus the immutable admission record."""

    sequence: int = Field(ge=1)
    admission: RunAdmissionRecord
    state: ControlRunState
    updated_at: datetime
    attention_reason: str | None = None
    cancellation_requested_at: datetime | None = None

    @model_validator(mode="after")
    def validate_state_details(self) -> ControlRun:
        if self.state == "attention_required":
            if not self.attention_reason:
                msg = "attention-required control run requires a reason"
                raise ValueError(msg)
        elif self.attention_reason is not None:
            msg = "attention reason is only valid for attention-required runs"
            raise ValueError(msg)
        return self

    @property
    def run_id(self) -> str:
        return self.admission.run_id


class RunPage(_ControlModel):
    items: tuple[ControlRun, ...]
    next_cursor: int | None = None


class DurableEventInput(_ControlModel):
    """One event to append to the globally ordered replay stream."""

    run_id: str | None = None
    kind: str = Field(min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class DurableEvent(DurableEventInput):
    event_id: int = Field(ge=1)


class EventPage(_ControlModel):
    items: tuple[DurableEvent, ...]
    next_cursor: int | None = None


class ExecutorLease(_ControlModel):
    """Renewable fencing token for the process executing one run."""

    segment_id: str = Field(min_length=1)
    run_id: str
    executor_id: str
    token: str
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_lifetime(self) -> ExecutorLease:
        if self.expires_at <= self.renewed_at:
            msg = "executor lease must expire after its renewal time"
            raise ValueError(msg)
        return self


class RunExecutionSegment(_ControlModel):
    """One immutable interval of continuous executor ownership within a run."""

    sequence: int = Field(ge=1)
    segment_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    executor_id: str = Field(min_length=1)
    run_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    start_point_count: int = Field(ge=0)
    ended_at: datetime | None = None
    end_point_count: int | None = Field(default=None, ge=0)
    result: RunExecutionSegmentResult | None = None
    certainty: RunExecutionSegmentCertainty | None = None
    reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> RunExecutionSegment:
        terminal = (
            self.ended_at,
            self.end_point_count,
            self.result,
            self.certainty,
        )
        if any(item is None for item in terminal) and not all(
            item is None for item in terminal
        ):
            raise ValueError("execution segment terminal fields must appear together")
        if self.ended_at is None:
            if self.reason is not None:
                raise ValueError("active execution segment cannot have an end reason")
            return self
        if self.ended_at < self.started_at:
            raise ValueError("execution segment cannot end before it starts")
        if self.end_point_count is None:
            raise AssertionError("terminal execution segment requires end coverage")
        if self.end_point_count < self.start_point_count:
            raise ValueError("execution segment coverage cannot move backwards")
        if self.result == "interrupted" and self.reason is None:
            raise ValueError("interrupted execution segment requires a reason")
        return self


class RunExecutionSegmentPage(_ControlModel):
    """Newest-first page of execution ownership intervals for one run."""

    items: tuple[RunExecutionSegment, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)


class ResourceClaim(_ControlModel):
    resource: ResourceKey
    owner_kind: ResourceOwnerKind
    owner_id: str = Field(min_length=1)
    status: ResourceClaimStatus
    acquired_at: datetime


class InventoryMigrationBlocker(_ControlModel):
    """A durable owner that prevents changing one resource identity."""

    key: ResourceKey
    owner_kind: ResourceOwnerKind
    owner_id: str = Field(min_length=1)
    state: InventoryMigrationBlockerState


class InstrumentSession(_ControlModel):
    """Durable daemon state for one explicit direct-control session."""

    session_id: str = Field(min_length=1)
    open_operation_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    setup: SetupRevisionRef
    instrument_ids: tuple[str, ...] = Field(min_length=1)
    exclusivity_keys: tuple[str, ...] = Field(min_length=1)
    state: InstrumentSessionState
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime
    attention_reason: str | None = None
    active_operation_id: str | None = None
    active_operation_kind: InstrumentOperationKind | None = None
    end_status: InstrumentSessionEndStatus | None = None

    @field_validator("instrument_ids")
    @classmethod
    def validate_instrument_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not instrument_id for instrument_id in value):
            raise ValueError("instrument session ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("instrument session ids must be unique")
        return value

    @field_validator("exclusivity_keys")
    @classmethod
    def validate_exclusivity_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not exclusivity_key for exclusivity_key in value):
            raise ValueError("instrument session exclusivity keys must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("instrument session exclusivity keys must be unique")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> InstrumentSession:
        if self.expires_at <= self.renewed_at:
            raise ValueError("instrument session must expire after its renewal time")
        return self

    @model_validator(mode="after")
    def validate_state(self) -> InstrumentSession:
        if len(self.instrument_ids) != len(self.exclusivity_keys):
            raise ValueError(
                "instrument session ids and exclusivity keys must have equal length"
            )
        if self.state == "active":
            if self.attention_reason is not None:
                raise ValueError("active instrument session cannot require attention")
            if self.end_status is not None:
                raise ValueError("active instrument session cannot have an end status")
        elif self.state == "attention_required":
            if not self.attention_reason:
                raise ValueError("attention-required session requires a reason")
            if self.end_status is not None:
                raise ValueError(
                    "attention-required instrument session cannot have an end status"
                )
        else:
            if self.attention_reason is not None:
                raise ValueError("closed instrument session cannot require attention")
            if self.end_status is None:
                raise ValueError("closed instrument session requires an end status")
            if (
                self.active_operation_id is not None
                or self.active_operation_kind is not None
            ):
                raise ValueError("closed instrument session cannot retain an operation")
        if (self.active_operation_id is None) != (self.active_operation_kind is None):
            raise ValueError(
                "instrument session operation id and kind must be present together"
            )
        return self
