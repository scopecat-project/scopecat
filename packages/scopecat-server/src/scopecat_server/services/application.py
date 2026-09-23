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
from scopecat.runtime_binding import load_runtime_binding

from scopecat_server.services.calibration_checks import CalibrationCheckQueries
from scopecat_server.services.calibration_profiles import CalibrationProfileService
from scopecat_server.services.calibration_tasks import CalibrationTaskService
from scopecat_server.storage.sqlite.apparatus_history import ApparatusHistoryStore
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.experimental_batches import ExperimentalBatchStore
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.record_collections import RecordCollectionStore
from scopecat_server.storage.sqlite.research_projects import ResearchProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore

from ..command_payloads import CommandPayloadService
from .admission import AdmissionService
from .analyses import AnalysisService
from .author_workspaces import AuthorWorkspaceServices
from .automation import AutomationService
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
from .setup import SetupService

if TYPE_CHECKING:
    from scopecat_server.instruments.service import InstrumentService


class DaemonApplication:
    """Composition root exposing narrow services to the transport."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        project_id: str,
        deployment_id: str,
        project_store: SQLiteProjectStore,
        config: ConfigService,
        setup: SetupService,
        analyses: AnalysisService,
        runs: RunService,
        admission: AdmissionService,
        executor: ExecutorService,
        instruments: InstrumentService,
        payloads: CommandPayloadService,
        lease_supervisor: OwnershipLeaseSupervisor,
        reviews: ReviewService,
        automation: AutomationService,
        procedure_schedules: ProcedureScheduleService,
        point_plans: RunPointPlanService,
        samples: SampleService,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.binding = load_runtime_binding(self.project_root)
        self.project_id = project_id
        self.deployment_id = deployment_id
        self._project_store = project_store
        self.author_workspaces = AuthorWorkspaceServices(
            self.project_root, project_store
        )
        self.author_revisions = self.author_workspaces.get("legacy")
        self.targets = TargetCatalogStore(
            project_store.sqlite, catalog_id=project_store.identity()
        )
        self.apparatus_history = ApparatusHistoryStore(
            project_store.sqlite,
            catalog_id=project_store.identity(),
            objects=project_store.objects,
        )
        self.plans = ExperimentPlanService(
            ExperimentPlanRepository(project_store),
            config=config,
            samples=samples,
            runs=runs,
            authors=self.author_workspaces,
            targets=self.targets,
        )
        self.config = config
        self.setup = setup
        self.manual_previews = ManualPreviewService(project_store.sqlite, config, runs)
        self.analyses = analyses
        self.runs = runs
        self._admission = admission
        self.executor = executor
        self.instruments = instruments
        self.payloads = payloads
        self.reviews = reviews
        self.automation = automation
        self.calibration_checks = CalibrationCheckQueries(
            project_store.sqlite,
            SQLiteRunRepository(project_store.sqlite, project_store.objects.root),
        )
        self.calibration_tasks = CalibrationTaskService(
            project_store.sqlite, automation, self.calibration_checks
        )
        self.calibration_profiles = CalibrationProfileService(
            project_store.sqlite, self.calibration_checks
        )

        self.procedure_schedules = procedure_schedules
        self.point_plans = point_plans
        self.samples = samples
        self.research = ResearchProjectStore(project_store.sqlite)
        self.record_collections = RecordCollectionStore(project_store.sqlite)
        self.experimental_batches = ExperimentalBatchStore(project_store.sqlite)
        self._lease_supervisor = lease_supervisor

    def start(self) -> None:
        self._lease_supervisor.start()

    def close(self) -> None:
        self.author_workspaces.close()
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
            deployment_id=self.deployment_id,
            project_name=self.project_root.name,
            author_workspaces=self.author_workspaces.roots,
            project_root=str(self.project_root),
            data_root=str(self.binding.data_root),
            deployment_root=str(self.binding.deployment_root),
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
