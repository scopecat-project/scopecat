"""Domain query contract for admitted calibration check declarations."""

from __future__ import annotations

from datetime import datetime, timedelta
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.automation import ProcedureRun
from scopecat.automation.calibration import (
    CapabilityAvailability,
    CheckEvidence,
    CheckSelection,
)
from scopecat.automation.calibration_tasks import CalibrationTaskPlan
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
    CalibrationScope,
)
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.sample import SampleSelector
from scopecat.records.setup import SetupRevisionRef
from scopecat.records.target_catalog import TargetRevisionRef

MAX_CHECK_OBSERVATIONS = 2000


class CalibrationContextResolve(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    branch: str = Field(min_length=1)
    setup: SetupRevisionRef | None = None
    samples: tuple[SampleSelector, ...] = Field(default=(), max_length=32)
    target: TargetRevisionRef | None = None

    @model_validator(mode="after")
    def exact_samples(self) -> CalibrationContextResolve:
        if self.target is not None and self.samples:
            raise ValueError("choose a registered target or inline samples, not both")
        if any(sample.revision is None for sample in self.samples):
            raise ValueError("select exact sample revisions for a capability context")
        return self


class CalibrationContextResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    context: CalibrationContext
    branch: ParameterBranch
    setup: SetupRevisionRef


class CalibrationRequirement(BaseModel):
    """One explicitly requested capability; no inferred dependency closure."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    scope: CalibrationScope
    max_age: timedelta = Field(gt=timedelta(0))
    depends_on: tuple[str, ...] = ()


class CalibrationRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirements: tuple[CalibrationRequirement, ...] = Field(
        min_length=1, max_length=32
    )

    @model_validator(mode="after")
    def validate_requirements(self) -> CalibrationRequirements:
        ids = {item.id for item in self.requirements}
        if len(ids) != len(self.requirements):
            raise ValueError("requirement IDs must be unique")
        for item in self.requirements:
            if len(set(item.depends_on)) != len(item.depends_on):
                raise ValueError("requirement dependencies must be unique")
            if not set(item.depends_on) <= ids:
                raise ValueError("requirement dependency names an unknown requirement")
        try:
            tuple(
                TopologicalSorter(
                    {item.id: item.depends_on for item in self.requirements}
                ).static_order()
            )
        except CycleError as error:
            raise ValueError("requirement dependencies must be acyclic") from error
        return self


class CalibrationProfile(CalibrationRequirements):
    """Immutable named requirements, evaluated against a separately chosen context."""

    id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    description: str = Field(default="", max_length=4000)


class CalibrationProfileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile: CalibrationProfile
    created_at: datetime


class CalibrationProfilePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[CalibrationProfileRecord, ...]
    next_cursor: int | None = None


class CalibrationProfileReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    context: CalibrationContext
    history_limit: int = Field(default=50, ge=1, le=200)


class CalibrationReportQuery(CalibrationRequirements):
    context: CalibrationContext
    history_limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_budget(self) -> CalibrationReportQuery:
        if len(self.requirements) * self.history_limit > MAX_CHECK_OBSERVATIONS:
            raise ValueError("report history budget exceeds 2000 requests")
        return self


class CalibrationRequirementStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement: CalibrationRequirement
    selection: CheckSelection
    availability: CapabilityAvailability
    scanned: int
    unresolved_procedures: tuple[str, ...]
    incomplete_reasons: tuple[Literal["scan_limit", "unresolved_checks"], ...]


class CalibrationReport(BaseModel):
    """Advisory evidence snapshot for explicit requirements, not sample health."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: CalibrationContext
    observed_at: datetime
    items: tuple[CalibrationRequirementStatus, ...]
    profile_id: str | None = None


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
