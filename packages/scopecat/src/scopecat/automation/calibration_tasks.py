"""Bounded stage plans and progress projections, independent of dispatch policy."""

from __future__ import annotations

from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.automation.calibration import CheckEvidence
from scopecat.automation.models import ProcedureRun
from scopecat.records.calibration_check import CalibrationCheckRequest


class CalibrationTaskStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    check: CalibrationCheckRequest
    depends_on: tuple[str, ...] = ()


class CalibrationTaskPlan(BaseModel):
    """Explicit target-expanded checks; every stage retains its own context."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    stages: tuple[CalibrationTaskStage, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_dependencies(self) -> CalibrationTaskPlan:
        ids = {stage.id for stage in self.stages}
        if len(ids) != len(self.stages):
            raise ValueError("task stage IDs must be unique")
        for stage in self.stages:
            if len(stage.depends_on) != len(set(stage.depends_on)):
                raise ValueError("task stage dependencies must be unique")
            if not set(stage.depends_on) <= ids:
                raise ValueError("task dependency names an unknown stage")
        try:
            tuple(
                TopologicalSorter(
                    {stage.id: stage.depends_on for stage in self.stages}
                ).static_order()
            )
        except CycleError as error:
            raise ValueError("task stage dependencies must be acyclic") from error
        return self


type CalibrationStageState = Literal[
    "ready",
    "waiting",
    "blocked",
    "queued",
    "running",
    "attention_required",
    "waiting_for_input",
    "passed",
    "rejected",
    "failed",
    "cancelled",
    "incomplete",
]
_FAILED = frozenset({"rejected", "failed", "cancelled", "incomplete", "blocked"})
_TERMINAL = _FAILED | {"passed"}


class CalibrationStageProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    state: CalibrationStageState
    procedure_run_id: str | None = None
    blocked_by: tuple[str, ...] = ()
    evidence: CheckEvidence | None = None


class CalibrationTaskProgress(BaseModel):
    """Observed execution progress; not scientific readiness or dispatch authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    stages: tuple[CalibrationStageProgress, ...]
    ready: tuple[str, ...]
    complete: bool
    successful: bool


def assess_calibration_task(
    plan: CalibrationTaskPlan,
    executions: Mapping[str, tuple[ProcedureRun, CheckEvidence | None]],
) -> CalibrationTaskProgress:
    """Evaluate dependencies using exact executions already validated by the server."""
    by_id = {stage.id: stage for stage in plan.stages}
    progress: dict[str, CalibrationStageProgress] = {}
    for stage_id in TopologicalSorter(
        {stage.id: stage.depends_on for stage in plan.stages}
    ).static_order():
        stage = by_id[stage_id]
        bound = executions.get(stage_id)
        if bound is not None:
            run, evidence = bound
            state = _execution_state(run, evidence)
            progress[stage_id] = CalibrationStageProgress(
                id=stage_id,
                state=state,
                procedure_run_id=run.procedure_run_id,
                evidence=evidence,
            )
            continue
        unavailable = tuple(
            key for key in stage.depends_on if progress[key].state != "passed"
        )
        state: CalibrationStageState = "ready"
        if unavailable:
            state = (
                "blocked"
                if any(progress[key].state in _FAILED for key in unavailable)
                else "waiting"
            )
        progress[stage_id] = CalibrationStageProgress(
            id=stage_id, state=state, blocked_by=unavailable
        )
    return CalibrationTaskProgress(
        stages=tuple(progress[stage.id] for stage in plan.stages),
        ready=tuple(
            stage.id for stage in plan.stages if progress[stage.id].state == "ready"
        ),
        complete=all(item.state in _TERMINAL for item in progress.values()),
        successful=all(item.state == "passed" for item in progress.values()),
    )


def _execution_state(
    run: ProcedureRun, evidence: CheckEvidence | None
) -> CalibrationStageState:
    if run.closure is not None:
        if run.closure.status == "cancelled":
            return "cancelled"
        if (
            evidence is not None
            and evidence.measurement.status == "completed"
            and evidence.passed is False
        ):
            return "rejected"
        if run.closure.status == "failed":
            return "failed"
        if (
            evidence is None
            or evidence.measurement.status != "completed"
            or evidence.passed is None
        ):
            return "incomplete"
        return "passed"
    if run.state == "attention_required":
        return "attention_required"
    if run.state == "waiting_for_input":
        return "waiting_for_input"
    return "queued" if run.state == "ready" else "running"
