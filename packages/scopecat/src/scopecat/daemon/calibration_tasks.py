"""Durable exact check-task specifications and stage dispatch contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.automation.calibration_tasks import (
    CalibrationTaskPlan,
    CalibrationTaskProgress,
)
from scopecat.automation.models import (
    ProcedureDefinitionRef,
    ProcedureIntent,
    ProcedureRun,
)
from scopecat.records.calibration_check import CalibrationCheckRequest
from scopecat.records.sample import SampleSelector


class CalibrationTaskCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    definition: ProcedureDefinitionRef
    intent: ProcedureIntent
    samples: tuple[SampleSelector, ...] = ()


class CalibrationStageRepair(BaseModel):
    """One bounded fit followed by the original check on its exact candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    call: CalibrationTaskCall
    proposal_id: str = Field(min_length=1)


class CalibrationRepairBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_repairs: int = Field(ge=0, le=256)
    elapsed: timedelta = Field(gt=timedelta(0), le=timedelta(days=7))


class CalibrationStageAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    phase: Literal["check", "repair", "verify"]
    procedure_run_id: str
    check: CalibrationCheckRequest


class CalibrationTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=200)
    plan: CalibrationTaskPlan
    calls: dict[str, CalibrationTaskCall]
    finalization: CalibrationTaskCall | None = None
    repairs: dict[str, CalibrationStageRepair] = Field(default_factory=dict)
    repair_budget: CalibrationRepairBudget | None = None

    @model_validator(mode="after")
    def validate_calls(self) -> CalibrationTaskCreate:
        if self.repairs and self.repair_budget is None:
            raise ValueError("repair calls require an explicit budget")
        if not self.repairs.keys() <= self.calls.keys():
            raise ValueError("repair names an unknown stage")
        if self.finalization is not None:
            if (
                "calibration_task" not in self.finalization.intent
                or self.finalization.intent["calibration_task"] is not None
            ):
                raise ValueError(
                    "finalization must declare calibration_task=None for binding"
                )
            if self.finalization.intent.get("calibration_check") is not None:
                raise ValueError(
                    "task finalization is not an individual calibration check"
                )
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
            if stage.id in self.repairs:
                if stage.candidate_from is not None:
                    raise ValueError("repair requires a fixed initial stage context")
                repair = CalibrationCheckRequest.model_validate(
                    self.repairs[stage.id].call.intent.get("calibration_check")
                )
                if repair.scope == stage.check.scope:
                    raise ValueError(
                        "repair acceptance must have a distinct check scope"
                    )
                if (
                    repair.context != stage.check.context
                    or repair.setup != stage.check.setup
                    or repair.scope.targets != stage.check.scope.targets
                ):
                    raise ValueError("repair must retain the stage inputs and targets")
        return self


class CalibrationTaskControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    action: Literal["start", "pause", "cancel"]
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CalibrationTaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    specification: CalibrationTaskCreate
    attempts: dict[str, tuple[CalibrationStageAttempt, ...]] = Field(
        default_factory=dict
    )
    started_at: datetime | None = None
    stop_reason: Literal["repair_budget_exhausted", "deadline_elapsed"] | None = None
    created_at: datetime
    mode: Literal["manual", "running", "paused", "cancelled", "finished"] = "manual"
    control_revision: int = 1
    last_control: CalibrationTaskControl | None = None
    dispatch_errors: dict[str, str] = Field(default_factory=dict)
    finalization_run_id: str | None = None
    finalization_error: str | None = None

    @property
    def executions(self) -> dict[str, str]:
        return {
            key: values[-1].procedure_run_id for key, values in self.attempts.items()
        }

    @property
    def resolved_checks(self) -> dict[str, CalibrationCheckRequest]:
        return {key: values[-1].check for key, values in self.attempts.items()}

    @property
    def repairs_used(self) -> int:
        return sum(
            item.phase == "repair"
            for values in self.attempts.values()
            for item in values
        )

    @property
    def resolved_plan(self) -> CalibrationTaskPlan:
        """Overlay retained bindings; undispatched candidate stages remain templates."""
        resolved = self.resolved_checks
        return self.specification.plan.model_copy(
            update={
                "stages": tuple(
                    stage.model_copy(
                        update={"check": resolved.get(stage.id, stage.check)}
                    )
                    for stage in self.specification.plan.stages
                )
            }
        )


class CalibrationTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task: CalibrationTaskRecord
    progress: CalibrationTaskProgress
    finalization: ProcedureRun | None = None
    admission_deadline: datetime | None = None


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
