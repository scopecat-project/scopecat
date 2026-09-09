# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
"""FastAPI boundary for a daemon application service."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast, override

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as ApiPath
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from scopecat.application.comparison import ComparisonResult
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchPreview,
    LaunchRequest,
    LaunchSubmission,
)
from scopecat.automation import (
    ProcedureCancelCommand,
    ProcedureCancelReceipt,
    ProcedureCloseCommand,
    ProcedureCloseReceipt,
    ProcedureRun,
    ProcedureRunAttentionCommand,
    ProcedureRunAttentionReceipt,
    ProcedureRunListQuery,
    ProcedureRunnablePage,
    ProcedureRunnableQuery,
    ProcedureRunPage,
    ProcedureRunState,
    ProcedureSchedule,
    ProcedureScheduleCancelCommand,
    ProcedureScheduleCancelReceipt,
    ProcedureScheduleCreateCommand,
    ProcedureScheduleCreateReceipt,
    ProcedureScheduleDuePage,
    ProcedureScheduleDueQuery,
    ProcedureScheduleListQuery,
    ProcedureScheduleMaterializeCommand,
    ProcedureScheduleMaterializeReceipt,
    ProcedureSchedulePage,
    ProcedureScheduleState,
    ProcedureStepAttemptListQuery,
    ProcedureStepAttemptPage,
    ProcedureStepAttentionCommand,
    ProcedureStepAttentionReceipt,
    ProcedureStepAttentionRetryCommand,
    ProcedureStepAttentionRetryReceipt,
    ProcedureStepBeginCommand,
    ProcedureStepBeginReceipt,
    ProcedureStepCompleteCommand,
    ProcedureStepCompleteReceipt,
    ProcedureStepFailCommand,
    ProcedureStepFailReceipt,
    ProcedureStepInputSubmitCommand,
    ProcedureStepInputSubmitReceipt,
    ProcedureStepInputWaitCommand,
    ProcedureStepInputWaitReceipt,
    ProcedureSubmitCommand,
    ProcedureSubmitReceipt,
    ProcedureWorkerLeaseAcquireCommand,
    ProcedureWorkerLeaseAcquireReceipt,
    ProcedureWorkerLeaseHeartbeatCommand,
    ProcedureWorkerLeaseHeartbeatReceipt,
    ProcedureWorkerLeaseReleaseCommand,
    ProcedureWorkerLeaseReleaseReceipt,
)
from scopecat.automation.calibration_wire import (
    CalibrationCohortCreateCommand,
    CalibrationCohortCreateReceipt,
    CalibrationCohortGetQuery,
    CalibrationCohortGetReceipt,
    CalibrationCohortListQuery,
    CalibrationCohortMemberListQuery,
    CalibrationCohortMemberPage,
    CalibrationCohortPage,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationAttentionReceipt,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationDeferReceipt,
    CalibrationPublicationGetQuery,
    CalibrationPublicationGetReceipt,
    CalibrationPublicationReadyPage,
    CalibrationPublicationReadyQuery,
    CalibrationPublicationRetryCommand,
    CalibrationPublicationRetryReceipt,
    CalibrationStatusQuery,
    CalibrationStatusReceipt,
)
from scopecat.automation.wire import (
    ProcedureStepResourceWaitCommand,
    ProcedureStepResourceWaitReceipt,
)
from scopecat.config.structure import (
    ParameterStructurePlan,
    ParameterStructurePreview,
)
from scopecat.control.models import (
    ControlRunState,
    EventPage,
    RunExecutionSegmentPage,
)
from scopecat.daemon.endpoint import (
    DAEMON_SHUTDOWN_PATH,
    DAEMON_SHUTDOWN_TOKEN_HEADER,
)
from scopecat.daemon.hardware_receipt_wire import (
    HARDWARE_RECEIPT_MEDIA_TYPE,
    encode_collect_receipt,
    encode_run_hardware_receipt,
)
from scopecat.daemon.points import (
    ResolvedRunDomainView,
    RunDomainDecisionCommand,
    RunDomainDecisionPage,
    RunDomainDecisionView,
    RunDomainEnqueueCommand,
    RunDomainQueueEntryView,
    RunDomainQueueView,
    RunDomainResolveCommand,
    RunPointPlanCloseCommand,
    RunPointPlanView,
)
from scopecat.daemon.reviews import (
    ReviewCompileCommand,
    ReviewCompileReceipt,
    ReviewCompletionCommand,
    ReviewHeartbeatReceipt,
    ReviewSessionCloseReceipt,
    ReviewSessionCreateCommand,
    ReviewSessionListView,
    ReviewSessionView,
    ReviewWorkItem,
)
from scopecat.daemon.views import (
    ActiveConfigView,
    AnalysisContentBytesView,
    ConfigActivationPage,
    ConfigContextResolution,
    ConfigDraftPreview,
    ConfigEntryView,
    ConfigRegistryPage,
    DaemonHealth,
    InstrumentListView,
    InstrumentView,
    MeasurementArrowQuery,
    MeasurementPreview,
    MeasurementSlice,
    MeasurementSliceQuery,
    MeasurementTracePreview,
    MeasurementTracePreviewQuery,
    ParameterProposalPage,
    ParameterProposalView,
    ProjectAnalysisContentPage,
    ProjectAnalysisPage,
    ProjectAnalysisView,
    RunAnalysisPage,
    RunAnalysisView,
    RunArtifactBytesView,
    RunConfigView,
    RunContentPage,
    RunDatasetBytesView,
    RunDetail,
    RunFailureEvidence,
    RunRequestView,
    RunSummaryPage,
    SampleAnalysisPage,
    SampleAnalysisView,
    SamplePage,
    SampleRevisionPage,
    SampleView,
)
from scopecat.daemon.wire import (
    AnalysisSaveCommand,
    AnalysisSaveReceipt,
    AttentionResolutionCommand,
    AttentionResolutionReceipt,
    CalibrationPublicationCommand,
    CalibrationPublicationReceipt,
    ConfigActivationReceipt,
    ConfigContextResolveCommand,
    ConfigContextSaveCommand,
    ConfigDraftCommand,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    ExecutorHeartbeat,
    ExecutorLease,
    ExecutorStartRequest,
    InstrumentConfiguredDefaultsApplyCommand,
    InstrumentContractCatalogRequest,
    InstrumentDriverProbeCommand,
    InstrumentDriverProbeReceipt,
    InstrumentInventoryMigrationCommand,
    InstrumentInventoryMigrationReceipt,
    InstrumentReleaseCommand,
    InstrumentReleaseReceipt,
    InstrumentSessionEndReceipt,
    InstrumentSessionLeaseReceipt,
    InstrumentSessionOpenCommand,
    InstrumentSessionOpenReceipt,
    MeasurementFlushCommand,
    MeasurementFlushReceipt,
    MeasurementHeaderCommand,
    MeasurementIngestReceipt,
    MeasurementSealCommand,
    PayloadObjectReceipt,
    RunAdmission,
    RunAttachmentCommand,
    RunCancellationReceipt,
    RunCancellationState,
    RunCoverageAdvanceCommand,
    RunCoverageState,
    RunDomainJobStatePage,
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionBatchReceipt,
    RunDomainJobTransitionPage,
    RunHardwareBatchCommand,
    RunHardwareFinishCommand,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
    RunRecoveryGroupCommitCommand,
    RunRecoveryGroupCommitReceipt,
    RunRecoveryGroupPage,
    RunSubmission,
    SampleCreateCommand,
    SampleMutationReceipt,
    SampleReviseCommand,
    TerminalRunCommitCommand,
)
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorRefreshRequest,
    AuthorRevisionBundle,
    AuthorRevisionRef,
    AuthorRevisionState,
)
from scopecat.records.comparison import ComparisonRequest
from scopecat.records.content import ContentEntry
from scopecat.records.costs import RunMeasuredCosts
from scopecat.records.instrument import (
    InstrumentStateCacheReadback,
    InstrumentStateReadback,
    InstrumentStateSnapshot,
)
from scopecat.records.measurement_recording import MeasurementDatasetReceipt
from scopecat.records.run import RunSnapshot
from scopecat.records.sample import SampleArtifactRef, SampleId, SampleRevision
from scopecat.records.sample_artifact import (
    MAX_SAMPLE_ARTIFACT_BYTES,
    SampleArtifactPage,
)
from scopecat.runs.data import (
    RunArtifactJsonResult,
    RunArtifactTextResult,
    RunMeasurementDatasetResult,
    RunRecordJsonResult,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.commands import (
    ApplyReceipt,
    CollectReceipt,
    InstrumentConfiguredDefaultsApplyReceipt,
    InstrumentStateCommand,
    InstrumentStateReadCommand,
    InteractiveCollectIntent,
    InvokeCommand,
    InvokeReceipt,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareBatchReceipt,
    RunHardwareFinalizationReceipt,
)
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scopecat_server.http.procedure_operator import (
    ProcedureOperatorView,
    read_procedure_operator,
)
from scopecat_server.services.project_workers import ProjectProcedureWorkers
from scopecat_server.storage.sqlite.author_revision_repository import (
    AuthorRevisionConflict,
)
from scopecat_server.storage.sqlite.connection import SQLiteBusyError

from ..command_payloads import (
    CommandPayloadError,
    CommandPayloadTooLarge,
    run_payload_scope,
    session_payload_scope,
)
from ..errors import BackendConflict, BackendNotFound
from ..services.application import DaemonApplication

_API_PREFIX = "/api/v1"
_ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"
_ARROW_FILE_MEDIA_TYPE = "application/vnd.apache.arrow.file"
_MEASUREMENT_ACTIVE_HEADER = "X-Scopecat-Measurement-Active"
_MEASUREMENT_DURABLE_COUNT_HEADER = "X-Scopecat-Durable-Record-Count"
_MEASUREMENT_RECEIVED_COUNT_HEADER = "X-Scopecat-Received-Record-Count"
_NEXT_OFFSET_HEADER = "X-Scopecat-Next-Offset"
_SNAPSHOT_SIZE_HEADER = "X-Scopecat-Snapshot-Size"
_SSE_PAGE_SIZE = 100
_SSE_POLL_SECONDS = 0.5
DEFAULT_MAX_COMMAND_BODY_BYTES = 8 * 1024 * 1024


def create_app(  # noqa: C901 - route registration is intentionally centralized
    application: DaemonApplication,
    static_dir: str | Path | None = None,
    *,
    max_command_body_bytes: int = DEFAULT_MAX_COMMAND_BODY_BYTES,
    request_shutdown: Callable[[str], bool] | None = None,
) -> FastAPI:
    """Create transport routes around an already-composed daemon application."""

    project_workers = ProjectProcedureWorkers(
        lambda: application.project_root,
        lambda procedure_id: application.automation.worker_state(procedure_id),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        project_workers.start()
        try:
            yield
        finally:
            project_workers.stop()

    app = FastAPI(title="Scopecat daemon", version="1", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )
    app.add_middleware(
        _CommandBodyLimitMiddleware,
        max_body_bytes=max_command_body_bytes,
    )
    _install_error_mapping(app)

    @app.get(f"{_API_PREFIX}/author-revisions")
    def author_revision_state() -> AuthorRevisionState:
        try:
            return application.author_revisions.state()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get(f"{_API_PREFIX}/author-revisions/{{content_hash}}")
    def author_revision(content_hash: str) -> AuthorRevisionBundle:
        try:
            return application.author_revisions.get(
                AuthorRevisionRef(content_hash=content_hash)
            )
        except KeyError as error:
            raise HTTPException(404, "Author revision not found") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post(f"{_API_PREFIX}/author-revisions/refresh")
    def refresh_author_revision(command: AuthorRefreshRequest) -> AuthorRevisionState:
        try:
            return application.author_revisions.refresh(
                expected_generation=command.expected_generation
            )
        except AuthorRevisionConflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post(f"{_API_PREFIX}/author-revisions/analyze")
    def analyze_author_revision(
        command: AuthorAnalysisRequest,
    ) -> AuthorAnalysisReceipt:
        completed = subprocess.run(  # noqa: S603 - internal revision-pinned analysis worker
            [
                sys.executable,
                "-m",
                "scopecat_server.author_worker",
                str(application.project_root),
                "--analyze",
            ],
            input=command.model_dump_json(),
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            raise HTTPException(
                422, completed.stderr.strip() or "Author analysis failed"
            )
        return AuthorAnalysisReceipt.model_validate_json(completed.stdout)

    def launch_call(command: LaunchRequest) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed project worker command
                [
                    sys.executable,
                    "-m",
                    "scopecat_server.launch_worker",
                    str(application.project_root),
                ],
                input=command.model_dump_json(),
                capture_output=True,
                encoding="utf-8",
                timeout=60,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise HTTPException(504, "Experiment preview timed out") from error
        if completed.returncode:
            detail = completed.stderr.strip().splitlines()
            raise HTTPException(
                422, detail[-1] if detail else "Experiment preview failed"
            )
        return completed.stdout

    @app.post(f"{_API_PREFIX}/run-comparison")
    def run_comparison(command: ComparisonRequest) -> ComparisonResult:
        from pydantic import TypeAdapter

        try:
            completed = subprocess.run(  # noqa: S603 - fixed project worker command
                [
                    sys.executable,
                    "-m",
                    "scopecat_server.comparison_worker",
                    str(application.project_root),
                ],
                input=command.model_dump_json(),
                capture_output=True,
                encoding="utf-8",
                timeout=60,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise HTTPException(
                504, "Comparison timed out; inspect history before retrying"
            ) from error
        if completed.returncode:
            lines = completed.stderr.strip().splitlines()
            raise HTTPException(422, lines[-1] if lines else "Comparison failed")
        return TypeAdapter(ComparisonResult).validate_json(completed.stdout)

    @app.get(f"{_API_PREFIX}/experiment-launcher")
    def experiment_launch_catalog() -> LaunchCatalog:
        return LaunchCatalog.model_validate_json(
            launch_call(LaunchRequest(action="list"))
        )

    @app.post(f"{_API_PREFIX}/experiment-launcher/preview")
    def experiment_launch_preview(command: LaunchRequest) -> LaunchPreview:
        if command.action != "preview":
            raise HTTPException(422, "Expected preview action")
        return LaunchPreview.model_validate_json(launch_call(command))

    def dispatch_procedure(
        procedure_id: str, *, explicit: bool = False
    ) -> LaunchSubmission:
        view = read_procedure_operator(application, project_workers, procedure_id)
        if view.dispatch_blocked_reason is not None:
            if explicit:
                raise HTTPException(409, view.dispatch_blocked_reason)
            # Exact admission retries retain their identity without redispatching
            # a settled, active, waiting or uncertain effect.
            return LaunchSubmission(procedure_id=procedure_id)
        try:
            project_workers.dispatch(procedure_id)
        except OSError as error:
            return LaunchSubmission(
                procedure_id=procedure_id, dispatch_error=str(error)
            )
        return LaunchSubmission(procedure_id=procedure_id)

    @app.post(f"{_API_PREFIX}/experiment-launcher/submit")
    def experiment_launch_submit(command: LaunchRequest) -> LaunchSubmission:
        if command.action != "submit" or not command.request_key.strip():
            raise HTTPException(422, "Submit requires a request key")
        admitted = LaunchSubmission.model_validate_json(launch_call(command))
        return dispatch_procedure(admitted.procedure_id)

    @app.post(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/dispatch")
    def dispatch_project_procedure(procedure_run_id: str) -> LaunchSubmission:
        return dispatch_procedure(procedure_run_id, explicit=True)

    @app.get(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/operator")
    def get_procedure_operator(
        procedure_run_id: str,
        cursor: Annotated[int | None, Query(ge=1)] = None,
    ) -> ProcedureOperatorView:
        return read_procedure_operator(
            application, project_workers, procedure_run_id, cursor=cursor
        )

    @app.get(f"{_API_PREFIX}/health")
    def health() -> DaemonHealth:
        return application.health()

    if request_shutdown is not None:

        @app.post(DAEMON_SHUTDOWN_PATH, include_in_schema=False, status_code=202)
        def shutdown_daemon(
            token: Annotated[
                str,
                Header(alias=DAEMON_SHUTDOWN_TOKEN_HEADER, min_length=1),
            ],
        ) -> None:
            if not request_shutdown(token):
                raise HTTPException(status_code=403, detail="invalid shutdown token")

    @app.put(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/"
        "payload-objects/{hexdigest}",
        status_code=201,
    )
    async def put_session_payload_object(
        session_id: str,
        hexdigest: Annotated[str, ApiPath(pattern=r"^[0-9a-f]{64}$")],
        request: Request,
        command_id: Annotated[
            str,
            Header(alias="X-Scopecat-Payload-Command-ID", min_length=1),
        ],
    ) -> PayloadObjectReceipt:
        application.instruments.authorize_session_payload_upload(session_id)
        return await application.payloads.put_object_stream(
            request.stream(),
            scope=session_payload_scope(session_id, command_id),
            expected_content_hash=f"sha256:{hexdigest}",
            declared_size_bytes=_request_content_length(request),
        )

    @app.put(
        f"{_API_PREFIX}/runs/{{run_id}}/payload-objects/{{hexdigest}}",
        status_code=201,
    )
    async def put_run_payload_object(
        run_id: str,
        hexdigest: Annotated[str, ApiPath(pattern=r"^[0-9a-f]{64}$")],
        request: Request,
        lease_id: Annotated[
            str,
            Header(alias="X-Scopecat-Lease-ID", min_length=1),
        ],
        operation_id: Annotated[
            str,
            Header(alias="X-Scopecat-Payload-Operation-ID", min_length=1),
        ],
    ) -> PayloadObjectReceipt:
        application.instruments.authorize_run_payload_upload(run_id, lease_id)
        return await application.payloads.put_object_stream(
            request.stream(),
            scope=run_payload_scope(run_id, operation_id),
            expected_content_hash=f"sha256:{hexdigest}",
            declared_size_bytes=_request_content_length(request),
        )

    @app.get(f"{_API_PREFIX}/config-registry")
    def get_config_registry(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> ConfigRegistryPage:
        return application.config.get_config_registry(limit=limit, before=before)

    @app.get(f"{_API_PREFIX}/config-registry/activations")
    def get_config_activation_history(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> ConfigActivationPage:
        return application.config.get_config_activation_history(
            limit=limit,
            before=before,
        )

    @app.post(f"{_API_PREFIX}/config-registry/contexts")
    def save_context(command: ConfigContextSaveCommand) -> ConfigEntryView:
        return application.config.save_context(command)

    @app.post(f"{_API_PREFIX}/config-registry/contexts/structure/preview")
    def preview_structure(plan: ParameterStructurePlan) -> ParameterStructurePreview:
        return application.config.preview_structure(plan)

    @app.post(f"{_API_PREFIX}/config-registry/contexts/resolve")
    def resolve_context(
        command: ConfigContextResolveCommand,
    ) -> ConfigContextResolution:
        return application.config.resolve_context(command)

    @app.get(f"{_API_PREFIX}/config-registry/active")
    def get_active_config() -> ActiveConfigView:
        return application.config.get_active_config()

    @app.get(f"{_API_PREFIX}/config-registry/entries/{{entry_id}}")
    def get_config_entry(entry_id: str) -> ConfigEntryView:
        return application.config.get_config_entry(entry_id)

    @app.get(f"{_API_PREFIX}/config-registry/publish-operations/{{operation_id:path}}")
    def get_config_publish_operation(operation_id: str) -> ConfigPublishReceipt:
        return application.config.get_config_publish_operation(operation_id)

    @app.post(f"{_API_PREFIX}/config-registry/publish-operations")
    def publish_config(command: ConfigPublishCommand) -> ConfigPublishReceipt:
        return application.config.publish_config(command)

    @app.post(f"{_API_PREFIX}/config-registry/instrument-inventory-migrations")
    def migrate_instrument_inventory(
        command: InstrumentInventoryMigrationCommand,
    ) -> InstrumentInventoryMigrationReceipt:
        return application.config.migrate_instrument_inventory(command)

    @app.post(f"{_API_PREFIX}/config-registry/drafts/preview")
    def preview_config_draft(
        command: ConfigDraftCommand,
    ) -> ConfigDraftPreview:
        return application.config.preview_config_draft(command)

    @app.get(
        f"{_API_PREFIX}/config-registry/activation-operations/{{operation_id:path}}"
    )
    def get_config_activation_operation(
        operation_id: str,
    ) -> ConfigActivationReceipt:
        return application.config.get_config_activation_operation(operation_id)

    @app.post(f"{_API_PREFIX}/config-registry/activation-operations")
    def activate_config_entry(
        command: ConfigEntryActivationCommand,
    ) -> ConfigActivationReceipt:
        return application.config.activate_config_entry(command)

    @app.get(f"{_API_PREFIX}/samples")
    def list_samples(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> SamplePage:
        return application.samples.list(limit=limit, before=before)

    @app.post(f"{_API_PREFIX}/samples", status_code=201)
    def create_sample(command: SampleCreateCommand) -> SampleMutationReceipt:
        return application.samples.create(command)

    @app.post(f"{_API_PREFIX}/sample-artifacts", status_code=201)
    async def import_sample_artifact(
        request: Request,
        artifact_id: Annotated[str, Query(min_length=1)],
        title: Annotated[str, Query(min_length=1)],
        media_type: Annotated[str, Query(min_length=1)],
    ) -> SampleArtifactRef:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_SAMPLE_ARTIFACT_BYTES:
                raise HTTPException(413, "Sample attachment exceeds 8 MiB")
            body.extend(chunk)
        try:
            return application.samples.artifacts.import_bytes(
                bytes(body), artifact_id=artifact_id, title=title, media_type=media_type
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get(f"{_API_PREFIX}/samples/{{sample_id}}/revisions/{{revision}}/artifacts")
    def sample_artifacts(
        sample_id: SampleId, revision: Annotated[int, ApiPath(ge=1)]
    ) -> SampleArtifactPage:
        return application.samples.artifact_list(sample_id, revision)

    @app.get(
        f"{_API_PREFIX}/samples/{{sample_id}}/revisions/{{revision}}/artifacts/content"
    )
    def sample_artifact_content(
        sample_id: SampleId,
        revision: Annotated[int, ApiPath(ge=1)],
        artifact_id: Annotated[str, Query(min_length=1)],
    ) -> Response:
        artifact = application.samples.artifact(sample_id, revision, artifact_id)
        try:
            content = application.samples.artifacts.content(artifact)
        except (OSError, ValueError) as error:
            delivery = application.samples.artifacts.resolve(
                application.samples.revision(sample_id, revision), artifact
            )
            raise HTTPException(
                422,
                delivery.reason
                + " "
                + (
                    delivery.repair
                    or "External references open directly from the sample workspace."
                ),
            ) from error
        return Response(
            content=content,
            media_type=artifact.media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Disposition": 'attachment; filename="sample-attachment.pdf"'
                if artifact.media_type == "application/pdf"
                else "inline",
            },
        )

    @app.get(f"{_API_PREFIX}/samples/{{sample_id}}")
    def get_sample(sample_id: SampleId) -> SampleView:
        return application.samples.get(sample_id)

    @app.get(f"{_API_PREFIX}/samples/{{sample_id}}/revisions")
    def list_sample_revisions(
        sample_id: SampleId,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> SampleRevisionPage:
        return application.samples.revisions(sample_id, limit=limit, before=before)

    @app.get(f"{_API_PREFIX}/samples/{{sample_id}}/revisions/{{revision}}")
    def get_sample_revision(
        sample_id: SampleId,
        revision: Annotated[int, ApiPath(ge=1)],
    ) -> SampleRevision:
        return application.samples.revision(sample_id, revision)

    @app.get(f"{_API_PREFIX}/samples/{{sample_id}}/analyses")
    def list_sample_analyses(
        sample_id: SampleId,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> SampleAnalysisPage:
        return application.analyses.list_sample(
            sample_id,
            limit=limit,
            before=before,
        )

    @app.post(f"{_API_PREFIX}/samples/{{sample_id}}/analyses", status_code=201)
    def save_sample_analysis(
        sample_id: SampleId,
        command: AnalysisSaveCommand,
    ) -> AnalysisSaveReceipt:
        return application.analyses.save_sample(sample_id, command)

    @app.get(f"{_API_PREFIX}/samples/{{sample_id}}/analyses/{{selector}}")
    def get_sample_analysis(sample_id: SampleId, selector: str) -> SampleAnalysisView:
        return application.analyses.get_sample(sample_id, selector)

    @app.get(f"{_API_PREFIX}/samples/{{sample_id}}/analyses/{{analysis_id}}/contents")
    def list_sample_analysis_contents(
        sample_id: SampleId,
        analysis_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> ProjectAnalysisContentPage:
        return application.analyses.list_sample_contents(
            sample_id,
            analysis_id,
            limit=limit,
            before=before,
        )

    @app.get(
        f"{_API_PREFIX}/samples/{{sample_id}}/analyses/{{analysis_id}}/"
        "contents/{selector}"
    )
    def get_sample_analysis_content(
        sample_id: SampleId,
        analysis_id: str,
        selector: str,
    ) -> ContentEntry:
        return application.analyses.sample_content(
            sample_id,
            analysis_id,
            selector,
        )

    @app.get(
        f"{_API_PREFIX}/samples/{{sample_id}}/analyses/{{analysis_id}}/"
        "contents/{selector}/bytes"
    )
    def get_sample_analysis_content_bytes(
        sample_id: SampleId,
        analysis_id: str,
        selector: str,
    ) -> AnalysisContentBytesView:
        return application.analyses.sample_content_bytes(
            sample_id,
            analysis_id,
            selector,
        )

    @app.post(f"{_API_PREFIX}/samples/{{sample_id}}/revisions")
    def revise_sample(
        sample_id: SampleId,
        command: SampleReviseCommand,
    ) -> SampleMutationReceipt:
        return application.samples.revise(sample_id, command)

    @app.get(f"{_API_PREFIX}/instruments")
    def list_instruments() -> InstrumentListView:
        return application.instruments.list_instruments()

    @app.post(f"{_API_PREFIX}/instruments/release")
    def release_instruments(
        command: InstrumentReleaseCommand,
    ) -> InstrumentReleaseReceipt:
        return application.instruments.release_idle_instruments(command)

    @app.get(f"{_API_PREFIX}/instrument-drivers")
    def get_driver_catalog() -> DriverCatalog:
        return application.instruments.driver_catalog()

    @app.post(f"{_API_PREFIX}/instrument-drivers/probe")
    def probe_driver(
        command: InstrumentDriverProbeCommand,
    ) -> InstrumentDriverProbeReceipt:
        return application.instruments.probe_driver(command)

    @app.get(f"{_API_PREFIX}/instruments/{{instrument_id}}")
    def get_instrument(instrument_id: str) -> InstrumentView:
        return application.instruments.get_instrument(instrument_id)

    @app.post(f"{_API_PREFIX}/instrument-contracts/resolve")
    def resolve_instrument_contracts(
        command: InstrumentContractCatalogRequest,
    ) -> InstrumentContractCatalog:
        return application.instruments.resolve_instrument_contracts(command.config)

    @app.post(f"{_API_PREFIX}/instrument-sessions", status_code=201)
    def open_instrument_session(
        command: InstrumentSessionOpenCommand,
    ) -> InstrumentSessionOpenReceipt:
        return application.instruments.open_session(command)

    @app.post(f"{_API_PREFIX}/instrument-sessions/{{session_id}}/heartbeat")
    def renew_instrument_session(
        session_id: str,
    ) -> InstrumentSessionLeaseReceipt:
        return application.instruments.renew_session(session_id)

    @app.get(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/instruments/"
        "{instrument_id}/state"
    )
    def read_instrument_state(
        session_id: str,
        instrument_id: str,
    ) -> InstrumentStateSnapshot:
        return application.instruments.read_state(
            session_id,
            instrument_id,
        )

    @app.post(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/instruments/"
        "{instrument_id}/state/read"
    )
    def read_instrument_state_members(
        session_id: str,
        instrument_id: str,
        command: InstrumentStateReadCommand,
    ) -> InstrumentStateReadback:
        return application.instruments.read_state_members(
            session_id,
            instrument_id,
            command,
        )

    @app.post(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/instruments/"
        "{instrument_id}/state/observed"
    )
    def read_observed_instrument_state_members(
        session_id: str,
        instrument_id: str,
        command: InstrumentStateReadCommand,
    ) -> InstrumentStateCacheReadback:
        return application.instruments.observed_state_members(
            session_id,
            instrument_id,
            command,
        )

    @app.post(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/instruments/"
        "{instrument_id}/state/apply"
    )
    def apply_instrument_state(
        session_id: str,
        instrument_id: str,
        command: InstrumentStateCommand,
    ) -> ApplyReceipt:
        return application.instruments.apply_state(
            session_id,
            instrument_id,
            command,
        )

    @app.post(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/instruments/"
        "{instrument_id}/configured-defaults/apply"
    )
    def apply_instrument_configured_defaults(
        session_id: str,
        instrument_id: str,
        command: InstrumentConfiguredDefaultsApplyCommand,
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        return application.instruments.apply_configured_defaults(
            session_id,
            instrument_id,
            command,
        )

    @app.post(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/instruments/"
        "{instrument_id}/invoke"
    )
    def invoke_instrument(
        session_id: str,
        instrument_id: str,
        command: InvokeCommand,
    ) -> InvokeReceipt:
        return application.instruments.invoke(
            session_id,
            instrument_id,
            command,
        )

    @app.post(
        f"{_API_PREFIX}/instrument-sessions/{{session_id}}/instruments/"
        "{instrument_id}/collect",
        response_class=Response,
        response_model=CollectReceipt,
        responses={
            200: {
                "content": {
                    HARDWARE_RECEIPT_MEDIA_TYPE: {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        },
    )
    def collect_instrument(
        session_id: str,
        instrument_id: str,
        intent: InteractiveCollectIntent,
    ) -> Response:
        return Response(
            content=encode_collect_receipt(
                application.instruments.collect(
                    session_id,
                    instrument_id,
                    intent,
                )
            ),
            media_type=HARDWARE_RECEIPT_MEDIA_TYPE,
        )

    @app.post(f"{_API_PREFIX}/instrument-sessions/{{session_id}}/close")
    def close_instrument_session(
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return application.instruments.close_session(session_id)

    @app.post(f"{_API_PREFIX}/instrument-sessions/{{session_id}}/abort")
    def abort_instrument_session(
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return application.instruments.abort_session(session_id)

    @app.post(f"{_API_PREFIX}/instrument-sessions/{{session_id}}/attention")
    def resolve_instrument_session_attention(
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return application.instruments.resolve_attention(session_id)

    @app.post(f"{_API_PREFIX}/reviews", status_code=201)
    def create_review(command: ReviewSessionCreateCommand) -> ReviewSessionView:
        return application.reviews.create(command)

    @app.get(f"{_API_PREFIX}/reviews")
    def list_reviews() -> ReviewSessionListView:
        return application.reviews.list()

    @app.get(f"{_API_PREFIX}/reviews/{{session_id}}")
    def get_review(session_id: str) -> ReviewSessionView:
        return application.reviews.get(session_id)

    @app.post(f"{_API_PREFIX}/reviews/{{session_id}}/compile", status_code=202)
    def compile_review_point(
        session_id: str,
        command: ReviewCompileCommand,
    ) -> ReviewCompileReceipt:
        return application.reviews.enqueue(session_id, command)

    @app.post(f"{_API_PREFIX}/reviews/{{session_id}}/worker/claim")
    def claim_review_work(
        session_id: str,
        worker_id: Annotated[str, Query(min_length=1)],
    ) -> ReviewWorkItem | None:
        return application.reviews.claim(session_id, worker_id)

    @app.post(f"{_API_PREFIX}/reviews/{{session_id}}/worker/complete")
    def complete_review_work(
        session_id: str,
        command: ReviewCompletionCommand,
    ) -> ReviewSessionView:
        return application.reviews.complete(session_id, command)

    @app.post(f"{_API_PREFIX}/reviews/{{session_id}}/worker/heartbeat")
    def heartbeat_review_worker(
        session_id: str,
        worker_id: Annotated[str, Query(min_length=1)],
    ) -> ReviewHeartbeatReceipt:
        return application.reviews.heartbeat(session_id, worker_id)

    @app.post(f"{_API_PREFIX}/reviews/{{session_id}}/worker/close")
    def close_review_worker(
        session_id: str,
        worker_id: Annotated[str, Query(min_length=1)],
    ) -> ReviewSessionCloseReceipt:
        return application.reviews.close(session_id, worker_id)

    @app.post(f"{_API_PREFIX}/procedures", status_code=201)
    def submit_procedure(
        command: ProcedureSubmitCommand,
    ) -> ProcedureSubmitReceipt:
        return application.automation.submit(command)

    @app.post(f"{_API_PREFIX}/calibration-status/query")
    def query_calibration_status(
        query: CalibrationStatusQuery,
    ) -> CalibrationStatusReceipt:
        return application.calibration_cohorts.status(query)

    @app.post(f"{_API_PREFIX}/calibration-cohorts", status_code=201)
    def create_calibration_cohort(
        command: CalibrationCohortCreateCommand,
    ) -> CalibrationCohortCreateReceipt:
        return application.calibration_cohorts.create(command)

    @app.get(f"{_API_PREFIX}/calibration-cohorts")
    def list_calibration_cohorts(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[int | None, Query(ge=1)] = None,
        fanout_scope: Annotated[str | None, Query(min_length=1)] = None,
    ) -> CalibrationCohortPage:
        return application.calibration_cohorts.list(
            CalibrationCohortListQuery(
                limit=limit,
                cursor=cursor,
                fanout_scope=fanout_scope,
            )
        )

    @app.get(f"{_API_PREFIX}/calibration-cohort-members/by-cohort/{{cohort_id:path}}")
    def list_calibration_cohort_members(
        cohort_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[int | None, Query(ge=0)] = None,
    ) -> CalibrationCohortMemberPage:
        return application.calibration_cohorts.list_members(
            CalibrationCohortMemberListQuery(
                cohort_id=cohort_id,
                limit=limit,
                cursor=cursor,
            )
        )

    @app.get(f"{_API_PREFIX}/calibration-cohorts/by-id/{{cohort_id:path}}")
    def get_calibration_cohort(cohort_id: str) -> CalibrationCohortGetReceipt:
        return application.calibration_cohorts.get(
            CalibrationCohortGetQuery(cohort_id=cohort_id)
        )

    @app.post(f"{_API_PREFIX}/calibration-publications/ready/query")
    def list_ready_calibration_publications(
        query: CalibrationPublicationReadyQuery,
    ) -> CalibrationPublicationReadyPage:
        return application.calibration_cohorts.ready_publications(query)

    @app.get(f"{_API_PREFIX}/calibration-publications/by-cohort/{{cohort_id:path}}")
    def get_calibration_publication(
        cohort_id: str,
    ) -> CalibrationPublicationGetReceipt:
        return application.calibration_cohorts.get_publication(
            CalibrationPublicationGetQuery(cohort_id=cohort_id)
        )

    @app.get(f"{_API_PREFIX}/calibration-publications/operations/{{operation_id:path}}")
    def get_calibration_publication_operation(
        operation_id: str,
    ) -> CalibrationPublicationReceipt:
        return application.config.get_calibration_publication_operation(operation_id)

    @app.post(f"{_API_PREFIX}/calibration-publications/operations")
    def publish_calibration(
        command: CalibrationPublicationCommand,
    ) -> CalibrationPublicationReceipt:
        return application.config.publish_calibration(command)

    @app.post(f"{_API_PREFIX}/calibration-publication-attentions/{{cohort_id:path}}")
    def require_calibration_publication_attention(
        cohort_id: str,
        command: CalibrationPublicationAttentionCommand,
    ) -> CalibrationPublicationAttentionReceipt:
        _require_calibration_publication_cohort_id(cohort_id, command.cohort_id)
        return application.calibration_cohorts.require_publication_attention(command)

    @app.post(f"{_API_PREFIX}/calibration-publication-retries/{{cohort_id:path}}")
    def retry_calibration_publication(
        cohort_id: str,
        command: CalibrationPublicationRetryCommand,
    ) -> CalibrationPublicationRetryReceipt:
        _require_calibration_publication_cohort_id(cohort_id, command.cohort_id)
        return application.calibration_cohorts.retry_publication(command)

    @app.post(f"{_API_PREFIX}/calibration-publication-deferrals/{{cohort_id:path}}")
    def defer_calibration_publication(
        cohort_id: str,
        command: CalibrationPublicationDeferCommand,
    ) -> CalibrationPublicationDeferReceipt:
        _require_calibration_publication_cohort_id(cohort_id, command.cohort_id)
        return application.calibration_cohorts.defer_publication(command)

    @app.post(f"{_API_PREFIX}/procedure-schedules", status_code=201)
    def create_procedure_schedule(
        command: ProcedureScheduleCreateCommand,
    ) -> ProcedureScheduleCreateReceipt:
        return application.procedure_schedules.create(command)

    @app.get(f"{_API_PREFIX}/procedure-schedules")
    def list_procedure_schedules(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[int | None, Query(ge=1)] = None,
        state: ProcedureScheduleState | None = None,
    ) -> ProcedureSchedulePage:
        return application.procedure_schedules.list(
            ProcedureScheduleListQuery(limit=limit, cursor=cursor, state=state)
        )

    @app.get(f"{_API_PREFIX}/procedure-schedules/due")
    def list_due_procedure_schedules(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[int | None, Query(ge=1)] = None,
        through_sequence: Annotated[int | None, Query(ge=1)] = None,
    ) -> ProcedureScheduleDuePage:
        _require_due_schedule_traversal(cursor, through_sequence)
        return application.procedure_schedules.due(
            ProcedureScheduleDueQuery(
                limit=limit,
                cursor=cursor,
                through_sequence=through_sequence,
            )
        )

    @app.get(f"{_API_PREFIX}/procedure-schedules/by-id/{{schedule_id:path}}")
    def get_procedure_schedule(schedule_id: str) -> ProcedureSchedule:
        return application.procedure_schedules.get(schedule_id)

    @app.post(f"{_API_PREFIX}/procedure-schedule-cancellations/{{schedule_id:path}}")
    def cancel_procedure_schedule(
        schedule_id: str,
        command: ProcedureScheduleCancelCommand,
    ) -> ProcedureScheduleCancelReceipt:
        _require_procedure_schedule_id(schedule_id, command.schedule_id)
        return application.procedure_schedules.cancel(command)

    @app.post(f"{_API_PREFIX}/procedure-schedule-materializations/{{schedule_id:path}}")
    def materialize_procedure_schedule(
        schedule_id: str,
        command: ProcedureScheduleMaterializeCommand,
    ) -> ProcedureScheduleMaterializeReceipt:
        _require_procedure_schedule_id(schedule_id, command.schedule_id)
        return application.procedure_schedules.materialize(command)

    @app.get(f"{_API_PREFIX}/procedures")
    def list_procedures(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[int | None, Query(ge=1)] = None,
        state: ProcedureRunState | None = None,
        request_key: Annotated[str | None, Query(min_length=1)] = None,
    ) -> ProcedureRunPage:
        return application.automation.list(
            ProcedureRunListQuery(
                limit=limit, cursor=cursor, state=state, request_key=request_key
            )
        )

    @app.post(f"{_API_PREFIX}/procedures/runnable/query")
    def list_runnable_procedures(
        query: ProcedureRunnableQuery,
    ) -> ProcedureRunnablePage:
        return application.automation.runnable(query)

    @app.get(f"{_API_PREFIX}/procedures/{{procedure_run_id}}")
    def get_procedure(procedure_run_id: str) -> ProcedureRun:
        return application.automation.get(procedure_run_id)

    @app.get(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps")
    def list_procedure_step_attempts(
        procedure_run_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[int | None, Query(ge=1)] = None,
    ) -> ProcedureStepAttemptPage:
        return application.automation.step_attempts(
            procedure_run_id,
            ProcedureStepAttemptListQuery(limit=limit, cursor=cursor),
        )

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/worker/lease/acquire",
        status_code=201,
    )
    def acquire_procedure_worker_lease(
        procedure_run_id: str,
        command: ProcedureWorkerLeaseAcquireCommand,
    ) -> ProcedureWorkerLeaseAcquireReceipt:
        _require_procedure_run_id(procedure_run_id, command.procedure_run_id)
        return application.automation.acquire_lease(command)

    @app.post(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/worker/lease/heartbeat")
    def heartbeat_procedure_worker_lease(
        procedure_run_id: str,
        command: ProcedureWorkerLeaseHeartbeatCommand,
    ) -> ProcedureWorkerLeaseHeartbeatReceipt:
        _require_procedure_run_id(procedure_run_id, command.procedure_run_id)
        return application.automation.heartbeat_lease(command)

    @app.post(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/worker/lease/release")
    def release_procedure_worker_lease(
        procedure_run_id: str,
        command: ProcedureWorkerLeaseReleaseCommand,
    ) -> ProcedureWorkerLeaseReleaseReceipt:
        _require_procedure_run_id(procedure_run_id, command.procedure_run_id)
        return application.automation.release_lease(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/begin",
        status_code=201,
    )
    def begin_procedure_step(
        procedure_run_id: str,
        command: ProcedureStepBeginCommand,
    ) -> ProcedureStepBeginReceipt:
        _require_procedure_run_id(procedure_run_id, command.procedure_run_id)
        return application.automation.begin_step(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/{{step_key:path}}/"
        "attempts/{attempt}/complete"
    )
    def complete_procedure_step(
        procedure_run_id: str,
        step_key: str,
        attempt: Annotated[int, ApiPath(ge=1)],
        command: ProcedureStepCompleteCommand,
    ) -> ProcedureStepCompleteReceipt:
        _require_procedure_step_identity(
            procedure_run_id,
            step_key,
            attempt,
            command.procedure_run_id,
            command.step_key,
            command.attempt,
        )
        return application.automation.complete_step(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/{{step_key:path}}/"
        "attempts/{attempt}/fail"
    )
    def fail_procedure_step(
        procedure_run_id: str,
        step_key: str,
        attempt: Annotated[int, ApiPath(ge=1)],
        command: ProcedureStepFailCommand,
    ) -> ProcedureStepFailReceipt:
        _require_procedure_step_identity(
            procedure_run_id,
            step_key,
            attempt,
            command.procedure_run_id,
            command.step_key,
            command.attempt,
        )
        return application.automation.fail_step(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/{{step_key:path}}/"
        "attempts/{attempt}/attention"
    )
    def require_procedure_step_attention(
        procedure_run_id: str,
        step_key: str,
        attempt: Annotated[int, ApiPath(ge=1)],
        command: ProcedureStepAttentionCommand,
    ) -> ProcedureStepAttentionReceipt:
        _require_procedure_step_identity(
            procedure_run_id,
            step_key,
            attempt,
            command.procedure_run_id,
            command.step_key,
            command.attempt,
        )
        return application.automation.require_step_attention(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/{{step_key:path}}/"
        "attempts/{attempt}/input/wait"
    )
    def wait_procedure_step_input(
        procedure_run_id: str,
        step_key: str,
        attempt: Annotated[int, ApiPath(ge=1)],
        command: ProcedureStepInputWaitCommand,
    ) -> ProcedureStepInputWaitReceipt:
        _require_procedure_step_identity(
            procedure_run_id,
            step_key,
            attempt,
            command.procedure_run_id,
            command.step_key,
            command.attempt,
        )
        return application.automation.wait_step_input(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/{{step_key:path}}/attempts/{{attempt}}/resources/wait"
    )
    def wait_procedure_step_resources(
        procedure_run_id: str,
        step_key: str,
        attempt: Annotated[int, ApiPath(ge=1)],
        command: ProcedureStepResourceWaitCommand,
    ) -> ProcedureStepResourceWaitReceipt:
        _require_procedure_step_identity(
            procedure_run_id,
            step_key,
            attempt,
            command.procedure_run_id,
            command.step_key,
            command.attempt,
        )
        return application.automation.wait_step_resources(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/{{step_key:path}}/"
        "attempts/{attempt}/input"
    )
    def submit_procedure_step_input(
        procedure_run_id: str,
        step_key: str,
        attempt: Annotated[int, ApiPath(ge=1)],
        command: ProcedureStepInputSubmitCommand,
    ) -> ProcedureStepInputSubmitReceipt:
        _require_procedure_step_identity(
            procedure_run_id,
            step_key,
            attempt,
            command.procedure_run_id,
            command.step_key,
            command.attempt,
        )
        return application.automation.submit_step_input(command)

    @app.post(
        f"{_API_PREFIX}/procedures/{{procedure_run_id}}/steps/{{step_key:path}}/"
        "attempts/{attempt}/retry"
    )
    def retry_procedure_step_attention(
        procedure_run_id: str,
        step_key: str,
        attempt: Annotated[int, ApiPath(ge=1)],
        command: ProcedureStepAttentionRetryCommand,
    ) -> ProcedureStepAttentionRetryReceipt:
        _require_procedure_step_identity(
            procedure_run_id,
            step_key,
            attempt,
            command.procedure_run_id,
            command.step_key,
            command.attempt,
        )
        return application.automation.retry_step_attention(command)

    @app.post(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/attention")
    def require_procedure_run_attention(
        procedure_run_id: str,
        command: ProcedureRunAttentionCommand,
    ) -> ProcedureRunAttentionReceipt:
        _require_procedure_run_id(procedure_run_id, command.procedure_run_id)
        return application.automation.require_run_attention(command)

    @app.post(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/cancel")
    def cancel_procedure(
        procedure_run_id: str,
        command: ProcedureCancelCommand,
    ) -> ProcedureCancelReceipt:
        _require_procedure_run_id(procedure_run_id, command.procedure_run_id)
        return application.automation.cancel(command)

    @app.post(f"{_API_PREFIX}/procedures/{{procedure_run_id}}/close")
    def close_procedure(
        procedure_run_id: str,
        command: ProcedureCloseCommand,
    ) -> ProcedureCloseReceipt:
        _require_procedure_run_id(procedure_run_id, command.procedure_run_id)
        return application.automation.close(command)

    @app.get(f"{_API_PREFIX}/runs")
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        before: Annotated[int | None, Query(ge=1)] = None,
        state: ControlRunState | None = None,
        sample_id: SampleId | None = None,
    ) -> RunSummaryPage:
        return application.runs.list_runs(
            limit=limit,
            before=before,
            state=state,
            sample_id=sample_id,
        )

    @app.post(f"{_API_PREFIX}/runs", status_code=201)
    def submit_run(submission: RunSubmission) -> RunAdmission:
        return application.submit_run(submission)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}")
    def get_run(run_id: str) -> RunDetail:
        return application.runs.get_run(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/measured-costs")
    def get_run_measured_costs(run_id: str) -> RunMeasuredCosts:
        return application.runs.get_run_measured_costs(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/failure-evidence")
    def get_run_failure_evidence(run_id: str) -> RunFailureEvidence:
        return application.runs.get_run_failure_evidence(run_id)

    @app.get(
        f"{_API_PREFIX}/instrument-workers/{{generation}}/diagnostics",
        response_class=Response,
    )
    def get_worker_diagnostics(
        generation: Annotated[str, ApiPath(pattern=r"^[0-9a-f]{32}$")],
        raw: bool = False,
    ) -> Response:
        from scopecat_server.instruments.worker_output import read_diagnostic

        try:
            content = read_diagnostic(application.project_root, generation, raw=raw)
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=410, detail="Worker diagnostics are no longer retained"
            ) from error
        return Response(
            content,
            media_type="application/x-ndjson" if raw else "text/plain",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/cancel")
    def cancel_run(run_id: str) -> RunCancellationReceipt:
        return application.cancel_run(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/cancellation")
    def run_cancellation(run_id: str) -> RunCancellationState:
        return application.executor.run_cancellation(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/config")
    def get_run_config(run_id: str) -> RunConfigView:
        return application.runs.get_run_config(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/request")
    def get_run_request(run_id: str) -> RunRequestView:
        return application.runs.get_run_request(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/contents")
    def list_run_contents(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
        role: Literal["artifact", "dataset", "record"] | None = None,
        kind: str | None = None,
    ) -> RunContentPage:
        return application.runs.list_run_contents(
            run_id,
            limit=limit,
            before=before,
            role=role,
            kind=kind,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/contents/{{role}}/{{content_id}}")
    def get_run_content(
        run_id: str,
        role: Literal["artifact", "dataset", "record"],
        content_id: str,
    ) -> ContentEntry:
        return application.runs.get_run_content(
            run_id,
            role=role,
            content_id=content_id,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/analyses")
    def list_run_analyses(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> RunAnalysisPage:
        return application.runs.list_run_analyses(
            run_id,
            limit=limit,
            before=before,
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/analyses", status_code=201)
    def save_run_analysis(
        run_id: str,
        command: AnalysisSaveCommand,
    ) -> AnalysisSaveReceipt:
        return application.runs.save_run_analysis(run_id, command)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/analyses/{{selector}}")
    def get_run_analysis(run_id: str, selector: str) -> RunAnalysisView:
        return application.runs.get_run_analysis(run_id, selector)

    @app.get(f"{_API_PREFIX}/analyses")
    def list_project_analyses(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> ProjectAnalysisPage:
        return application.analyses.list(limit=limit, before=before)

    @app.post(f"{_API_PREFIX}/analyses", status_code=201)
    def save_project_analysis(command: AnalysisSaveCommand) -> AnalysisSaveReceipt:
        return application.analyses.save(command)

    @app.get(f"{_API_PREFIX}/analyses/{{selector}}")
    def get_project_analysis(selector: str) -> ProjectAnalysisView:
        return application.analyses.get(selector)

    @app.get(f"{_API_PREFIX}/analyses/{{analysis_id}}/contents")
    def list_project_analysis_contents(
        analysis_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> ProjectAnalysisContentPage:
        return application.analyses.list_contents(
            analysis_id,
            limit=limit,
            before=before,
        )

    @app.get(f"{_API_PREFIX}/analyses/{{analysis_id}}/contents/{{selector}}")
    def get_project_analysis_content(
        analysis_id: str,
        selector: str,
    ) -> ContentEntry:
        return application.analyses.content(analysis_id, selector)

    @app.get(f"{_API_PREFIX}/analyses/{{analysis_id}}/contents/{{selector}}/bytes")
    def get_project_analysis_content_bytes(
        analysis_id: str,
        selector: str,
    ) -> AnalysisContentBytesView:
        return application.analyses.content_bytes(analysis_id, selector)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/artifacts/{{selector}}/bytes")
    def get_run_artifact_bytes(
        run_id: str,
        selector: str,
        expected_kind: str | None = None,
    ) -> RunArtifactBytesView:
        return application.runs.get_run_artifact_bytes(
            run_id,
            selector,
            expected_kind=expected_kind,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/artifacts/{{selector}}/text")
    def get_run_artifact_text(
        run_id: str,
        selector: str,
        expected_kind: str | None = None,
    ) -> RunArtifactTextResult:
        return application.runs.get_run_artifact_text(
            run_id,
            selector,
            expected_kind=expected_kind,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/artifacts/{{selector}}/json")
    def get_run_artifact_json(
        run_id: str,
        selector: str,
        expected_kind: str | None = None,
    ) -> RunArtifactJsonResult:
        return application.runs.get_run_artifact_json(
            run_id,
            selector,
            expected_kind=expected_kind,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/records/{{selector}}/json")
    def get_run_record_json(
        run_id: str,
        selector: str,
        expected_kind: str | None = None,
    ) -> RunRecordJsonResult:
        return application.runs.get_run_record_json(
            run_id,
            selector,
            expected_kind=expected_kind,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/datasets/{{selector}}")
    def get_run_dataset_content(
        run_id: str,
        selector: str,
    ) -> RunMeasurementDatasetResult:
        return application.runs.get_run_dataset_content(run_id, selector)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/datasets/{{selector}}/bytes")
    def get_run_dataset_bytes(
        run_id: str,
        selector: str,
        expected_kind: str | None = None,
    ) -> RunDatasetBytesView:
        return application.runs.get_run_dataset_bytes(
            run_id,
            selector,
            expected_kind=expected_kind,
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/attachments", status_code=201)
    def attach_run_content(
        run_id: str,
        command: RunAttachmentCommand,
    ) -> ContentEntry:
        return application.runs.attach_run_content(run_id, command)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/parameter-proposals")
    def list_parameter_proposals(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> ParameterProposalPage:
        return application.runs.list_parameter_proposals(
            run_id,
            limit=limit,
            before=before,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/parameter-proposals/{{proposal_id}}")
    def get_parameter_proposal(
        run_id: str,
        proposal_id: str,
    ) -> ParameterProposalView:
        return application.runs.get_parameter_proposal(run_id, proposal_id)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/attention")
    def resolve_attention(
        run_id: str,
        command: AttentionResolutionCommand,
    ) -> AttentionResolutionReceipt:
        return application.resolve_attention(run_id, command)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/measurements/arrow")
    def measurement_arrow(
        run_id: str,
        query: MeasurementArrowQuery,
    ) -> Response:
        import pyarrow as pa

        table, next_offset, snapshot_size = application.runs.measurement_arrow(
            run_id, query
        )
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        headers = {_SNAPSHOT_SIZE_HEADER: str(snapshot_size)}
        if next_offset is not None:
            headers[_NEXT_OFFSET_HEADER] = str(next_offset)
        return Response(
            content=sink.getvalue().to_pybytes(),
            media_type=_ARROW_STREAM_MEDIA_TYPE,
            headers=headers,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/measurements/preview")
    def measurement_preview(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> MeasurementPreview:
        return application.runs.measurement_preview(run_id, limit=limit)

    @app.get(
        f"{_API_PREFIX}/runs/{{run_id}}/measurements/live",
        response_class=Response,
        responses={
            200: {
                "content": {
                    _ARROW_FILE_MEDIA_TYPE: {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        },
    )
    def measurement_live_preview(
        run_id: str,
        after_record_count: Annotated[int | None, Query(ge=0)] = None,
    ) -> Response:
        preview, content = application.runs.measurement_live_arrow(
            run_id,
            after_record_count=after_record_count,
        )
        return Response(
            content=content,
            media_type=_ARROW_FILE_MEDIA_TYPE,
            headers={
                _MEASUREMENT_ACTIVE_HEADER: str(preview.active).lower(),
                _MEASUREMENT_RECEIVED_COUNT_HEADER: str(preview.received_record_count),
                _MEASUREMENT_DURABLE_COUNT_HEADER: str(preview.durable_record_count),
            },
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/measurements/query")
    def query_measurements(
        run_id: str,
        query: MeasurementSliceQuery,
    ) -> MeasurementSlice:
        return application.runs.measurement_slice(run_id, query)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/measurements/traces/query")
    def query_measurement_traces(
        run_id: str,
        query: MeasurementTracePreviewQuery,
    ) -> MeasurementTracePreview:
        return application.runs.measurement_trace_preview(run_id, query)

    @app.get(f"{_API_PREFIX}/events")
    def list_events(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        after: Annotated[int | None, Query(ge=0)] = None,
        run_id: str | None = None,
        latest: bool = False,
    ) -> EventPage:
        return application.runs.list_events(
            limit=limit,
            after=after,
            run_id=run_id,
            latest=latest,
        )

    @app.get(f"{_API_PREFIX}/events/stream")
    async def stream_events(
        request: Request,
        after: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[
            int | None,
            Header(alias="Last-Event-ID", ge=0),
        ] = None,
        run_id: str | None = None,
        follow: bool = True,
    ) -> StreamingResponse:
        cursor = last_event_id if last_event_id is not None else after
        events = _event_stream(
            application,
            request,
            after=cursor,
            run_id=run_id,
            follow=follow,
        )
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/executor/start")
    def start_executor(
        run_id: str,
        request: ExecutorStartRequest,
    ) -> ExecutorLease:
        return application.executor.start_executor(run_id, request)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/executor/heartbeat")
    def heartbeat_executor(
        run_id: str,
        heartbeat: ExecutorHeartbeat,
    ) -> ExecutorLease:
        return application.executor.heartbeat_executor(run_id, heartbeat)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/coverage")
    def get_run_coverage(run_id: str) -> RunCoverageState:
        return application.executor.run_coverage(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/execution-segments")
    def get_run_execution_segments(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 64,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> RunExecutionSegmentPage:
        return application.executor.execution_segments(
            run_id,
            limit=limit,
            before=before,
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/coverage/advance")
    def advance_run_coverage(
        run_id: str,
        command: RunCoverageAdvanceCommand,
    ) -> RunCoverageState:
        return application.executor.advance_run_coverage(run_id, command)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/recovery-groups")
    def get_run_recovery_groups(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 64,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> RunRecoveryGroupPage:
        return application.executor.recovery_groups(
            run_id,
            limit=limit,
            before=before,
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/recovery-groups")
    def commit_run_recovery_groups(
        run_id: str,
        command: RunRecoveryGroupCommitCommand,
    ) -> RunRecoveryGroupCommitReceipt:
        return application.executor.commit_recovery_groups(run_id, command)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/domain-jobs/transitions")
    def get_run_domain_job_transitions(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 64,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> RunDomainJobTransitionPage:
        return application.executor.domain_job_transitions(
            run_id,
            limit=limit,
            before=before,
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/domain-jobs")
    def get_run_domain_jobs(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 64,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> RunDomainJobStatePage:
        return application.executor.domain_jobs(
            run_id,
            limit=limit,
            before=before,
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/domain-jobs/transitions")
    def commit_run_domain_job_transitions(
        run_id: str,
        command: RunDomainJobTransitionBatchCommand,
    ) -> RunDomainJobTransitionBatchReceipt:
        return application.executor.commit_domain_job_transitions(run_id, command)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/point-plan")
    def get_run_point_plan(run_id: str) -> RunPointPlanView:
        return application.point_plans.read(run_id)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/point-plan/decisions")
    def append_run_domain_decision(
        run_id: str,
        command: RunDomainDecisionCommand,
    ) -> RunDomainDecisionView:
        return application.executor.append_run_domain_decision(run_id, command)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/point-plan/decisions")
    def get_run_domain_decisions(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 64,
        before: Annotated[int | None, Query(ge=0)] = None,
    ) -> RunDomainDecisionPage:
        return application.point_plans.decisions(
            run_id,
            limit=limit,
            before=before,
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/point-plan/close")
    def close_run_point_plan(
        run_id: str,
        command: RunPointPlanCloseCommand,
    ) -> RunPointPlanView:
        return application.executor.close_run_point_plan(run_id, command)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/point-plan/queue")
    def get_run_domain_queue(run_id: str) -> RunDomainQueueView:
        return application.point_plans.queue(run_id)

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/point-plan/queue/next")
    def get_next_queued_run_domain(run_id: str) -> RunDomainQueueEntryView | None:
        return application.point_plans.next_queued(run_id)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/point-plan/queue")
    def enqueue_run_domain(
        run_id: str,
        command: RunDomainEnqueueCommand,
    ) -> RunDomainQueueEntryView:
        return application.point_plans.enqueue(run_id, command)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/point-plan/resolve")
    def resolve_run_domain(
        run_id: str,
        command: RunDomainResolveCommand,
    ) -> ResolvedRunDomainView:
        return application.point_plans.resolve(run_id, command)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/instruments/provision")
    def provision_run_instruments(
        run_id: str,
        command: RunInstrumentProvisionCommand,
    ) -> RunInstrumentProvisionReceipt:
        return application.instruments.provision_run(run_id, command)

    @app.post(
        f"{_API_PREFIX}/runs/{{run_id}}/hardware/execute",
        response_class=Response,
        response_model=RunHardwareBatchReceipt,
        responses={
            200: {
                "content": {
                    HARDWARE_RECEIPT_MEDIA_TYPE: {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        },
    )
    def execute_run_hardware(
        run_id: str,
        command: RunHardwareBatchCommand,
    ) -> Response:
        return Response(
            content=encode_run_hardware_receipt(
                application.instruments.execute_run_hardware(run_id, command)
            ),
            media_type=HARDWARE_RECEIPT_MEDIA_TYPE,
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/hardware/finish")
    def finish_run_hardware(
        run_id: str,
        command: RunHardwareFinishCommand,
    ) -> RunHardwareFinalizationReceipt:
        return application.instruments.finish_run_hardware(run_id, command)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/measurements/header")
    def initialize_measurements(
        run_id: str,
        command: MeasurementHeaderCommand,
    ) -> MeasurementDatasetReceipt:
        _require_run_id(run_id, command.header.run_id)
        return application.executor.initialize_measurements(run_id, command)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/measurements/ingest")
    async def ingest_measurements(
        run_id: str,
        request: Request,
        lease_id: Annotated[
            str,
            Header(alias="X-Scopecat-Lease-ID", min_length=1),
        ],
    ) -> MeasurementIngestReceipt:
        return application.executor.ingest_measurements(
            run_id,
            lease_id=lease_id,
            content=await request.body(),
        )

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/measurements/flush")
    def flush_measurements(
        run_id: str,
        command: MeasurementFlushCommand,
    ) -> MeasurementFlushReceipt:
        return application.executor.flush_measurements(run_id, command)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/measurements/seal")
    def seal_measurements(
        run_id: str,
        command: MeasurementSealCommand,
    ) -> MeasurementDatasetReceipt:
        _require_run_id(run_id, command.seal.run_id)
        return application.executor.seal_measurements(run_id, command)

    @app.post(f"{_API_PREFIX}/runs/{{run_id}}/terminal")
    def commit_terminal(
        run_id: str,
        command: TerminalRunCommitCommand,
    ) -> RunSnapshot:
        _require_run_id(run_id, command.outcome.run_id)
        return application.executor.commit_terminal(run_id, command)

    if static_dir is not None:
        root = Path(static_dir)
        if not (root / "index.html").is_file():
            raise ValueError("static_dir must contain index.html")
        app.mount(
            "/",
            _SpaStaticFiles(directory=root, html=True),
            name="gui",
        )
    return app


def _install_error_mapping(app: FastAPI) -> None:
    @app.exception_handler(CommandPayloadTooLarge)
    async def payload_too_large(
        _request: Request,
        error: CommandPayloadTooLarge,
    ) -> JSONResponse:
        return JSONResponse(status_code=413, content={"detail": str(error)})

    @app.exception_handler(CommandPayloadError)
    async def invalid_payload(
        _request: Request,
        error: CommandPayloadError,
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(BackendNotFound)
    async def not_found(
        _request: Request,
        error: BackendNotFound,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(BackendConflict)
    async def conflict(
        _request: Request,
        error: BackendConflict,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(SQLiteBusyError)
    async def sqlite_busy(
        _request: Request,
        error: SQLiteBusyError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(error)},
            headers={"Retry-After": "1"},
        )


async def _event_stream(
    application: DaemonApplication,
    request: Request,
    *,
    after: int | None,
    run_id: str | None,
    follow: bool,
) -> AsyncIterator[str]:
    cursor = after
    while True:
        page = await run_in_threadpool(
            application.runs.list_events,
            limit=_SSE_PAGE_SIZE,
            after=cursor,
            run_id=run_id,
            latest=False,
        )
        for event in page.items:
            cursor = event.event_id
            yield _encode_sse(event.event_id, event.model_dump_json())
        if page.next_cursor is not None:
            cursor = page.next_cursor
            continue
        if not follow or await request.is_disconnected():
            return
        await asyncio.sleep(_SSE_POLL_SECONDS)


def _encode_sse(event_id: int, data: str) -> str:
    return f"id: {event_id}\nevent: project\ndata: {data}\n\n"


def _require_run_id(path_run_id: str, body_run_id: str) -> None:
    if path_run_id != body_run_id:
        raise HTTPException(
            status_code=422,
            detail="path run_id must match request body",
        )


def _require_procedure_run_id(
    path_procedure_run_id: str,
    body_procedure_run_id: str,
) -> None:
    if path_procedure_run_id != body_procedure_run_id:
        raise HTTPException(
            status_code=422,
            detail="path procedure_run_id must match request body",
        )


def _require_calibration_publication_cohort_id(
    path_cohort_id: str,
    body_cohort_id: str,
) -> None:
    if path_cohort_id != body_cohort_id:
        raise HTTPException(
            status_code=422,
            detail="path cohort_id must match request body",
        )


def _require_procedure_schedule_id(
    path_schedule_id: str,
    body_schedule_id: str,
) -> None:
    if path_schedule_id != body_schedule_id:
        raise HTTPException(
            status_code=422,
            detail="path schedule_id must match request body",
        )


def _require_due_schedule_traversal(
    cursor: int | None,
    through_sequence: int | None,
) -> None:
    if (cursor is None) != (through_sequence is None):
        raise HTTPException(
            status_code=422,
            detail="cursor and through_sequence must be provided together",
        )
    if (
        cursor is not None
        and through_sequence is not None
        and cursor >= through_sequence
    ):
        raise HTTPException(
            status_code=422,
            detail="cursor must be below through_sequence",
        )


def _require_procedure_step_identity(
    path_procedure_run_id: str,
    path_step_key: str,
    path_attempt: int,
    body_procedure_run_id: str,
    body_step_key: str,
    body_attempt: int,
) -> None:
    _require_procedure_run_id(path_procedure_run_id, body_procedure_run_id)
    if path_step_key != body_step_key or path_attempt != body_attempt:
        raise HTTPException(
            status_code=422,
            detail="path procedure step identity must match request body",
        )


class _CommandBodyTooLarge(Exception):
    """The JSON command body crossed the executable request boundary."""


class _CommandBodyLimitMiddleware:
    """Bound command JSON while leaving FastAPI request schemas intact."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if max_body_bytes < 1:
            raise ValueError("command body byte limit must be positive")
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if not _is_payload_command_request(scope):
            await self._app(scope, receive, send)
            return
        content_length = _content_length(scope)
        if content_length is not None and content_length > self._max_body_bytes:
            await self._reject(scope, receive, send)
            return
        size_bytes = 0

        async def bounded_receive() -> Message:
            nonlocal size_bytes
            message = await receive()
            if message["type"] == "http.request":
                body = cast("bytes", message.get("body", b""))
                size_bytes += len(body)
                if size_bytes > self._max_body_bytes:
                    raise _CommandBodyTooLarge
            return message

        try:
            await self._app(scope, bounded_receive, send)
        except _CommandBodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "detail": (
                    f"command request body exceeds {self._max_body_bytes} byte limit"
                )
            },
        )
        await response(scope, receive, send)


def _is_payload_command_request(scope: Scope) -> bool:
    method = cast("str | None", scope.get("method"))
    if scope["type"] != "http" or method != "POST":
        return False
    path = cast("str", scope.get("path", ""))
    return path.endswith(("/invoke", "/hardware/execute"))


def _content_length(scope: Scope) -> int | None:
    headers = cast("tuple[tuple[bytes, bytes], ...]", scope.get("headers", ()))
    for name, value in headers:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _request_content_length(request: Request) -> int | None:
    return _content_length(request.scope)


class _SpaStaticFiles(StaticFiles):
    """Serve real files, then route extensionless GUI paths to the SPA."""

    @override
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if not _is_spa_path(path) or error.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and _is_spa_path(path):
            return await super().get_response("index.html", scope)
        return response


def _is_spa_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return not normalized.startswith("api/") and not PurePosixPath(normalized).suffix
