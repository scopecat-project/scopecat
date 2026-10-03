"""Durable one-shot schedules for exact procedure invocations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scopecat.automation.models import (
    ProcedureDefinitionRef,
    ProcedureIntent,
    ProcedureSource,
    procedure_intent_hash,
)
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.records.content import Sha256ContentHash

type _NonEmptyText = Annotated[str, Field(min_length=1)]
type ProcedureScheduleState = Literal["pending", "materialized", "cancelled"]

_PROCEDURE_SCHEDULE_REQUEST_KEY_CODEC = "scopecat.procedure-schedule-request.v1"


class _ScheduleModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class ProcedureScheduleMaterialization(_ScheduleModel):
    """Exact procedure run atomically admitted for one due schedule."""

    procedure_run_id: _NonEmptyText
    request_key: _NonEmptyText
    materialized_at: datetime

    @field_validator("procedure_run_id", "request_key")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="schedule materialization identity")

    @field_validator("materialized_at")
    @classmethod
    def canonicalize_materialized_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="materialized_at")


class ProcedureScheduleCancellation(_ScheduleModel):
    """Terminal operator reason for suppressing one pending schedule."""

    actor: _NonEmptyText
    reason: _NonEmptyText
    cancelled_at: datetime

    @field_validator("actor", "reason")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="schedule cancellation identity")

    @field_validator("cancelled_at")
    @classmethod
    def canonicalize_cancelled_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="cancelled_at")


def procedure_schedule_request_key(
    schedule_id: str,
    due_at: datetime,
    definition: ProcedureDefinitionRef,
    intent_hash: Sha256ContentHash,
) -> str:
    """Derive the stable Procedure submission key for one exact schedule."""

    selected_schedule_id = _non_blank(schedule_id, field_name="procedure schedule id")
    selected_due_at = _canonical_utc(due_at, field_name="due_at")
    digest = stable_content_hash(
        {
            "codec": _PROCEDURE_SCHEDULE_REQUEST_KEY_CODEC,
            "schedule_id": selected_schedule_id,
            "due_at": selected_due_at.isoformat(),
            "definition": definition.model_dump(mode="json"),
            "intent_hash": intent_hash,
        }
    )
    return f"procedure-schedule:{digest}"


class ProcedureSchedule(_ScheduleModel):
    """Revisioned one-shot admission of an exact versioned procedure intent."""

    schedule_id: _NonEmptyText
    definition: ProcedureDefinitionRef
    intent: ProcedureIntent
    intent_hash: Sha256ContentHash
    source: ProcedureSource | None = None
    due_at: datetime
    revision: int = Field(ge=1)
    state: ProcedureScheduleState
    created_at: datetime
    updated_at: datetime
    materialization: ProcedureScheduleMaterialization | None = None
    cancellation: ProcedureScheduleCancellation | None = None

    @field_validator("schedule_id")
    @classmethod
    def validate_schedule_id(cls, value: str) -> str:
        return _non_blank(value, field_name="procedure schedule id")

    @field_validator("due_at")
    @classmethod
    def canonicalize_due_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="due_at")

    @field_validator("created_at")
    @classmethod
    def canonicalize_created_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="created_at")

    @field_validator("updated_at")
    @classmethod
    def canonicalize_updated_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="updated_at")

    @model_validator(mode="after")
    def validate_schedule(self) -> ProcedureSchedule:
        if self.intent_hash != procedure_intent_hash(
            self.definition, self.intent, source=self.source
        ):
            raise ValueError(
                "procedure schedule intent hash must cover its definition and intent"
            )
        if self.updated_at < self.created_at:
            raise ValueError(
                "procedure schedule cannot be updated before it is created"
            )

        if self.state == "pending":
            if self.materialization is not None or self.cancellation is not None:
                raise ValueError(
                    "pending procedure schedule cannot have terminal details"
                )
        elif self.state == "materialized":
            if self.materialization is None:
                raise ValueError(
                    "materialized procedure schedule requires materialization details"
                )
            if self.cancellation is not None:
                raise ValueError("materialized procedure schedule cannot be cancelled")
            materialized_at = self.materialization.materialized_at
            if (
                materialized_at < self.created_at
                or materialized_at < self.due_at
                or materialized_at > self.updated_at
            ):
                raise ValueError(
                    "schedule materialization time must be due and within its lifetime"
                )
            expected_request_key = procedure_schedule_request_key(
                self.schedule_id,
                self.due_at,
                self.definition,
                self.intent_hash,
            )
            if self.materialization.request_key != expected_request_key:
                raise ValueError(
                    "schedule materialization request key must identify its exact "
                    "schedule"
                )
        else:
            if self.cancellation is None:
                raise ValueError(
                    "cancelled procedure schedule requires cancellation details"
                )
            if self.materialization is not None:
                raise ValueError("cancelled procedure schedule cannot be materialized")
            if not self.created_at <= self.cancellation.cancelled_at <= self.updated_at:
                raise ValueError(
                    "schedule cancellation time must be within its lifetime"
                )
        return self


def _canonical_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(UTC)


def _non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


__all__ = [
    "ProcedureSchedule",
    "ProcedureScheduleCancellation",
    "ProcedureScheduleMaterialization",
    "ProcedureScheduleState",
    "procedure_schedule_request_key",
]
