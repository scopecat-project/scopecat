"""Source-bound author convenience over the existing calibration task protocol."""

from __future__ import annotations

import time
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import httpx2

from scopecat.api.calibration_tasks import LabCalibrationTasks
from scopecat.automation.calibration_tasks import CalibrationTaskPlan
from scopecat.automation.models import ProcedureSource
from scopecat.daemon.calibration_tasks import (
    CalibrationRepairBudget,
    CalibrationStageRepair,
    CalibrationTaskCall,
    CalibrationTaskCreate,
    CalibrationTaskPage,
    CalibrationTaskView,
)
from scopecat.daemon.client import DaemonClient
from scopecat.kernel.errors import SessionClosedError


@dataclass(frozen=True, slots=True)
class AuthorCalibrationTasks:
    session: DaemonClient
    project_root: Path | None

    def prepare(
        self,
        task_id: str,
        plan: CalibrationTaskPlan,
        *,
        calls: dict[str, CalibrationTaskCall],
        finalization: CalibrationTaskCall | None = None,
        repairs: dict[str, CalibrationStageRepair] | None = None,
        repair_budget: CalibrationRepairBudget | None = None,
    ) -> PreparedAuthorCalibrationTask:
        """Capture one source for every stage, repair and finalization template.

        Build calls with task_call after selecting/reimporting the author source.
        References and normalized intents are checked against that retained source
        by the server, including after candidate/evidence binding. Session defaults
        do not rewrite scientific inputs. Preparing neither creates nor starts work.
        """
        from scopecat.application.author_imports import notebook_imports_selected

        if self.session.is_closed:
            raise SessionClosedError("Cannot prepare a task on a closed session")
        root = self.project_root
        if root is None:
            raise ValueError("Use project.authoring() or sc.notebook()")
        revision = self.session.current_author_source()
        if not notebook_imports_selected(root, revision):
            raise ValueError("Explicitly refresh the session and reimport task calls")
        command = CalibrationTaskCreate(
            task_id=task_id,
            plan=plan,
            calls=calls,
            finalization=finalization,
            repairs=repairs or {},
            repair_budget=repair_budget,
            source=ProcedureSource(
                workspace_id=self.session.workspace_id, code_revision=revision
            ),
        )
        health = self.session.health()
        return PreparedAuthorCalibrationTask(
            self.session,
            command.model_dump_json(),
            health.project_id,
            health.deployment_id,
        )

    def get(self, task_id: str) -> CalibrationTaskView:
        """Reconnect by durable ID; do not refresh source or advance execution."""
        return self.session.get_calibration_task(task_id)

    def list(
        self, *, limit: int = 50, cursor: int | None = None
    ) -> CalibrationTaskPage:
        return LabCalibrationTasks(self.session).list(limit=limit, cursor=cursor)

    def start(
        self, task: CalibrationTaskView, *, actor: str, reason: str
    ) -> CalibrationTaskView:
        """Explicitly start/continue; preserve the task's control revision fence."""
        return LabCalibrationTasks(self.session).start(task, actor=actor, reason=reason)

    def pause(
        self, task: CalibrationTaskView, *, actor: str, reason: str
    ) -> CalibrationTaskView:
        return LabCalibrationTasks(self.session).pause(task, actor=actor, reason=reason)

    def cancel(
        self, task: CalibrationTaskView, *, actor: str, reason: str
    ) -> CalibrationTaskView:
        """Stop future admission; already admitted procedures have separate controls."""
        return LabCalibrationTasks(self.session).cancel(
            task, actor=actor, reason=reason
        )

    def wait(
        self, task_id: str, *, timeout: float = 90, interval: float = 0.2
    ) -> CalibrationTaskView:
        """Observe until stopped, blocked on admission or needing human input.

        Finished is not scientific acceptance or publication. Timeout stops only
        observation. Reads never retry admission, start, resume or cancel work.
        """
        if (
            not isfinite(timeout)
            or timeout < 0
            or not isfinite(interval)
            or interval <= 0
        ):
            raise ValueError(
                "timeout must be finite and nonnegative; "
                "interval must be finite and positive"
            )
        deadline = time.monotonic() + timeout
        while True:
            try:
                view = self.session.get_calibration_task(
                    task_id, timeout=max(0.001, deadline - time.monotonic())
                )
            except httpx2.TimeoutException as error:
                raise TimeoutError("Wait ended; task was not cancelled") from error
            if (
                view.task.mode != "running"
                or view.task.dispatch_errors
                or view.task.finalization_error is not None
                or any(
                    stage.state in {"waiting_for_input", "attention_required"}
                    for stage in view.progress.stages
                )
                or (
                    view.finalization is not None
                    and view.finalization.state
                    in {"waiting_for_input", "attention_required"}
                )
            ):
                return view
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Wait ended; task was not cancelled")
            time.sleep(min(interval, remaining))


@dataclass(frozen=True, slots=True)
class PreparedAuthorCalibrationTask:
    session: DaemonClient
    _command_json: str
    project_id: str
    deployment_id: str

    @property
    def command(self) -> CalibrationTaskCreate:
        """A detached copy of the existing wire model; edits cannot alter retries."""
        return CalibrationTaskCreate.model_validate_json(self._command_json)

    def submit(self) -> CalibrationTaskView:
        """Save the exact task idempotently, without starting it or refreshing code.

        After an uncertain response, retry this prepared object with its original
        task ID. Call start explicitly after inspecting the saved specification.
        """
        self._require_application(self.session)
        return self.session.create_calibration_task(self.command)

    def reconnect(self, session: DaemonClient) -> PreparedAuthorCalibrationTask:
        source = self.command.source
        assert source is not None
        if session.workspace_id != source.workspace_id:
            raise ValueError("Prepared task belongs to another workspace")
        self._require_application(session)
        return PreparedAuthorCalibrationTask(
            session, self._command_json, self.project_id, self.deployment_id
        )

    def _require_application(self, session: DaemonClient) -> None:
        health = session.health()
        if (health.project_id, health.deployment_id) != (
            self.project_id,
            self.deployment_id,
        ):
            raise ValueError(
                "Prepared task belongs to another data store or deployment"
            )
