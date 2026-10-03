"""Typed wire contracts for durable one-shot procedure schedules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scopecat.automation.models import (
    ProcedureDefinitionRef,
    ProcedureIntent,
    ProcedureSource,
    procedure_intent_hash,
)
from scopecat.automation.schedules import ProcedureSchedule, ProcedureScheduleState
from scopecat.records.content import Sha256ContentHash

type _NonEmptyText = Annotated[str, Field(min_length=1)]


class _ScheduleWireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class ProcedureScheduleCreateCommand(_ScheduleWireModel):
    """Create one exact one-shot schedule under a caller-owned identity."""

    schedule_id: _NonEmptyText
    definition: ProcedureDefinitionRef
    intent: ProcedureIntent
    source: ProcedureSource | None = None
    due_at: datetime

    @field_validator("schedule_id")
    @classmethod
    def validate_schedule_id(cls, value: str) -> str:
        return _non_blank(value, field_name="procedure schedule id")

    @field_validator("due_at")
    @classmethod
    def canonicalize_due_at(cls, value: datetime) -> datetime:
        return _canonical_utc(value, field_name="due_at")

    @property
    def intent_hash(self) -> Sha256ContentHash:
        return procedure_intent_hash(self.definition, self.intent, source=self.source)


class ProcedureScheduleCreateReceipt(_ScheduleWireModel):
    """Current canonical schedule returned after idempotent creation or replay."""

    schedule: ProcedureSchedule


class ProcedureScheduleListQuery(_ScheduleWireModel):
    """Bounded newest-first inspection query over durable schedules."""

    cursor: int | None = Field(default=None, ge=1)
    limit: int = Field(default=50, ge=1, le=200)
    state: ProcedureScheduleState | None = None


class ProcedureSchedulePage(_ScheduleWireModel):
    """One newest-first page of durable procedure schedules."""

    items: tuple[ProcedureSchedule, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)

    @field_validator("items")
    @classmethod
    def validate_unique_schedules(
        cls,
        value: tuple[ProcedureSchedule, ...],
    ) -> tuple[ProcedureSchedule, ...]:
        _require_unique_schedule_ids(value)
        return value


class ProcedureScheduleDueQuery(_ScheduleWireModel):
    """Bound one finite server-clock traversal of pending due schedules."""

    cursor: int | None = Field(default=None, ge=1)
    through_sequence: int | None = Field(default=None, ge=1)
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_traversal(self) -> ProcedureScheduleDueQuery:
        _require_traversal_pair(
            cursor=self.cursor,
            through_sequence=self.through_sequence,
            cursor_name="procedure schedule due cursor",
        )
        return self


class ProcedureScheduleDuePage(_ScheduleWireModel):
    """One insertion-oldest page within a finite due-schedule traversal."""

    items: tuple[ProcedureSchedule, ...] = ()
    next_cursor: int | None = Field(default=None, ge=1)
    through_sequence: int | None = Field(default=None, ge=1)

    @field_validator("items")
    @classmethod
    def validate_due_schedules(
        cls,
        value: tuple[ProcedureSchedule, ...],
    ) -> tuple[ProcedureSchedule, ...]:
        _require_unique_schedule_ids(value)
        if any(item.state != "pending" for item in value):
            raise ValueError("due procedure schedule page requires pending schedules")
        return value

    @model_validator(mode="after")
    def validate_traversal(self) -> ProcedureScheduleDuePage:
        _require_traversal_pair(
            cursor=self.next_cursor,
            through_sequence=self.through_sequence,
            cursor_name="next procedure schedule due cursor",
        )
        return self


class ProcedureScheduleCancelCommand(_ScheduleWireModel):
    """Cancel one exact pending schedule through revision compare-and-swap."""

    schedule_id: _NonEmptyText
    expected_schedule_revision: int = Field(ge=1)
    actor: _NonEmptyText
    reason: _NonEmptyText

    @field_validator("schedule_id")
    @classmethod
    def validate_schedule_id(cls, value: str) -> str:
        return _non_blank(value, field_name="procedure schedule id")

    @field_validator("actor", "reason")
    @classmethod
    def validate_cancellation_identity(cls, value: str) -> str:
        return _non_blank(value, field_name="schedule cancellation identity")


class ProcedureScheduleCancelReceipt(_ScheduleWireModel):
    """Terminal schedule returned after exact cancellation."""

    schedule: ProcedureSchedule

    @model_validator(mode="after")
    def validate_cancelled(self) -> ProcedureScheduleCancelReceipt:
        if self.schedule.state != "cancelled":
            raise ValueError("schedule cancellation receipt requires cancellation")
        return self


class ProcedureScheduleMaterializeCommand(_ScheduleWireModel):
    """Atomically admit the ProcedureRun for one exact due schedule."""

    schedule_id: _NonEmptyText
    expected_schedule_revision: int = Field(ge=1)

    @field_validator("schedule_id")
    @classmethod
    def validate_schedule_id(cls, value: str) -> str:
        return _non_blank(value, field_name="procedure schedule id")


class ProcedureScheduleMaterializeReceipt(_ScheduleWireModel):
    """Terminal schedule returned after atomic ProcedureRun admission."""

    schedule: ProcedureSchedule

    @model_validator(mode="after")
    def validate_materialized(self) -> ProcedureScheduleMaterializeReceipt:
        if self.schedule.state != "materialized":
            raise ValueError(
                "schedule materialization receipt requires materialization"
            )
        return self


def _require_unique_schedule_ids(value: tuple[ProcedureSchedule, ...]) -> None:
    identities = tuple(item.schedule_id for item in value)
    if len(identities) != len(set(identities)):
        raise ValueError("procedure schedule page ids must be unique")


def _require_traversal_pair(
    *,
    cursor: int | None,
    through_sequence: int | None,
    cursor_name: str,
) -> None:
    if (cursor is None) != (through_sequence is None):
        raise ValueError(
            f"{cursor_name} and through_sequence must be provided together"
        )
    if (
        cursor is not None
        and through_sequence is not None
        and cursor >= through_sequence
    ):
        raise ValueError(f"{cursor_name} must be below through_sequence")


def _canonical_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(UTC)


def _non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


__all__ = [
    "ProcedureScheduleCancelCommand",
    "ProcedureScheduleCancelReceipt",
    "ProcedureScheduleCreateCommand",
    "ProcedureScheduleCreateReceipt",
    "ProcedureScheduleDuePage",
    "ProcedureScheduleDueQuery",
    "ProcedureScheduleListQuery",
    "ProcedureScheduleMaterializeCommand",
    "ProcedureScheduleMaterializeReceipt",
    "ProcedureSchedulePage",
]
