"""Read-side composition of durable procedure effects and process observations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from scopecat.automation import (
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureStepAttemptListQuery,
    ProcedureStepAttemptPage,
    procedure_step_operation_id,
)
from scopecat.daemon.views import RunDetail

from scopecat_server.services.project_workers import ProcedureDispatchView

if TYPE_CHECKING:
    from scopecat_server.services.application import DaemonApplication
    from scopecat_server.services.project_workers import ProjectProcedureWorkers


class ProcedureChildRunView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step_key: str
    run: RunDetail


class ProcedureOperatorView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    procedure: ProcedureRun
    steps: ProcedureStepAttemptPage
    dispatch: ProcedureDispatchView
    current_step: ProcedureStepAttempt | None
    current_child: ProcedureChildRunView | None
    child_runs: tuple[ProcedureChildRunView, ...]
    dispatch_blocked_reason: str | None


def read_procedure_operator(
    application: DaemonApplication,
    workers: ProjectProcedureWorkers,
    procedure_id: str,
    *,
    cursor: int | None = None,
) -> ProcedureOperatorView:
    procedure = application.automation.get(procedure_id)
    current_step = application.automation.running_step(procedure_id)
    current_run = (
        application.runs.find_run_by_submission_id(
            procedure_step_operation_id(procedure_id, current_step.step_key)
        )
        if current_step is not None and current_step.operation == "run"
        else None
    )
    current_child = (
        ProcedureChildRunView(step_key=current_step.step_key, run=current_run)
        if current_step is not None and current_run is not None
        else None
    )
    steps = application.automation.step_attempts(
        procedure_id, ProcedureStepAttemptListQuery(limit=50, cursor=cursor)
    )
    children: list[ProcedureChildRunView] = []
    seen: set[str] = set()
    for step in steps.items:
        if step.operation != "run" or step.step_key in seen:
            continue
        seen.add(step.step_key)
        child = application.runs.find_run_by_submission_id(
            procedure_step_operation_id(procedure_id, step.step_key)
        )
        if child is not None:
            children.append(ProcedureChildRunView(step_key=step.step_key, run=child))
    dispatch = workers.snapshot(procedure_id)
    blocked = None
    if procedure.state != "ready":
        blocked = "Only a ready procedure can be dispatched."
    elif procedure.cancellation is not None:
        blocked = "Cancellation is requested; wait for the current step to settle."
    elif procedure.resource_wait is not None:
        blocked = "The child is waiting for resources held by another owner."
    elif dispatch.worker_running:
        blocked = "A worker is already handling this procedure."
    elif current_run is not None and (
        current_run.control.state == "attention_required"
        or (
            current_run.snapshot.outcome is not None
            and current_run.snapshot.outcome.certainty != "known"
        )
    ):
        blocked = (
            "A child run has an unknown outcome; "
            "inspect retained evidence before reconciliation."
        )
    return ProcedureOperatorView(
        procedure=procedure,
        steps=steps,
        dispatch=dispatch,
        current_step=current_step,
        current_child=current_child,
        child_runs=tuple(children),
        dispatch_blocked_reason=blocked,
    )
