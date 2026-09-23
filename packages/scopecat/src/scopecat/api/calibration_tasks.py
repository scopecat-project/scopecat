"""Author-facing fixed tasks; acquisition remains owned by procedure workers."""

from scopecat.automation import RegisteredProcedure
from scopecat.automation.calibration_tasks import CalibrationTaskPlan
from scopecat.daemon.calibration_tasks import (
    CalibrationTaskCall,
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
