"""Author-facing fixed tasks; acquisition remains owned by procedure workers."""

from typing import Literal

from scopecat.automation import RegisteredProcedure
from scopecat.automation.calibration_tasks import CalibrationTaskPlan
from scopecat.daemon.calibration_tasks import (
    CalibrationTaskCall,
    CalibrationTaskControl,
    CalibrationTaskCreate,
    CalibrationTaskDispatch,
    CalibrationTaskListQuery,
    CalibrationTaskPage,
    CalibrationTaskView,
)
from scopecat.daemon.client import DaemonClient
from scopecat.records.sample import SampleSelector


def task_call(
    definition: RegisteredProcedure,
    intent: object,
    *,
    samples: tuple[SampleSelector, ...] = (),
) -> CalibrationTaskCall:
    """Capture the same typed intent and definition identity used by procedures."""
    return CalibrationTaskCall(
        definition=definition.ref,
        intent=definition.encode_intent(intent),
        samples=samples,
    )


class LabCalibrationTasks:
    def __init__(self, client: DaemonClient) -> None:
        self._client = client

    def create(
        self,
        task_id: str,
        plan: CalibrationTaskPlan,
        *,
        calls: dict[str, CalibrationTaskCall],
    ) -> CalibrationTaskView:
        return self._client.create_calibration_task(
            CalibrationTaskCreate(task_id=task_id, plan=plan, calls=calls)
        )

    def get(self, task_id: str) -> CalibrationTaskView:
        return self._client.get_calibration_task(task_id)

    def list(
        self, *, limit: int = 50, cursor: int | None = None
    ) -> CalibrationTaskPage:
        return self._client.list_calibration_tasks(
            CalibrationTaskListQuery(limit=limit, cursor=cursor)
        )

    def dispatch(self, task_id: str, stage_id: str) -> CalibrationTaskView:
        """Admit a ready stage once; a registered procedure worker executes it."""
        return self._client.dispatch_calibration_task(
            CalibrationTaskDispatch(task_id=task_id, stage_id=stage_id)
        )

    def start(
        self, task: CalibrationTaskView, *, actor: str, reason: str
    ) -> CalibrationTaskView:
        """Start or resume automatic advancement and retry admission errors."""
        return self._control(task, "start", actor=actor, reason=reason)

    def pause(
        self, task: CalibrationTaskView, *, actor: str, reason: str
    ) -> CalibrationTaskView:
        """Stop admitting new stages; already admitted procedures may finish."""
        return self._control(task, "pause", actor=actor, reason=reason)

    def cancel(
        self, task: CalibrationTaskView, *, actor: str, reason: str
    ) -> CalibrationTaskView:
        """Permanently stop future stages; use procedure controls for admitted work."""
        return self._control(task, "cancel", actor=actor, reason=reason)

    def _control(
        self,
        task: CalibrationTaskView,
        action: Literal["start", "pause", "cancel"],
        *,
        actor: str,
        reason: str,
    ) -> CalibrationTaskView:
        return self._client.control_calibration_task(
            CalibrationTaskControl(
                task_id=task.task.specification.task_id,
                expected_revision=task.task.control_revision,
                action=action,
                actor=actor,
                reason=reason,
            )
        )
