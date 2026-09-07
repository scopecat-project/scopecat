"""Process-safe instrument commands and their explicit outcomes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.program.measurement_types import MeasurementDType
from scopecat.records.content import CommandPayload
from scopecat.records.costs import OperationCostMeasurement
from scopecat.records.instrument import (
    CommandChannelBinding as _CommandChannelBinding,
)
from scopecat.records.instrument import (
    InstrumentReadback as _InstrumentReadback,
)
from scopecat.records.instrument import (
    InstrumentStateReadback as _InstrumentStateReadback,
)
from scopecat.records.instrument import (
    InstrumentStateSnapshot as _InstrumentStateSnapshot,
)
from scopecat.records.instrument import StateMemberTarget as _StateMemberTarget
from scopecat.records.instrument import (
    state_member_identity as _state_member_identity,
)
from scopecat.records.instrument import (
    validate_entity_target as _validate_entity_target,
)
from scopecat.records.metadata import JsonMetadata
from scopecat.sdk.problems import Problem

type _NonEmptyId = Annotated[str, Field(min_length=1)]
_JSON_SAFE_INTEGER = (1 << 53) - 1


class InstrumentStateAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    target: _StateMemberTarget
    value: StateValue
    entity_ids: list[_NonEmptyId] = Field(default_factory=list)
    channel_bindings: list[_CommandChannelBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_target(self) -> InstrumentStateAssignment:
        _validate_entity_target(self.entity_ids, self.channel_bindings)
        return self


class InstrumentStateReadCommand(BaseModel):
    """Explicit member selection for one live hardware observation."""

    model_config = ConfigDict(extra="forbid")

    targets: list[_StateMemberTarget] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_targets(self) -> InstrumentStateReadCommand:
        identities = [_state_member_identity(target) for target in self.targets]
        if len(identities) != len(set(identities)):
            raise ValueError("instrument state read targets must be unique")
        return self


class InstrumentStateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: _NonEmptyId
    instrument_id: _NonEmptyId
    assignments: list[InstrumentStateAssignment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_structure(self) -> InstrumentStateCommand:
        identities = [
            _state_member_identity(assignment.target) for assignment in self.assignments
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("instrument state command property targets must be unique")
        return self


class ApplyReceipt(BaseModel):
    """Outcome reported after one instrument state command.

    ``unknown`` is intentionally distinct from failure.  A driver can lose the
    response after the hardware accepted a command, so the execution engine must
    reconcile state before it can safely issue another command or retry.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["applied", "not_applied", "unknown"] = "applied"
    problems: tuple[Problem, ...] = ()
    readback: _InstrumentStateReadback | None = None
    metadata: JsonMetadata = Field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None

    @model_validator(mode="after")
    def validate_outcome_truth_table(self) -> ApplyReceipt:
        if self.status == "applied" and self.problems:
            raise ValueError("an applied receipt cannot contain problems")
        if self.status != "applied" and not self.problems:
            raise ValueError("a negative or unknown apply receipt requires a problem")
        return self


