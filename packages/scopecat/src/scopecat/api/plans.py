"""Named immutable experiment plans, independent of execution and active config."""

from scopecat.daemon.client import DaemonClient
from scopecat.records.experiment_plan import (
    ExperimentPlanList,
    ExperimentPlanRevision,
    ExperimentPlanSave,
)
from scopecat.records.plan_ref import ExperimentPlanRef


class LabPlanOperations:
    def __init__(self, client: DaemonClient) -> None:
        self._client = client

    def list(self, *, plan_id: str | None = None) -> ExperimentPlanList:
        return self._client.experiment_plans(plan_id=plan_id)

    def get(self, ref: ExperimentPlanRef) -> ExperimentPlanRevision:
        return self._client.experiment_plan(ref)

    def save(self, command: ExperimentPlanSave) -> ExperimentPlanRevision:
        return self._client.save_experiment_plan(command)

    def delete(self, ref: ExperimentPlanRef) -> None:
        """Hide the current head; exact revision references remain readable."""
        self._client.hide_experiment_plan(ref)
