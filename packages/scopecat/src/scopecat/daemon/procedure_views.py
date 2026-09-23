"""Shared read-only procedure progress for Python and GUI consumers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from scopecat.automation.models import ProcedureRun, ProcedureStepAttempt
from scopecat.automation.wire import ProcedureStepAttemptPage
from scopecat.daemon.views import RunDetail


class ProcedureWorkerFailure(BaseModel):
    """Last process-management failure; separate from scientific outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["dispatch", "process_exit"]
    message: str
    observed_at: datetime
    exit_code: int | None = None


class ProcedureWorkerLog(BaseModel):
    """Bounded UTF-8 rendering of one execution's recent process output."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    available: bool
    text: str
    total_bytes: int
    truncated: bool


class ProcedureDispatchView(BaseModel):
    """Observation of existing process management, not execution authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    management: Literal["unmanaged", "active", "paused"]
    worker_running: bool
    failure: ProcedureWorkerFailure | None = None
    log_path: str | None = None


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

    @property
    def state(
        self,
    ) -> Literal[
        "succeeded",
        "failed",
        "cancelled",
        "cancelling",
        "attention_required",
        "waiting_for_input",
        "waiting_for_resources",
        "dispatch_paused",
        "waiting_for_acquisition",
        "acquiring",
        "settling",
        "running_step",
        "starting",
        "waiting_for_dispatch",
    ]:
        """Compact observed state; the durable details remain available."""
        if self.procedure.closure is not None:
            return self.procedure.closure.status
        if self.procedure.cancellation is not None:
            return "cancelling"
        if self.procedure.state == "attention_required":
            return "attention_required"
        if self.procedure.state == "waiting_for_input":
            return "waiting_for_input"
        child = self.current_child
        if child is not None and child.run.control.state == "attention_required":
            return "attention_required"
        if self.dispatch.management == "paused":
            return "dispatch_paused"
        if self.procedure.resource_wait is not None:
            return "waiting_for_resources"
        if child is not None:
            if child.run.control.state == "queued":
                return "waiting_for_acquisition"
            if child.run.control.state == "leased":
                return "acquiring"
            return "settling"
        if self.current_step is not None:
            return "running_step"
        return "starting" if self.dispatch.worker_running else "waiting_for_dispatch"