class InstrumentConfiguredDefaultsApplyReceipt(BaseModel):
    """Outcome of reconciling a live session with configured defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    session_id: _NonEmptyId
    operation_id: _NonEmptyId
    instrument_id: _NonEmptyId
    config_entry_id: _NonEmptyId
    status: Literal["applied", "unchanged", "rejected"]
    problems: tuple[Problem, ...] = ()
    state: _InstrumentStateSnapshot | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> InstrumentConfiguredDefaultsApplyReceipt:
        if self.status in {"applied", "unchanged"}:
            if self.problems:
                raise ValueError(
                    "successful configured-default apply cannot contain problems"
                )
            if self.state is None:
                raise ValueError(
                    "successful configured-default apply requires synchronized state"
                )
        elif not self.problems:
            raise ValueError("rejected configured-default apply requires a problem")
        elif self.state is not None:
            raise ValueError("rejected configured-default apply cannot report state")
        if self.state is not None and self.state.instrument_id != self.instrument_id:
            raise ValueError("configured-default state must match instrument_id")
        return self


class CollectAxisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    kind: _NonEmptyId
    offset: Annotated[int, Field(strict=True, ge=0, le=_JSON_SAFE_INTEGER)] | None = (
        None
    )
    size: Annotated[int, Field(strict=True, ge=1, le=_JSON_SAFE_INTEGER)] | None = None
    unit: str | None = None
    metadata: JsonMetadata = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_selection(self) -> CollectAxisRequest:
        if self.size is None and self.offset is not None:
            raise ValueError("an unbounded collect axis cannot select an offset")
        if (
            self.size is not None
            and self.offset is not None
            and self.offset + self.size > _JSON_SAFE_INTEGER
        ):
            raise ValueError("collect axis selection exceeds the JSON-safe range")
        return self


class CollectResultRequest(BaseModel):
    """One explicitly acquisition-scoped result request.

    Interface and acquisition identity prevent a driver from inferring
    ownership from an accidentally unique result id.
    """

    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    interface_id: InterfaceId
    component_path: list[_NonEmptyId] = Field(default_factory=list)
    acquisition_id: _NonEmptyId
    result_id: _NonEmptyId
    unit: str | None = None
    dtype: MeasurementDType = "float64"
    dimensions: list[CollectAxisRequest] = Field(default_factory=list)
    entity_ids: list[_NonEmptyId] = Field(default_factory=list)
    channel_bindings: list[_CommandChannelBinding] = Field(default_factory=list)
    metadata: JsonMetadata = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target(self) -> CollectResultRequest:
        _validate_entity_target(self.entity_ids, self.channel_bindings)
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(f"{self.dtype} collection results cannot have a unit")
        return self


class InteractiveCollectIntent(BaseModel):
    """State-independent acquisition selection for one direct interaction."""

    model_config = ConfigDict(extra="forbid")

    command_id: _NonEmptyId
    instrument_id: _NonEmptyId
    interface_id: InterfaceId
    component_path: list[_NonEmptyId] = Field(default_factory=list)
    acquisition_id: _NonEmptyId
    result_ids: list[_NonEmptyId] = Field(default_factory=list)

    @field_validator("result_ids")
    @classmethod
    def validate_unique_results(cls, value: list[str]) -> list[str]:
        _require_unique(value, "interactive collect result ids")
        return value


class CollectCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: _NonEmptyId
    instrument_id: _NonEmptyId
    point_index: int | None = Field(default=None, ge=0)
    point_count: int = Field(ge=1)
    requests: list[CollectResultRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_requests(self) -> CollectCommand:
        if self.point_index is not None and self.point_index >= self.point_count:
            raise ValueError("collect command point index must be within point count")
        request_ids = [request.id for request in self.requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("collect command request ids must be unique")
        acquisition_targets = {
            (
                request.interface_id,
                tuple(request.component_path),
                request.acquisition_id,
                tuple(request.entity_ids) if request.channel_bindings else (),
                tuple(
                    (
                        binding.entity_id,
                        binding.channel_id,
                        binding.interface_id,
                    )
                    for binding in request.channel_bindings
                ),
            )
            for request in self.requests
        }
        if len(acquisition_targets) != 1:
            raise ValueError("collect command must target exactly one acquisition")
        return self


class ResolvedInteractiveCollect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["resolved"] = "resolved"
    command: CollectCommand


class RejectedInteractiveCollect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["rejected"] = "rejected"
    problems: tuple[Problem, ...] = Field(min_length=1)


type InteractiveCollectResolution = Annotated[
    ResolvedInteractiveCollect | RejectedInteractiveCollect,
    Field(discriminator="kind"),
]


class InstrumentOperationArgument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    value: StateValue


class InvokeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: _NonEmptyId
    instrument_id: _NonEmptyId
    resource_id: _NonEmptyId
    interface_id: InterfaceId
    component_path: list[_NonEmptyId] = Field(default_factory=list)
    operation_id: _NonEmptyId
    arguments: list[InstrumentOperationArgument] = Field(default_factory=list)
    payloads: dict[str, CommandPayload] = Field(default_factory=dict)
    entity_ids: list[_NonEmptyId] = Field(default_factory=list)
    channel_bindings: list[_CommandChannelBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_structure(self) -> InvokeCommand:
        _require_unique(
            (argument.id for argument in self.arguments),
            "instrument operation argument ids",
        )
        _validate_entity_target(self.entity_ids, self.channel_bindings)
        _validate_payload_bindings(
            values=(argument.value for argument in self.arguments),
            payloads=self.payloads,
            label="instrument invoke command",
        )
        return self


class InvokeReceipt(BaseModel):
    """Outcome reported after one atomic instrument operation."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["invoked", "not_invoked", "unknown"] = "invoked"
    problems: tuple[Problem, ...] = ()
    readback: _InstrumentStateReadback | None = None
    metadata: JsonMetadata = Field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None

    @model_validator(mode="after")
    def validate_outcome_truth_table(self) -> InvokeReceipt:
        if self.status == "invoked" and self.problems:
            raise ValueError("an invoked receipt cannot contain problems")
        if self.status != "invoked" and not self.problems:
            raise ValueError("a negative or unknown invoke receipt requires a problem")
        return self


