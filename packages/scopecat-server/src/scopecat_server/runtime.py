"""Composition root for a local Scopecat daemon."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import Self
from uuid import UUID, uuid4

from fastapi import FastAPI
from filelock import FileLock, Timeout
from scopecat.application.bootstrap import BootstrapConfigFactory
from scopecat.author_workspaces import author_workspace_id
from scopecat.config.resolution import validate_config_profile
from scopecat.daemon.wire import (
    ConfigPublishCommand,
    DirectConfigRevisionSource,
)
from scopecat.project import load_bootstrap_factory
from scopecat.project_state import ProjectStateServices
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.runtime_binding import load_runtime_binding

from scopecat_server._startup_diagnostics import stage as startup_stage
from scopecat_server.command_payloads import CommandPayloadService
from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.services.active_measurements import ActiveMeasurementStore
from scopecat_server.services.admission import AdmissionService
from scopecat_server.services.analyses import AnalysisService
from scopecat_server.services.application import DaemonApplication
from scopecat_server.services.automation import AutomationService
from scopecat_server.services.calibration_cohorts import CalibrationCohortService
from scopecat_server.services.config import ConfigService
from scopecat_server.services.executor import ExecutorService
from scopecat_server.services.leases import OwnershipLeaseSupervisor
from scopecat_server.services.point_plans import RunPointPlanService
from scopecat_server.services.procedure_schedules import ProcedureScheduleService
from scopecat_server.services.resource_waits import ProcedureResourceWaits
from scopecat_server.services.reviews import ReviewService
from scopecat_server.services.runs import RunService
from scopecat_server.services.samples import SampleService
from scopecat_server.services.setup import SetupService
from scopecat_server.storage.sqlite.analysis_repository import SQLiteAnalysisRepository
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.calibration_cohorts import (
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.config_operations import SQLiteConfigOperationStore
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.procedure_schedules import (
    SQLiteProcedureScheduleStore,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository
from scopecat_server.storage.sqlite.samples import SQLiteSampleStore
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore

from .http.transport import create_app
from .instruments.actors import InstrumentActorRegistry
from .instruments.backend import InstrumentBackendEndpoint
from .instruments.service import InstrumentService
from .instruments.worker import SubprocessInstrumentBackendEndpoint

_DEFAULT_INSTRUMENT_SHUTDOWN_GRACE = timedelta(seconds=5)
_DEFAULT_INSTRUMENT_SESSION_LEASE_TTL = timedelta(seconds=90)


class LocalDaemonRuntime:
    """Own all process-scoped services for one project."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        bootstrap_config: ConfigProfileSnapshot | BootstrapConfigFactory | None = None,
        bootstrap_spec: str | None = None,
        instrument_backend_spec: str | None = None,
        instrument_endpoint: InstrumentBackendEndpoint | None = None,
        instrument_shutdown_grace: timedelta = _DEFAULT_INSTRUMENT_SHUTDOWN_GRACE,
        instrument_session_lease_ttl: timedelta = (
            _DEFAULT_INSTRUMENT_SESSION_LEASE_TTL
        ),
        lease_ttl: timedelta | None = None,
    ) -> None:
        self._close_lock = Lock()
        if instrument_backend_spec is not None and instrument_endpoint is not None:
            raise ValueError(
                "instrument_backend_spec and instrument_endpoint cannot be combined"
            )
        self.project_root = Path(project_root).resolve()
        if author_workspace_id(self.project_root) != "legacy":
            raise ValueError(
                "Start the deployment from its service workspace, not "
                "a registered author workspace"
            )
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.binding = load_runtime_binding(self.project_root)
        self.state_dir = self.binding.data_root
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        self.binding.deployment_root.mkdir(parents=True, exist_ok=True)
        self._deployment_lock = FileLock(
            self.binding.deployment_root / "deployment.lock"
        )
        self._owner_lock = FileLock(self.state_dir / "daemon.lock")
        try:
            # This lock establishes one process owner; SQLite remains the only
            # concurrency mechanism inside that process boundary.
            self._deployment_lock.acquire(timeout=0)
            self._owner_lock.acquire(timeout=0)
        except Timeout as error:
            self._deployment_lock.release()
            raise RuntimeError(
                "project already has a running daemon or deployment owner: "
                f"{self.project_root}"
            ) from error
        database = self.state_dir / "control.sqlite3"
        objects = self.state_dir / "objects"
        project_bootstrap: BootstrapConfigFactory | None = None
        sqlite: SQLiteDatabase | None = None

        try:
            startup_stage("initializing project store")
            sqlite = SQLiteDatabase(database)
            project_store = SQLiteProjectStore(sqlite, objects)
            project_store.bootstrap()
            deployment_file = self.binding.deployment_root / "deployment-id"
            if deployment_file.exists():
                deployment_id = str(
                    UUID(deployment_file.read_text(encoding="utf-8").strip())
                )
            else:
                deployment_id = str(uuid4())
                deployment_file.write_text(deployment_id + "\n", encoding="utf-8")

            if bootstrap_spec is not None:
                bootstrap = load_bootstrap_factory(
                    bootstrap_spec,
                    self.project_root,
                )(self.project_root)
                project_bootstrap = bootstrap.bootstrap_config
            if instrument_backend_spec is not None:
                instrument_endpoint = SubprocessInstrumentBackendEndpoint(
                    self.project_root,
                    instrument_backend_spec,
                )

            startup_stage("project store ready; composing services")
            control = SQLiteControlPlane(sqlite)
            automation_store = SQLiteAutomationStore(sqlite)
            calibration_cohort_store = SQLiteCalibrationCohortStore(sqlite)
            procedure_schedule_store = SQLiteProcedureScheduleStore(sqlite)
            runs = SQLiteRunRepository(sqlite, objects)
            analyses = SQLiteAnalysisRepository(sqlite, objects)
            sample_store = SQLiteSampleStore(sqlite, control=control)
            samples = SampleService(sample_store, project_store.objects)
            config_registry = SQLiteConfigRegistryStore(
                sqlite,
                runs=runs,
            )
            config_operations = SQLiteConfigOperationStore(sqlite)
            payloads = CommandPayloadService()

            services = ProjectStateServices(
                runs=runs,
                config_registry=config_registry.read_unit_of_work,
            )
            active_measurements = ActiveMeasurementStore()
            reviews = ReviewService()
            point_plans = RunPointPlanService(control=control, runs=runs)
            automation = AutomationService(
                automation_store,
                plans=ExperimentPlanRepository(project_store),
                runs=runs,
                resource_waits=ProcedureResourceWaits(control, runs, point_plans),
            )
            calibration_cohorts = CalibrationCohortService(
                calibration_cohort_store,
                automation,
                config_registry,
            )
            procedure_schedules = ProcedureScheduleService(
                procedure_schedule_store,
                automation,
            )
            instrument_actors = InstrumentActorRegistry()
            analysis_service = AnalysisService(
                repository=analyses,
                services=services,
                control=control,
                samples=samples,
            )
            config_service = ConfigService(
                samples=samples,
                control=control,
                config_registry=config_registry,
                config_operations=config_operations,
                runs=runs,
                services=services,
                analyses=analysis_service,
                automation=automation_store,
                calibration_cohorts=calibration_cohort_store,
            )
            setup_service = SetupService(
                control=control,
                config_registry=config_registry,
                actors=instrument_actors,
                calibration_cohorts=calibration_cohort_store,
            )
            run_service = RunService(
                control=control,
                runs=runs,
                services=services,
                active_measurements=active_measurements,
                point_plans=point_plans,
            )
            admission = AdmissionService(
                control=control,
                runs=runs,
                services=services,
                point_plans=point_plans,
                samples=samples,
                sample_store=sample_store,
                targets=TargetCatalogStore(sqlite, catalog_id=project_store.identity()),
                deployment_id=deployment_id,
            )

            def retain_measurements_before_fence(run_id: str, token: str) -> None:
                executor.retain_measurements_before_fence(run_id, token)

            instruments = InstrumentService(
                control=control,
                runs=runs,
                setup=setup_service,
                endpoint=instrument_endpoint,
                payloads=payloads,
                actors=instrument_actors,
                shutdown_grace_seconds=instrument_shutdown_grace.total_seconds(),
                session_lease_ttl=instrument_session_lease_ttl,
                retain_measurements_before_fence=retain_measurements_before_fence,
            )
            executor = ExecutorService(
                control=control,
                runs=runs,
                instruments=instruments,
                active_measurements=active_measurements,
                point_plans=point_plans,
                lease_ttl=lease_ttl,
            )
            project_id = project_store.identity()
            lease_supervisor = OwnershipLeaseSupervisor(
                instruments=instruments,
                executor=executor,
                shutdown_timeout_seconds=instrument_shutdown_grace.total_seconds(),
            )
            startup_stage("services composed; constructing daemon application")
            application = DaemonApplication(
                project_root=self.project_root,
                project_id=project_id,
                deployment_id=deployment_id,
                project_store=project_store,
                config=config_service,
                setup=setup_service,
                analyses=analysis_service,
                runs=run_service,
                admission=admission,
                executor=executor,
                instruments=instruments,
                payloads=payloads,
                lease_supervisor=lease_supervisor,
                reviews=reviews,
                automation=automation,
                calibration_cohorts=calibration_cohorts,
                procedure_schedules=procedure_schedules,
                point_plans=point_plans,
                samples=samples,
            )
            startup_stage("daemon application ready; bootstrapping config registry")
            try:
                bootstrap_source = (
                    bootstrap_config
                    if bootstrap_config is not None
                    else project_bootstrap
                )
                if bootstrap_source is not None:
                    _bootstrap_config_registry(
                        config_service,
                        bootstrap_source,
                        setup_service,
                    )
                startup_stage("config registry ready; starting application services")
                application.start()
                startup_stage("application services started")
            except BaseException:
                application.close()
                raise
            self.application = application
            self._closed = False
        except BaseException:
            if instrument_endpoint is not None:
                with suppress(Exception):
                    instrument_endpoint.shutdown()
            if sqlite is not None:
                with suppress(Exception):
                    sqlite.close()
            self._owner_lock.release()
            self._deployment_lock.release()
            raise

    def app(
        self,
        *,
        static_dir: str | Path | None = None,
        request_shutdown: Callable[[str], bool] | None = None,
    ) -> FastAPI:
        return create_app(
            self.application,
            static_dir=static_dir,
            request_shutdown=request_shutdown,
        )

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self.application.close()
            self._owner_lock.release()
            self._deployment_lock.release()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()


def _bootstrap_config_registry(
    config_service: ConfigService,
    config: ConfigProfileSnapshot | BootstrapConfigFactory,
    setup_service: SetupService,
) -> None:
    if config_service.get_config_registry().entries:
        try:
            setup_service.current()
            config_service.get_active_config()
        except BackendNotFound as error:
            raise BackendConflict(
                "existing registry has incomplete setup/default initialization"
            ) from error
        return
    if setup_service.list():
        raise BackendConflict(
            "initial parameter bootstrap requires an empty setup registry"
        )
    # Resolve application-owned inputs only for a genuinely empty registry.
    selected = config() if callable(config) else config
    validated = validate_config_profile(selected)
    digest = config_content_hash(validated).removeprefix("sha256:")
    entry_id = f"daemon-{digest}"
    config_service.publish_config(
        ConfigPublishCommand(
            operation_id=f"bootstrap-config:{entry_id}",
            source=DirectConfigRevisionSource(config=validated),
            entry_id=entry_id,
            actor="scopecat",
            expected_generation=0,
            note="imported while bootstrapping a new lab instance",
        )
    )


__all__ = ["LocalDaemonRuntime"]
