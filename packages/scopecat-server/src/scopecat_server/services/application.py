"""Daemon application composition root."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from scopecat.daemon.health import DaemonHealth
from scopecat.daemon.wire import (
    AttentionResolutionCommand,
    AttentionResolutionReceipt,
    RunAdmission,
    RunCancellationReceipt,
    RunSubmission,
)

from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

from ..command_payloads import CommandPayloadService
from .admission import AdmissionService
from .analyses import AnalysisService
from .author_revisions import AuthorRevisionService
from .automation import AutomationService
from .calibration_cohorts import CalibrationCohortService
from .config import ConfigService
from .executor import ExecutorService
from .experiment_plans import ExperimentPlanService
from .leases import OwnershipLeaseSupervisor
from .manual_previews import ManualPreviewService
from .point_plans import RunPointPlanService
from .procedure_schedules import ProcedureScheduleService
from .reviews import ReviewService
from .runs import RunService
from .samples import SampleService

if TYPE_CHECKING:
    from scopecat_server.instruments.service import InstrumentService


class DaemonApplication:
    """Composition root exposing narrow services to the transport."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        project_id: str,
        project_store: SQLiteProjectStore,
        config: ConfigService,
        analyses: AnalysisService,
        runs: RunService,
        admission: AdmissionService,
        executor: ExecutorService,
        instruments: InstrumentService,
        payloads: CommandPayloadService,
        lease_supervisor: OwnershipLeaseSupervisor,
        reviews: ReviewService,
        automation: AutomationService,
        calibration_cohorts: CalibrationCohortService,
        procedure_schedules: ProcedureScheduleService,
        point_plans: RunPointPlanService,
        samples: SampleService,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.project_id = project_id
        self._project_store = project_store
        self.author_revisions = AuthorRevisionService(self.project_root, project_store)
        self.plans = ExperimentPlanService(
            ExperimentPlanRepository(project_store),
            config=config,
            samples=samples,
            runs=runs,
            authors=self.author_revisions,
        )
        self.config = config
        self.manual_previews = ManualPreviewService(project_store.sqlite, config)
        self.analyses = analyses
        self.runs = runs
        self._admission = admission
        self.executor = executor
        self.instruments = instruments
        self.payloads = payloads
        self.reviews = reviews
        self.automation = automation
        self.calibration_cohorts = calibration_cohorts
        self.procedure_schedules = procedure_schedules
        self.point_plans = point_plans
        self.samples = samples
        self._lease_supervisor = lease_supervisor

    def start(self) -> None:
        self._lease_supervisor.start()

    def close(self) -> None:
        self._lease_supervisor.request_stop()
        try:
            self.instruments.shutdown()
        finally:
            try:
                self.payloads.close()
            finally:
                try:
                    self._lease_supervisor.close()
                finally:
                    try:
                        self.executor.close()
                    finally:
                        self._project_store.close()

    def health(self) -> DaemonHealth:
        try:
            self._project_store.schema_version()
        except Exception:
            status: Literal["ok", "degraded"] = "degraded"
        else:
            status = (
                "ok"
                if self._lease_supervisor.healthy and self.instruments.healthy
                else "degraded"
            )
        return DaemonHealth(
            status=status,
            project_id=self.project_id,
            project_name=self.project_root.name,
            project_root=str(self.project_root),
        )

    def submit_run(self, submission: RunSubmission) -> RunAdmission:
        return self._admission.submit_run(submission)

    def cancel_run(self, run_id: str) -> RunCancellationReceipt:
        return self.executor.cancel_run(run_id)

    def resolve_attention(
        self,
        run_id: str,
        command: AttentionResolutionCommand,
    ) -> AttentionResolutionReceipt:
        return self.instruments.resolve_run_attention(
            run_id,
            lambda selected_run_id: self._admission.resolve_attention(
                selected_run_id,
                command,
            ),
        )