class AcquisitionPreparationReceipt(BaseModel):
    """Outcome of preparing all acquisition demands in one hardware batch."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["prepared", "not_prepared", "unknown"] = "prepared"
    problems: tuple[Problem, ...] = ()
    metadata: JsonMetadata = Field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None

    @model_validator(mode="after")
    def validate_outcome_truth_table(self) -> AcquisitionPreparationReceipt:
        if self.status == "prepared" and self.problems:
            raise ValueError("a prepared receipt cannot contain problems")
        if self.status != "prepared" and not self.problems:
            raise ValueError("a negative or unknown prepare receipt requires a problem")
        return self


class CollectReceipt(BaseModel):
    """Explicit outcome reported after one collection command.

    ``not_collected`` proves that collection did not occur; ``unknown`` means it
    may have occurred. The distinction prevents automatic retry from silently
    duplicating an external acquisition.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["collected", "not_collected", "unknown"] = "collected"
    problems: tuple[Problem, ...] = ()
    readback: _InstrumentReadback | None = None
    metadata: JsonMetadata = Field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None

    @model_validator(mode="after")
    def validate_readback_outcome(self) -> CollectReceipt:
        if self.status == "collected" and self.readback is None:
            raise ValueError("a collected receipt requires a readback")
        if self.status == "not_collected" and self.readback is not None:
            raise ValueError("a not_collected receipt must not contain a readback")
        if self.status == "collected" and self.problems:
            raise ValueError("a collected receipt cannot contain problems")
        if self.status != "collected" and not self.problems:
            raise ValueError("a negative or unknown collect receipt requires a problem")
        return self


def _validate_payload_bindings(
    *,
    values: Iterable[StateValue],
    payloads: Mapping[str, CommandPayload],
    label: str,
) -> None:
    mismatched_keys = [
        (payload_id, payload.id)
        for payload_id, payload in payloads.items()
        if payload_id != payload.id
    ]
    if mismatched_keys:
        payload_id, descriptor_id = mismatched_keys[0]
        raise ValueError(
            f"{label} payload map key {payload_id!r} does not match "
            f"payload.id {descriptor_id!r}"
        )
    referenced_ids = {
        value.payload_id
        for item in values
        if isinstance((value := item.root), PayloadRef)
    }
    payload_ids = set(payloads)
    missing = sorted(referenced_ids - payload_ids)
    extra = sorted(payload_ids - referenced_ids)
    issues: list[str] = []
    if missing:
        issues.append(f"missing referenced payload ids: {missing!r}")
    if extra:
        issues.append(f"unreferenced payload ids: {extra!r}")
    if issues:
        raise ValueError(f"{label} payload map has " + "; ".join(issues))


def _require_unique(values: Iterable[str], label: str) -> None:
    selected = tuple(values)
    if len(selected) != len(set(selected)):
        raise ValueError(f"{label} must be unique")
