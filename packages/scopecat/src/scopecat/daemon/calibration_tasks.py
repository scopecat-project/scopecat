"""Durable exact check-task specifications and stage dispatch contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.automation.calibration_tasks import (
    CalibrationTaskPlan,
    CalibrationTaskProgress,
)
from scopecat.automation.models import ProcedureDefinitionRef, ProcedureIntent
from scopecat.records.calibration_check import CalibrationCheckRequest
from scopecat.records.sample import SampleSelector


class CalibrationTaskCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    definition: ProcedureDefinitionRef
    intent: ProcedureIntent
    samples: tuple[SampleSelector, ...] = ()


class CalibrationTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=200)
    plan: CalibrationTaskPlan
    calls: dict[str, CalibrationTaskCall]

    @model_validator(mode="after")
    def validate_calls(self) -> CalibrationTaskCreate:
        if self.calls.keys() != {stage.id for stage in self.plan.stages}:
            raise ValueError("task calls must cover exactly its stages")
        for stage in self.plan.stages:
            declaration = CalibrationCheckRequest.model_validate(
                self.calls[stage.id].intent.get("calibration_check")
            )
            if declaration != stage.check:
                raise ValueError(
                    f"task call {stage.id!r} differs from its declared check"
                )
        return self


class CalibrationTaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    specification: CalibrationTaskCreate
    executions: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class CalibrationTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task: CalibrationTaskRecord
    progress: CalibrationTaskProgress


class CalibrationTaskDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)


class CalibrationTaskListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: int | None = Field(default=None, ge=1)


class CalibrationTaskPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[CalibrationTaskRecord, ...]
    next_cursor: int | None = None
