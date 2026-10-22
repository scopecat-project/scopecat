"""Read line progress from durable execution receipts; never dispatch on reading."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict
from scopecat.automation.models import ProcedureRun
from scopecat.automation.wire import procedure_step_operation_id
from scopecat.daemon.views import RunDetail
from scopecat.records.manual_line import ManualLineSelection, PhysicalLine
from scopecat.records.manual_line_scan import (
    ManualLineScan,
    ManualLineScanCreate,
)

from scopecat_server.storage.sqlite.manual_lines import (
    ManualLineStore,
    unresolved_line_result,
)

if TYPE_CHECKING:
    from .experiment_plans import ExperimentPlanService
    from .runs import RunService


class ManualLineAttemptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    selection: ManualLineSelection
    procedure: ProcedureRun
    run: RunDetail | None


class PhysicalLineView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    line: PhysicalLine
    state: Literal[
        "not_started",
        "completed",
        "in_progress",
        "uncertain",
        "incomplete",
        "confirmation_required",
    ]
    attempts: tuple[ManualLineAttemptView, ...]


class ManualLineScanView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scan: ManualLineScan
    lines: tuple[PhysicalLineView, ...]
    active_procedure_id: str | None
    blocked_reason: str | None


class ManualLineScanPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[ManualLineScan, ...]


class ManualLineService:
    def __init__(
        self, store: ManualLineStore, plans: ExperimentPlanService, runs: RunService
    ) -> None:
        self.store = store
        self.plans = plans
        self.runs = runs

    def create(self, command: ManualLineScanCreate) -> ManualLineScan:
        self.plans.validate_definition(command.template)
        return self.store.create(command)

    def get(self, scan_id: str) -> ManualLineScanView:
        scan = self.store.get(scan_id)
        with self.store.store.sqlite.read_connection() as connection:
            procedures = self.store.attempts_in_transaction(connection, scan_id)
        attempts = tuple(
            ManualLineAttemptView(
                selection=ManualLineSelection.model_validate(
                    procedure.intent["manual_line"]
                ),
                procedure=procedure,
                run=self.runs.find_run_by_submission_id(
                    procedure_step_operation_id(
                        procedure.procedure_run_id, "experiment"
                    )
                ),
            )
            for procedure in procedures
        )
        views: list[PhysicalLineView] = []
        for line in scan.lines:
            history = tuple(
                item for item in attempts if item.selection.line_id == line.line_id
            )
            state: Literal[
                "not_started",
                "completed",
                "in_progress",
                "uncertain",
                "incomplete",
                "confirmation_required",
            ] = "not_started"
            if history:
                state = "incomplete"
                if any(
                    item.run
                    and item.run.snapshot.outcome
                    and item.run.snapshot.outcome.result == "succeeded"
                    and item.run.snapshot.outcome.certainty == "known"
                    for item in history
                ):
                    state = "completed"
                if any(item.procedure.state != "closed" for item in history):
                    state = "in_progress"
                if any(
                    item.run
                    and (
                        (
                            item.run.control.state == "attention_required"
                            and item.run.control.attention_reason
                            != "manual_line_confirmation_expired"
                        )
                        or (
                            item.run.snapshot.outcome
                            and item.run.snapshot.outcome.certainty != "known"
                        )
                    )
                    for item in history
                ):
                    state = "uncertain"
            if state != "uncertain" and any(
                item.run
                and item.run.control.state == "attention_required"
                and item.run.control.attention_reason
                == "manual_line_confirmation_expired"
                for item in history
            ):
                state = "confirmation_required"
            views.append(PhysicalLineView(line=line, state=state, attempts=history))
        return ManualLineScanView(
            blocked_reason=(
                "Finish or reconcile the current line attempt first"
                if any(
                    item.procedure.state != "closed"
                    or (
                        item.run is not None
                        and unresolved_line_result(
                            item.run.control.state, item.run.snapshot.outcome
                        )
                    )
                    for item in attempts
                )
                else None
            ),
            scan=scan,
            lines=tuple(views),
            active_procedure_id=next(
                (
                    item.procedure.procedure_run_id
                    for item in attempts
                    if item.procedure.state != "closed"
                ),
                None,
            ),
        )
