"""Domain query contract for admitted calibration check declarations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.automation import ProcedureRun
from scopecat.automation.calibration import CheckEvidence, CheckSelection
from scopecat.automation.calibration_tasks import CalibrationTaskPlan
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
    CalibrationScope,
)

MAX_CHECK_OBSERVATIONS = 2000


class CalibrationRequirement(BaseModel):
    """One explicitly requested capability; no inferred dependency closure."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    scope: CalibrationScope
    max_age: timedelta = Field(gt=timedelta(0))


class CalibrationReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    context: CalibrationContext
    requirements: tuple[CalibrationRequirement, ...] = Field(
        min_length=1, max_length=32
    )
    history_limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_requirements(self) -> CalibrationReportQuery:
        if len({item.id for item in self.requirements}) != len(self.requirements):
            raise ValueError("requirement IDs must be unique")
        if len(self.requirements) * self.history_limit > MAX_CHECK_OBSERVATIONS:
            raise ValueError("report history budget exceeds 2000 requests")
        return self


class CalibrationRequirementStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement: CalibrationRequirement
    selection: CheckSelection
    scanned: int
    unresolved_procedures: tuple[str, ...]
    incomplete_reasons: tuple[Literal["scan_limit", "unresolved_checks"], ...]


class CalibrationReport(BaseModel):
    """Advisory evidence snapshot for explicit requirements, not sample health."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: CalibrationContext
    observed_at: datetime
    items: tuple[CalibrationRequirementStatus, ...]


class CalibrationCheckQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: CalibrationScope | None = None
    context: CalibrationContext | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: int | None = Field(default=None, ge=1)


class CalibrationCheckView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution: ProcedureRun
    request: CalibrationCheckRequest
    evidence: CheckEvidence | None


class CalibrationCheckPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CalibrationCheckView, ...]
    next_cursor: int | None = None


class CalibrationCheckObservation(BaseModel):
    """Previously read execution revisions and the first filtered request."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: CalibrationScope | None = None
    context: CalibrationContext | None = None
    head: str | None = Field(default=None, min_length=1)
    revisions: dict[
        Annotated[str, Field(min_length=1)], Annotated[int, Field(ge=1)]
    ] = Field(
        max_length=MAX_CHECK_OBSERVATIONS,
    )


class CalibrationCheckObservationResult(BaseModel):
    """Comparison at one read snapshot, not permission for a later write."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    changed_procedures: tuple[str, ...]
    head_changed: bool


class CalibrationTaskPreview(BaseModel):
    """Read-only evaluation of a plan against explicitly selected executions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    plan: CalibrationTaskPlan
    executions: dict[str, Annotated[str, Field(min_length=1)]] = Field(
        default_factory=dict, max_length=256
    )

    @model_validator(mode="after")
    def validate_bindings(self) -> CalibrationTaskPreview:
        if not self.executions.keys() <= {stage.id for stage in self.plan.stages}:
            raise ValueError("execution binding names an unknown task stage")
        if len(set(self.executions.values())) != len(self.executions):
            raise ValueError("one execution cannot fulfill multiple task stages")
        return self
