# pyright: reportUnknownMemberType=false, reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
"""Synchronous transport client for one project daemon."""

from __future__ import annotations

from collections.abc import Buffer, Callable, Iterable, Iterator
from types import TracebackType
from typing import Literal, Self
from urllib.parse import quote

import httpx2
import pyarrow as pa
from pydantic import BaseModel, ValidationError

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
    CalibrationCohortGetReceipt,
    CalibrationCohortListQuery,
    CalibrationCohortMemberListQuery,
    CalibrationCohortMemberPage,
    CalibrationCohortPage,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationAttentionReceipt,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationDeferReceipt,
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
from scopecat.daemon.hardware_receipt_wire import (
    decode_collect_receipt,
    decode_run_hardware_receipt,
)
from scopecat.daemon.points import (
    RunDomainDecisionCommand,
    RunDomainDecisionPage,
    RunDomainDecisionView,
    RunDomainEnqueueCommand,
    RunDomainQueueEntryView,
    RunDomainQueueView,
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
from scopecat.kernel.content_identity import (
    sha256_content_hash,
    sha256_content_hash_segments,
)
from scopecat.kernel.errors import SessionClosedError
from scopecat.measurements.recording_arrow import encode_measurement_append
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorRefreshRequest,
    AuthorRevisionBundle,
    AuthorRevisionRef,
    AuthorRevisionState,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import (
    BlobPayloadBody,
    CommandPayload,
    ContentEntry,
    InlinePayloadBody,
)
from scopecat.records.costs import RunMeasuredCosts
from scopecat.records.experiment_plan import (
    ExperimentPlanList,
    ExperimentPlanRevision,
    ExperimentPlanSave,
)
from scopecat.records.instrument import (
    InstrumentStateCacheReadback,
    InstrumentStateReadback,
    InstrumentStateSnapshot,
)
from scopecat.records.manual_preview import ManualPreviewFence, ManualPreviewValidity
from scopecat.records.measurement import MeasurementDatasetSchema
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetReceipt,
)
from scopecat.records.plan_ref import ExperimentPlanRef
from scopecat.records.run import RunSnapshot
from scopecat.records.sample import SampleArtifactRef, SampleRevision
from scopecat.records.sample_artifact import SampleArtifactPage
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
    RunHardwareInvoke,
)

type _PayloadUploadContent = bytes | tuple[Buffer, ...]

_API_PREFIX = "/api/v1"
_NEXT_OFFSET_HEADER = "X-Scopecat-Next-Offset"
_SNAPSHOT_SIZE_HEADER = "X-Scopecat-Snapshot-Size"
_PAYLOAD_UPLOAD_CHUNK_BYTES = 1024 * 1024
# The daemon owns operation deadlines; a read timeout would make a completed
# hardware command ambiguous to its caller.
_DEFAULT_TIMEOUT = httpx2.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)


class DaemonClientError(RuntimeError):
    """Base class for errors translated from daemon responses."""

    def __init__(self, detail: str, *, response: httpx2.Response) -> None:
        super().__init__(detail)
        self.detail = detail
        self.response = response


class DaemonNotFoundError(DaemonClientError):
    """The requested daemon object does not exist."""


class DaemonConflictError(DaemonClientError):
    """The command conflicts with current durable daemon state."""


class DaemonUnavailableError(DaemonClientError):
    """The daemon temporarily cannot complete a retry-safe request."""


class DaemonClient:
    """Thin synchronous wrapper around the versioned daemon HTTP API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float | httpx2.Timeout | None = _DEFAULT_TIMEOUT,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx2.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def is_closed(self) -> bool:
        """Whether this connection has been closed by its owner."""

        return self._http.is_closed

    def close(self) -> None:
        self._http.close()

    def experiment_plans(self, *, plan_id: str | None = None) -> ExperimentPlanList:
        return self._get_model(
            f"{_API_PREFIX}/experiment-plans",
            ExperimentPlanList,
            params={"plan_id": plan_id} if plan_id else {},
        )

    def experiment_plan(self, ref: ExperimentPlanRef) -> ExperimentPlanRevision:
        return self._post_model(
            f"{_API_PREFIX}/experiment-plans/read", ref, ExperimentPlanRevision
        )

    def save_experiment_plan(
        self, command: ExperimentPlanSave
    ) -> ExperimentPlanRevision:
        return self._post_model(
            f"{_API_PREFIX}/experiment-plans", command, ExperimentPlanRevision
        )

    def hide_experiment_plan(self, ref: ExperimentPlanRef) -> ExperimentPlanRef:
        return self._post_model(
            f"{_API_PREFIX}/experiment-plans/hide", ref, ExperimentPlanRef
        )

    def author_revision_state(self) -> AuthorRevisionState:
        return self._get_model(f"{_API_PREFIX}/author-revisions", AuthorRevisionState)

    def author_revision(self, ref: AuthorRevisionRef) -> AuthorRevisionBundle:
        return self._get_model(
            f"{_API_PREFIX}/author-revisions/{ref.content_hash}", AuthorRevisionBundle
        )

    def refresh_authors(self, *, expected_generation: int) -> AuthorRevisionState:
        return self._post_model(
            f"{_API_PREFIX}/author-revisions/refresh",
            AuthorRefreshRequest(expected_generation=expected_generation),
            AuthorRevisionState,
        )

    def analyze_author_revision(
        self, request: AuthorAnalysisRequest
    ) -> AuthorAnalysisReceipt:
        return self._post_model(
            f"{_API_PREFIX}/author-revisions/analyze", request, AuthorAnalysisReceipt
        )

    def health(self) -> DaemonHealth:
        return self._get_model(f"{_API_PREFIX}/health", DaemonHealth)

    def create_review(
        self,
        command: ReviewSessionCreateCommand,
    ) -> ReviewSessionView:
        return self._post_model(
            f"{_API_PREFIX}/reviews",
            command,
            ReviewSessionView,
        )

    def list_reviews(self) -> ReviewSessionListView:
        return self._get_model(f"{_API_PREFIX}/reviews", ReviewSessionListView)

    def get_review(self, session_id: str) -> ReviewSessionView:
        return self._get_model(
            f"{_API_PREFIX}/reviews/{quote(session_id, safe='')}",
            ReviewSessionView,
        )

    def enqueue_review_compile(
        self,
        session_id: str,
        command: ReviewCompileCommand,
    ) -> ReviewCompileReceipt:
        return self._post_model(
            f"{_API_PREFIX}/reviews/{quote(session_id, safe='')}/compile",
            command,
            ReviewCompileReceipt,
        )

    def claim_review_work(
        self,
        session_id: str,
        worker_id: str,
    ) -> ReviewWorkItem | None:
        response = self._request(
            "POST",
            f"{_API_PREFIX}/reviews/{quote(session_id, safe='')}/worker/claim",
            params={"worker_id": worker_id},
        )
        if response.content == b"null":
            return None
        return ReviewWorkItem.model_validate_json(response.content)

    def complete_review_work(
        self,
        session_id: str,
        command: ReviewCompletionCommand,
    ) -> ReviewSessionView:
        return self._post_model(
            f"{_API_PREFIX}/reviews/{quote(session_id, safe='')}/worker/complete",
            command,
            ReviewSessionView,
        )

    def heartbeat_review_worker(
        self,
        session_id: str,
        worker_id: str,
    ) -> ReviewHeartbeatReceipt:
        response = self._request(
            "POST",
            f"{_API_PREFIX}/reviews/{quote(session_id, safe='')}/worker/heartbeat",
            params={"worker_id": worker_id},
        )
        return ReviewHeartbeatReceipt.model_validate_json(response.content)

    def close_review_worker(
        self,
        session_id: str,
        worker_id: str,
    ) -> ReviewSessionCloseReceipt:
        response = self._request(
            "POST",
            f"{_API_PREFIX}/reviews/{quote(session_id, safe='')}/worker/close",
            params={"worker_id": worker_id},
        )
        return ReviewSessionCloseReceipt.model_validate_json(response.content)

    def submit_procedure(
        self,
        command: ProcedureSubmitCommand,
    ) -> ProcedureSubmitReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/procedures",
            command,
            ProcedureSubmitReceipt,
        )

    def query_calibration_status(
        self,
        query: CalibrationStatusQuery,
    ) -> CalibrationStatusReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/calibration-status/query",
            query,
            CalibrationStatusReceipt,
        )

    def create_calibration_cohort(
        self,
        command: CalibrationCohortCreateCommand,
    ) -> CalibrationCohortCreateReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/calibration-cohorts",
            command,
            CalibrationCohortCreateReceipt,
        )

    def get_calibration_cohort(
        self,
        cohort_id: str,
    ) -> CalibrationCohortGetReceipt:
        return self._get_model(
            (f"{_API_PREFIX}/calibration-cohorts/by-id/{quote(cohort_id, safe='')}"),
            CalibrationCohortGetReceipt,
        )

    def list_calibration_cohorts(
        self,
        query: CalibrationCohortListQuery,
    ) -> CalibrationCohortPage:
        params: dict[str, str | int] = {"limit": query.limit}
        if query.cursor is not None:
            params["cursor"] = query.cursor
        if query.fanout_scope is not None:
            params["fanout_scope"] = query.fanout_scope
        return self._get_model(
            f"{_API_PREFIX}/calibration-cohorts",
            CalibrationCohortPage,
            params=params,
        )

    def list_calibration_cohort_members(
        self,
        query: CalibrationCohortMemberListQuery,
    ) -> CalibrationCohortMemberPage:
        params: dict[str, str | int] = {"limit": query.limit}
        if query.cursor is not None:
            params["cursor"] = query.cursor
        return self._get_model(
            (
                f"{_API_PREFIX}/calibration-cohort-members/by-cohort/"
                f"{quote(query.cohort_id, safe='')}"
            ),
            CalibrationCohortMemberPage,
            params=params,
        )

    def list_ready_calibration_publications(
        self,
        query: CalibrationPublicationReadyQuery,
    ) -> CalibrationPublicationReadyPage:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/calibration-publications/ready/query",
            query,
            CalibrationPublicationReadyPage,
        )

    def get_calibration_publication(
        self,
        cohort_id: str,
    ) -> CalibrationPublicationGetReceipt:
        return self._get_model(
            (
                f"{_API_PREFIX}/calibration-publications/by-cohort/"
                f"{quote(cohort_id, safe='')}"
            ),
            CalibrationPublicationGetReceipt,
        )

    def publish_calibration(
        self,
        command: CalibrationPublicationCommand,
    ) -> CalibrationPublicationReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/calibration-publications/operations",
            command,
            CalibrationPublicationReceipt,
        )

    def calibration_publication_operation(
        self,
        operation_id: str,
    ) -> CalibrationPublicationReceipt:
        return self._get_model(
            (
                f"{_API_PREFIX}/calibration-publications/operations/"
                f"{quote(operation_id, safe='')}"
            ),
            CalibrationPublicationReceipt,
        )

    def require_calibration_publication_attention(
        self,
        command: CalibrationPublicationAttentionCommand,
    ) -> CalibrationPublicationAttentionReceipt:
        return self._post_model(
            (
                f"{_API_PREFIX}/calibration-publication-attentions/"
                f"{quote(command.cohort_id, safe='')}"
            ),
            command,
            CalibrationPublicationAttentionReceipt,
        )

    def retry_calibration_publication(
        self,
        command: CalibrationPublicationRetryCommand,
    ) -> CalibrationPublicationRetryReceipt:
        return self._post_model(
            (
                f"{_API_PREFIX}/calibration-publication-retries/"
                f"{quote(command.cohort_id, safe='')}"
            ),
            command,
            CalibrationPublicationRetryReceipt,
        )

    def defer_calibration_publication(
        self,
        command: CalibrationPublicationDeferCommand,
    ) -> CalibrationPublicationDeferReceipt:
        return self._post_model(
            (
                f"{_API_PREFIX}/calibration-publication-deferrals/"
                f"{quote(command.cohort_id, safe='')}"
            ),
            command,
            CalibrationPublicationDeferReceipt,
        )

    def list_procedures(
        self,
        query: ProcedureRunListQuery,
    ) -> ProcedureRunPage:
        params: dict[str, str | int] = {"limit": query.limit}
        if query.cursor is not None:
            params["cursor"] = query.cursor
        if query.state is not None:
            params["state"] = query.state
        if query.request_key is not None:
            params["request_key"] = query.request_key
        return self._get_model(
            f"{_API_PREFIX}/procedures",
            ProcedureRunPage,
            params=params,
        )

    def get_procedure(self, procedure_run_id: str) -> ProcedureRun:
        return self._get_model(
            f"{_API_PREFIX}/procedures/{quote(procedure_run_id, safe='')}",
            ProcedureRun,
        )

    def list_runnable_procedures(
        self,
        query: ProcedureRunnableQuery,
    ) -> ProcedureRunnablePage:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/procedures/runnable/query",
            query,
            ProcedureRunnablePage,
        )

    def create_procedure_schedule(
        self,
        command: ProcedureScheduleCreateCommand,
    ) -> ProcedureScheduleCreateReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/procedure-schedules",
            command,
            ProcedureScheduleCreateReceipt,
        )

    def list_procedure_schedules(
        self,
        query: ProcedureScheduleListQuery,
    ) -> ProcedureSchedulePage:
        params: dict[str, str | int] = {"limit": query.limit}
        if query.cursor is not None:
            params["cursor"] = query.cursor
        if query.state is not None:
            params["state"] = query.state
        return self._get_model(
            f"{_API_PREFIX}/procedure-schedules",
            ProcedureSchedulePage,
            params=params,
        )

    def list_due_procedure_schedules(
        self,
        query: ProcedureScheduleDueQuery,
    ) -> ProcedureScheduleDuePage:
        params: dict[str, str | int] = {"limit": query.limit}
        if query.cursor is not None:
            params["cursor"] = query.cursor
        if query.through_sequence is not None:
            params["through_sequence"] = query.through_sequence
        return self._get_model(
            f"{_API_PREFIX}/procedure-schedules/due",
            ProcedureScheduleDuePage,
            params=params,
        )

    def get_procedure_schedule(self, schedule_id: str) -> ProcedureSchedule:
        return self._get_model(
            f"{_API_PREFIX}/procedure-schedules/by-id/{quote(schedule_id, safe='')}",
            ProcedureSchedule,
        )

    def cancel_procedure_schedule(
        self,
        command: ProcedureScheduleCancelCommand,
    ) -> ProcedureScheduleCancelReceipt:
        return self._post_idempotent_model(
            (
                f"{_API_PREFIX}/procedure-schedule-cancellations/"
                f"{quote(command.schedule_id, safe='')}"
            ),
            command,
            ProcedureScheduleCancelReceipt,
        )

    def materialize_procedure_schedule(
        self,
        command: ProcedureScheduleMaterializeCommand,
    ) -> ProcedureScheduleMaterializeReceipt:
        return self._post_idempotent_model(
            (
                f"{_API_PREFIX}/procedure-schedule-materializations/"
                f"{quote(command.schedule_id, safe='')}"
            ),
            command,
            ProcedureScheduleMaterializeReceipt,
        )

    def list_procedure_step_attempts(
        self,
        procedure_run_id: str,
        query: ProcedureStepAttemptListQuery,
    ) -> ProcedureStepAttemptPage:
        params: dict[str, str | int] = {"limit": query.limit}
        if query.cursor is not None:
            params["cursor"] = query.cursor
        return self._get_model(
            (f"{_API_PREFIX}/procedures/{quote(procedure_run_id, safe='')}/steps"),
            ProcedureStepAttemptPage,
            params=params,
        )

    def acquire_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseAcquireCommand,
    ) -> ProcedureWorkerLeaseAcquireReceipt:
        return self._post_idempotent_model(
            self._procedure_path(command.procedure_run_id, "worker/lease/acquire"),
            command,
            ProcedureWorkerLeaseAcquireReceipt,
        )

    def heartbeat_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseHeartbeatCommand,
    ) -> ProcedureWorkerLeaseHeartbeatReceipt:
        return self._post_idempotent_model(
            self._procedure_path(command.procedure_run_id, "worker/lease/heartbeat"),
            command,
            ProcedureWorkerLeaseHeartbeatReceipt,
        )

    def release_procedure_worker_lease(
        self,
        command: ProcedureWorkerLeaseReleaseCommand,
    ) -> ProcedureWorkerLeaseReleaseReceipt:
        return self._post_idempotent_model(
            self._procedure_path(command.procedure_run_id, "worker/lease/release"),
            command,
            ProcedureWorkerLeaseReleaseReceipt,
        )

    def begin_procedure_step(
        self,
        command: ProcedureStepBeginCommand,
    ) -> ProcedureStepBeginReceipt:
        return self._post_idempotent_model(
            self._procedure_path(command.procedure_run_id, "steps/begin"),
            command,
            ProcedureStepBeginReceipt,
        )

    def complete_procedure_step(
        self,
        command: ProcedureStepCompleteCommand,
    ) -> ProcedureStepCompleteReceipt:
        return self._post_idempotent_model(
            self._procedure_step_path(command, "complete"),
            command,
            ProcedureStepCompleteReceipt,
        )

    def fail_procedure_step(
        self,
        command: ProcedureStepFailCommand,
    ) -> ProcedureStepFailReceipt:
        return self._post_idempotent_model(
            self._procedure_step_path(command, "fail"),
            command,
            ProcedureStepFailReceipt,
        )

    def require_procedure_step_attention(
        self,
        command: ProcedureStepAttentionCommand,
    ) -> ProcedureStepAttentionReceipt:
        return self._post_idempotent_model(
            self._procedure_step_path(command, "attention"),
            command,
            ProcedureStepAttentionReceipt,
        )

    def wait_procedure_step_resources(
        self, command: ProcedureStepResourceWaitCommand
    ) -> ProcedureStepResourceWaitReceipt:
        return self._post_idempotent_model(
            self._procedure_step_path(command, "resources/wait"),
            command,
            ProcedureStepResourceWaitReceipt,
        )

    def wait_procedure_step_input(
        self,
        command: ProcedureStepInputWaitCommand,
    ) -> ProcedureStepInputWaitReceipt:
        return self._post_idempotent_model(
            self._procedure_step_path(command, "input/wait"),
            command,
            ProcedureStepInputWaitReceipt,
        )

    def submit_procedure_step_input(
        self,
        command: ProcedureStepInputSubmitCommand,
    ) -> ProcedureStepInputSubmitReceipt:
        return self._post_idempotent_model(
            self._procedure_step_path(command, "input"),
            command,
            ProcedureStepInputSubmitReceipt,
        )

    def retry_procedure_step_attention(
        self,
        command: ProcedureStepAttentionRetryCommand,
    ) -> ProcedureStepAttentionRetryReceipt:
        return self._post_idempotent_model(
            self._procedure_step_path(command, "retry"),
            command,
            ProcedureStepAttentionRetryReceipt,
        )

    def require_procedure_run_attention(
        self,
        command: ProcedureRunAttentionCommand,
    ) -> ProcedureRunAttentionReceipt:
        return self._post_idempotent_model(
            self._procedure_path(command.procedure_run_id, "attention"),
            command,
            ProcedureRunAttentionReceipt,
        )

    def cancel_procedure(
        self, command: ProcedureCancelCommand
    ) -> ProcedureCancelReceipt:
        return self._post_idempotent_model(
            self._procedure_path(command.procedure_run_id, "cancel"),
            command,
            ProcedureCancelReceipt,
        )

    def close_procedure(
        self,
        command: ProcedureCloseCommand,
    ) -> ProcedureCloseReceipt:
        return self._post_idempotent_model(
            self._procedure_path(command.procedure_run_id, "close"),
            command,
            ProcedureCloseReceipt,
        )

    def config_registry(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigRegistryPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/config-registry",
            ConfigRegistryPage,
            params=params,
        )

    def config_activation_history(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ConfigActivationPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/config-registry/activations",
            ConfigActivationPage,
            params=params,
        )

    def active_config(self) -> ActiveConfigView:
        return self._get_model(
            f"{_API_PREFIX}/config-registry/active",
            ActiveConfigView,
        )

    def config_entry(self, entry_id: str) -> ConfigEntryView:
        return self._get_model(
            f"{_API_PREFIX}/config-registry/entries/{quote(entry_id, safe='')}",
            ConfigEntryView,
        )

    def save_context(self, command: ConfigContextSaveCommand) -> ConfigEntryView:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/config-registry/contexts",
            command,
            ConfigEntryView,
        )

    def preview_structure(
        self, plan: ParameterStructurePlan
    ) -> ParameterStructurePreview:
        return self._post_model(
            f"{_API_PREFIX}/config-registry/contexts/structure/preview",
            plan,
            ParameterStructurePreview,
        )

    def resolve_context(
        self, command: ConfigContextResolveCommand
    ) -> ConfigContextResolution:
        return self._post_model(
            f"{_API_PREFIX}/config-registry/contexts/resolve",
            command,
            ConfigContextResolution,
        )

    def publish_config(
        self,
        command: ConfigPublishCommand,
    ) -> ConfigPublishReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/config-registry/publish-operations",
            command,
            ConfigPublishReceipt,
        )

    def config_publish_operation(
        self,
        operation_id: str,
    ) -> ConfigPublishReceipt:
        return self._get_model(
            f"{_API_PREFIX}/config-registry/publish-operations/"
            f"{quote(operation_id, safe='')}",
            ConfigPublishReceipt,
        )

    def migrate_instrument_inventory(
        self,
        command: InstrumentInventoryMigrationCommand,
    ) -> InstrumentInventoryMigrationReceipt:
        return self._post_model(
            f"{_API_PREFIX}/config-registry/instrument-inventory-migrations",
            command,
            InstrumentInventoryMigrationReceipt,
        )

    def preview_config_draft(
        self,
        command: ConfigDraftCommand,
    ) -> ConfigDraftPreview:
        return self._post_model(
            f"{_API_PREFIX}/config-registry/drafts/preview",
            command,
            ConfigDraftPreview,
        )

    def activate_config_entry(
        self,
        command: ConfigEntryActivationCommand,
    ) -> ConfigActivationReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/config-registry/activation-operations",
            command,
            ConfigActivationReceipt,
        )

    def config_activation_operation(
        self,
        operation_id: str,
    ) -> ConfigActivationReceipt:
        return self._get_model(
            f"{_API_PREFIX}/config-registry/activation-operations/"
            f"{quote(operation_id, safe='')}",
            ConfigActivationReceipt,
        )

    def release_instruments(
        self, command: InstrumentReleaseCommand
    ) -> InstrumentReleaseReceipt:
        return self._post_model(
            f"{_API_PREFIX}/instruments/release", command, InstrumentReleaseReceipt
        )

    def list_instruments(self) -> InstrumentListView:
        return self._get_model(
            f"{_API_PREFIX}/instruments",
            InstrumentListView,
        )

    def list_samples(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> SamplePage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/samples",
            SamplePage,
            params=params,
        )

    def import_sample_artifact(
        self, content: bytes, *, artifact_id: str, title: str, media_type: str
    ) -> SampleArtifactRef:
        response = self._request(
            "POST",
            f"{_API_PREFIX}/sample-artifacts",
            params={
                "artifact_id": artifact_id,
                "title": title,
                "media_type": media_type,
            },
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        return SampleArtifactRef.model_validate_json(response.content)

    def sample_artifacts(self, sample_id: str, revision: int) -> SampleArtifactPage:
        return self._get_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}"
            f"/revisions/{revision}/artifacts",
            SampleArtifactPage,
        )

    def sample_artifact_content(
        self, sample_id: str, revision: int, artifact_id: str
    ) -> bytes:
        return self._request(
            "GET",
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}"
            f"/revisions/{revision}/artifacts/content",
            params={"artifact_id": artifact_id},
        ).content

    def get_sample(self, sample_id: str) -> SampleView:
        return self._get_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}",
            SampleView,
        )

    def sample_revisions(
        self,
        sample_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> SampleRevisionPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}/revisions",
            SampleRevisionPage,
            params=params,
        )

    def sample_revision(self, sample_id: str, revision: int) -> SampleRevision:
        return self._get_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}/revisions/{revision}",
            SampleRevision,
        )

    def sample_analyses(
        self,
        sample_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> SampleAnalysisPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}/analyses",
            SampleAnalysisPage,
            params=params,
        )

    def sample_analysis(self, sample_id: str, selector: str) -> SampleAnalysisView:
        return self._get_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}/analyses/"
            f"{quote(selector, safe='')}",
            SampleAnalysisView,
        )

    def save_sample_analysis(
        self,
        sample_id: str,
        command: AnalysisSaveCommand,
    ) -> AnalysisSaveReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}/analyses",
            command,
            AnalysisSaveReceipt,
        )

    def sample_analysis_contents(
        self,
        sample_id: str,
        analysis_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ProjectAnalysisContentPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            self._sample_analysis_content_path(sample_id, analysis_id, ""),
            ProjectAnalysisContentPage,
            params=params,
        )

    def sample_analysis_content(
        self,
        sample_id: str,
        analysis_id: str,
        selector: str,
    ) -> ContentEntry:
        return self._get_model(
            self._sample_analysis_content_path(sample_id, analysis_id, selector),
            ContentEntry,
        )

    def sample_analysis_content_bytes(
        self,
        sample_id: str,
        analysis_id: str,
        selector: str,
    ) -> AnalysisContentBytesView:
        path = self._sample_analysis_content_path(
            sample_id,
            analysis_id,
            selector,
        )
        return self._get_model(f"{path}/bytes", AnalysisContentBytesView)

    @staticmethod
    def _sample_analysis_content_path(
        sample_id: str,
        analysis_id: str,
        selector: str,
    ) -> str:
        path = (
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}/analyses/"
            f"{quote(analysis_id, safe='')}/contents"
        )
        if selector:
            return f"{path}/{quote(selector, safe='')}"
        return path

    def create_sample(
        self,
        command: SampleCreateCommand,
    ) -> SampleMutationReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/samples",
            command,
            SampleMutationReceipt,
        )

    def revise_sample(
        self,
        sample_id: str,
        command: SampleReviseCommand,
    ) -> SampleMutationReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/samples/{quote(sample_id, safe='')}/revisions",
            command,
            SampleMutationReceipt,
        )

    def driver_catalog(self) -> DriverCatalog:
        return self._get_model(
            f"{_API_PREFIX}/instrument-drivers",
            DriverCatalog,
        )

    def probe_driver(
        self,
        command: InstrumentDriverProbeCommand,
    ) -> InstrumentDriverProbeReceipt:
        return self._post_model(
            f"{_API_PREFIX}/instrument-drivers/probe",
            command,
            InstrumentDriverProbeReceipt,
        )

    def get_instrument(self, instrument_id: str) -> InstrumentView:
        return self._get_model(
            f"{_API_PREFIX}/instruments/{quote(instrument_id, safe='')}",
            InstrumentView,
        )

    def resolve_instrument_contracts(
        self,
        config: ConfigProfileSnapshot,
    ) -> InstrumentContractCatalog:
        return self._post_model(
            f"{_API_PREFIX}/instrument-contracts/resolve",
            InstrumentContractCatalogRequest(config=config),
            InstrumentContractCatalog,
        )

    def manual_preview_validity(
        self, fence: ManualPreviewFence
    ) -> ManualPreviewValidity:
        """Check relevant manual events without querying or changing instruments."""
        return self._post_model(
            f"{_API_PREFIX}/experiment-launcher/validity", fence, ManualPreviewValidity
        )

    def open_instrument_session(
        self,
        command: InstrumentSessionOpenCommand,
    ) -> InstrumentSessionOpenReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/instrument-sessions",
            command,
            InstrumentSessionOpenReceipt,
        )

    def renew_instrument_session(
        self,
        session_id: str,
    ) -> InstrumentSessionLeaseReceipt:
        return self._post_empty_idempotent_model(
            (
                f"{_API_PREFIX}/instrument-sessions/"
                f"{quote(session_id, safe='')}/heartbeat"
            ),
            InstrumentSessionLeaseReceipt,
        )

    def read_instrument_state(
        self,
        session_id: str,
        instrument_id: str,
    ) -> InstrumentStateSnapshot:
        return self._get_model(
            self._instrument_session_path(
                session_id,
                instrument_id,
                "state",
            ),
            InstrumentStateSnapshot,
        )

    def read_instrument_state_members(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentStateReadCommand,
    ) -> InstrumentStateReadback:
        return self._post_model(
            self._instrument_session_path(
                session_id,
                instrument_id,
                "state/read",
            ),
            command,
            InstrumentStateReadback,
        )

    def read_observed_instrument_state_members(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentStateReadCommand,
    ) -> InstrumentStateCacheReadback:
        return self._post_model(
            self._instrument_session_path(
                session_id,
                instrument_id,
                "state/observed",
            ),
            command,
            InstrumentStateCacheReadback,
        )

    def apply_instrument_state(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentStateCommand,
    ) -> ApplyReceipt:
        return self._post_idempotent_model(
            self._instrument_session_path(
                session_id,
                instrument_id,
                "state/apply",
            ),
            command,
            ApplyReceipt,
        )

    def apply_instrument_configured_defaults(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentConfiguredDefaultsApplyCommand,
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        return self._post_idempotent_model(
            self._instrument_session_path(
                session_id,
                instrument_id,
                "configured-defaults/apply",
            ),
            command,
            InstrumentConfiguredDefaultsApplyReceipt,
        )

    def invoke_instrument(
        self,
        session_id: str,
        instrument_id: str,
        command: InvokeCommand,
    ) -> InvokeReceipt:
        command = self._externalize_invoke_command(session_id, command)
        return self._post_idempotent_model(
            self._instrument_session_path(
                session_id,
                instrument_id,
                "invoke",
            ),
            command,
            InvokeReceipt,
        )

    def collect_instrument(
        self,
        session_id: str,
        instrument_id: str,
        intent: InteractiveCollectIntent,
    ) -> CollectReceipt:
        response = self._post_idempotent_response(
            self._instrument_session_path(
                session_id,
                instrument_id,
                "collect",
            ),
            intent,
        )
        return decode_collect_receipt(response.content)

    def close_instrument_session(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return self._post_empty_idempotent_model(
            (f"{_API_PREFIX}/instrument-sessions/{quote(session_id, safe='')}/close"),
            InstrumentSessionEndReceipt,
        )

    def abort_instrument_session(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return self._post_empty_idempotent_model(
            (f"{_API_PREFIX}/instrument-sessions/{quote(session_id, safe='')}/abort"),
            InstrumentSessionEndReceipt,
        )

    def resolve_instrument_session_attention(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        response = self._request(
            "POST",
            (
                f"{_API_PREFIX}/instrument-sessions/"
                f"{quote(session_id, safe='')}/attention"
            ),
        )
        return InstrumentSessionEndReceipt.model_validate_json(response.content)

    def list_runs(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ControlRunState | None = None,
        sample_id: str | None = None,
    ) -> RunSummaryPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        if state is not None:
            params["state"] = state
        if sample_id is not None:
            params["sample_id"] = sample_id
        return self._get_model(f"{_API_PREFIX}/runs", RunSummaryPage, params=params)

    def get_run(self, run_id: str) -> RunDetail:
        return self._get_model(f"{_API_PREFIX}/runs/{run_id}", RunDetail)

    def run_cancellation(self, run_id: str) -> RunCancellationState:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/cancellation",
            RunCancellationState,
        )

    def cancel_run(self, run_id: str) -> RunCancellationReceipt:
        return self._post_empty_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/cancel",
            RunCancellationReceipt,
        )

    def run_config(self, run_id: str) -> RunConfigView:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/config",
            RunConfigView,
        )

    def run_request(self, run_id: str) -> RunRequestView:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/request",
            RunRequestView,
        )

    def run_contents(
        self,
        run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
        role: Literal["artifact", "dataset", "record"] | None = None,
        kind: str | None = None,
    ) -> RunContentPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        if role is not None:
            params["role"] = role
        if kind is not None:
            params["kind"] = kind
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/contents",
            RunContentPage,
            params=params,
        )

    def run_content(
        self,
        run_id: str,
        *,
        role: Literal["artifact", "dataset", "record"],
        content_id: str,
    ) -> ContentEntry:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/contents/"
            f"{role}/{quote(content_id, safe='')}",
            ContentEntry,
        )

    def analyses(
        self,
        run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> RunAnalysisPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/analyses",
            RunAnalysisPage,
            params=params,
        )

    def analysis(self, run_id: str, selector: str) -> RunAnalysisView:
        selected_run = quote(run_id, safe="")
        selected_analysis = quote(selector, safe="")
        return self._get_model(
            f"{_API_PREFIX}/runs/{selected_run}/analyses/{selected_analysis}",
            RunAnalysisView,
        )

    def save_analysis(
        self,
        run_id: str,
        command: AnalysisSaveCommand,
    ) -> AnalysisSaveReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/analyses",
            command,
            AnalysisSaveReceipt,
        )

    def project_analyses(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ProjectAnalysisPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/analyses",
            ProjectAnalysisPage,
            params=params,
        )

    def project_analysis(self, selector: str) -> ProjectAnalysisView:
        return self._get_model(
            f"{_API_PREFIX}/analyses/{quote(selector, safe='')}",
            ProjectAnalysisView,
        )

    def project_analysis_contents(
        self,
        analysis_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ProjectAnalysisContentPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/analyses/{quote(analysis_id, safe='')}/contents",
            ProjectAnalysisContentPage,
            params=params,
        )

    def project_analysis_content(
        self,
        analysis_id: str,
        selector: str,
    ) -> ContentEntry:
        selected_analysis = quote(analysis_id, safe="")
        selected_content = quote(selector, safe="")
        return self._get_model(
            f"{_API_PREFIX}/analyses/{selected_analysis}/contents/{selected_content}",
            ContentEntry,
        )

    def save_project_analysis(
        self,
        command: AnalysisSaveCommand,
    ) -> AnalysisSaveReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/analyses",
            command,
            AnalysisSaveReceipt,
        )

    def project_analysis_content_bytes(
        self,
        analysis_id: str,
        selector: str,
    ) -> AnalysisContentBytesView:
        selected_analysis = quote(analysis_id, safe="")
        selected_content = quote(selector, safe="")
        return self._get_model(
            f"{_API_PREFIX}/analyses/{selected_analysis}/contents/"
            f"{selected_content}/bytes",
            AnalysisContentBytesView,
        )

    def artifact_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactBytesView:
        return self._artifact_content(
            run_id,
            selector,
            representation="bytes",
            expected_kind=expected_kind,
            model=RunArtifactBytesView,
        )

    def artifact_text(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactTextResult:
        return self._artifact_content(
            run_id,
            selector,
            representation="text",
            expected_kind=expected_kind,
            model=RunArtifactTextResult,
        )

    def artifact_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunArtifactJsonResult:
        return self._artifact_content(
            run_id,
            selector,
            representation="json",
            expected_kind=expected_kind,
            model=RunArtifactJsonResult,
        )

    def _artifact_content[ArtifactViewT: BaseModel](
        self,
        run_id: str,
        selector: str,
        *,
        representation: str,
        expected_kind: str | None,
        model: type[ArtifactViewT],
    ) -> ArtifactViewT:
        selected_run = quote(run_id, safe="")
        selected_artifact = quote(selector, safe="")
        params: dict[str, str | int] | None = (
            None if expected_kind is None else {"expected_kind": expected_kind}
        )
        return self._get_model(
            (
                f"{_API_PREFIX}/runs/{selected_run}/artifacts/"
                f"{selected_artifact}/{representation}"
            ),
            model,
            params=params,
        )

    def record_json(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunRecordJsonResult:
        selected_run = quote(run_id, safe="")
        selected_record = quote(selector, safe="")
        params: dict[str, str | int] | None = (
            None if expected_kind is None else {"expected_kind": expected_kind}
        )
        return self._get_model(
            f"{_API_PREFIX}/runs/{selected_run}/records/{selected_record}/json",
            RunRecordJsonResult,
            params=params,
        )

    def dataset_content(
        self,
        run_id: str,
        selector: str,
    ) -> RunMeasurementDatasetResult:
        selected_run = quote(run_id, safe="")
        selected_dataset = quote(selector, safe="")
        return self._get_model(
            f"{_API_PREFIX}/runs/{selected_run}/datasets/{selected_dataset}",
            RunMeasurementDatasetResult,
        )

    def dataset_bytes(
        self,
        run_id: str,
        selector: str,
        *,
        expected_kind: str | None = None,
    ) -> RunDatasetBytesView:
        selected_run = quote(run_id, safe="")
        selected_dataset = quote(selector, safe="")
        params: dict[str, str | int] | None = (
            None if expected_kind is None else {"expected_kind": expected_kind}
        )
        return self._get_model(
            f"{_API_PREFIX}/runs/{selected_run}/datasets/{selected_dataset}/bytes",
            RunDatasetBytesView,
            params=params,
        )

    def attach(
        self,
        run_id: str,
        command: RunAttachmentCommand,
    ) -> ContentEntry:
        return self._post_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/attachments",
            command,
            ContentEntry,
        )

    def parameter_proposals(
        self,
        run_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> ParameterProposalPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/parameter-proposals",
            ParameterProposalPage,
            params=params,
        )

    def parameter_proposal(
        self,
        run_id: str,
        proposal_id: str,
    ) -> ParameterProposalView:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/parameter-proposals/"
            f"{quote(proposal_id, safe='')}",
            ParameterProposalView,
        )

    def resolve_attention(
        self,
        run_id: str,
        command: AttentionResolutionCommand,
    ) -> AttentionResolutionReceipt:
        return self._post_model(
            f"{_API_PREFIX}/runs/{run_id}/attention",
            command,
            AttentionResolutionReceipt,
        )

    def measurement_arrow(
        self,
        run_id: str,
        query: MeasurementArrowQuery,
    ) -> tuple[pa.Table, int | None, int]:
        response = self._request(
            "POST",
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/measurements/arrow",
            json=query.model_dump(mode="json"),
        )
        table = pa.ipc.open_stream(response.content).read_all()
        encoded_next_offset = response.headers.get(_NEXT_OFFSET_HEADER)
        next_offset = None if encoded_next_offset is None else int(encoded_next_offset)
        encoded_snapshot_size = response.headers.get(_SNAPSHOT_SIZE_HEADER)
        if encoded_snapshot_size is None:
            raise ValueError("measurement Arrow response has no snapshot size")
        return table, next_offset, int(encoded_snapshot_size)

    def measurement_preview(
        self,
        run_id: str,
        *,
        limit: int = 1,
    ) -> MeasurementPreview:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/measurements/preview",
            MeasurementPreview,
            params={"limit": limit},
        )

    def measurement_trace_preview(
        self,
        run_id: str,
        query: MeasurementTracePreviewQuery,
    ) -> MeasurementTracePreview:
        return self._post_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/measurements/traces/query",
            query,
            MeasurementTracePreview,
        )

    def replay_events(
        self,
        *,
        limit: int = 100,
        after: int | None = None,
        run_id: str | None = None,
        latest: bool = False,
    ) -> EventPage:
        params: dict[str, str | int] = {"limit": limit}
        if after is not None:
            params["after"] = after
        if run_id is not None:
            params["run_id"] = run_id
        if latest:
            params["latest"] = "true"
        return self._get_model(f"{_API_PREFIX}/events", EventPage, params=params)

    def submit_run(self, submission: RunSubmission) -> RunAdmission:
        return self._post_model(
            f"{_API_PREFIX}/runs",
            submission,
            RunAdmission,
        )

    def start_executor(
        self,
        run_id: str,
        request: ExecutorStartRequest,
    ) -> ExecutorLease:
        lease = self._post_model(
            f"{_API_PREFIX}/runs/{run_id}/executor/start",
            request,
            ExecutorLease,
        )
        if lease.run_id != run_id:
            raise ValueError("executor lease does not match its request")
        return lease

    def heartbeat_executor(
        self,
        run_id: str,
        heartbeat: ExecutorHeartbeat,
    ) -> ExecutorLease:
        return self._post_model(
            f"{_API_PREFIX}/runs/{run_id}/executor/heartbeat",
            heartbeat,
            ExecutorLease,
        )

    def get_run_coverage(self, run_id: str) -> RunCoverageState:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/coverage",
            RunCoverageState,
        )

    def get_worker_diagnostics(self, generation: str, *, raw: bool = False) -> bytes:
        return self._request(
            "GET",
            f"{_API_PREFIX}/instrument-workers/"
            f"{quote(generation, safe='')}/diagnostics",
            params={"raw": "true" if raw else "false"},
        ).content

    def get_run_measured_costs(self, run_id: str) -> RunMeasuredCosts:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/measured-costs",
            RunMeasuredCosts,
        )

    def get_run_failure_evidence(self, run_id: str) -> RunFailureEvidence:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/failure-evidence",
            RunFailureEvidence,
        )

    def get_run_execution_segments(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunExecutionSegmentPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/execution-segments",
            RunExecutionSegmentPage,
            params=params,
        )

    def advance_run_coverage(
        self,
        run_id: str,
        command: RunCoverageAdvanceCommand,
    ) -> RunCoverageState:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/coverage/advance",
            command,
            RunCoverageState,
        )

    def get_run_recovery_groups(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunRecoveryGroupPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/recovery-groups",
            RunRecoveryGroupPage,
            params=params,
        )

    def commit_run_recovery_groups(
        self,
        run_id: str,
        command: RunRecoveryGroupCommitCommand,
    ) -> RunRecoveryGroupCommitReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/recovery-groups",
            command,
            RunRecoveryGroupCommitReceipt,
        )

    def get_run_domain_job_transitions(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunDomainJobTransitionPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/domain-jobs/transitions",
            RunDomainJobTransitionPage,
            params=params,
        )

    def get_run_domain_jobs(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunDomainJobStatePage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/domain-jobs",
            RunDomainJobStatePage,
            params=params,
        )

    def commit_run_domain_job_transitions(
        self,
        run_id: str,
        command: RunDomainJobTransitionBatchCommand,
    ) -> RunDomainJobTransitionBatchReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/domain-jobs/transitions",
            command,
            RunDomainJobTransitionBatchReceipt,
        )

    def get_run_point_plan(self, run_id: str) -> RunPointPlanView:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/point-plan",
            RunPointPlanView,
        )

    def append_run_domain_decision(
        self,
        run_id: str,
        command: RunDomainDecisionCommand,
    ) -> RunDomainDecisionView:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/point-plan/decisions",
            command,
            RunDomainDecisionView,
        )

    def get_run_domain_decisions(
        self,
        run_id: str,
        *,
        limit: int = 64,
        before: int | None = None,
    ) -> RunDomainDecisionPage:
        params: dict[str, str | int] = {"limit": limit}
        if before is not None:
            params["before"] = before
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/point-plan/decisions",
            RunDomainDecisionPage,
            params=params,
        )

    def close_run_point_plan(
        self,
        run_id: str,
        command: RunPointPlanCloseCommand,
    ) -> RunPointPlanView:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/point-plan/close",
            command,
            RunPointPlanView,
        )

    def get_run_domain_queue(self, run_id: str) -> RunDomainQueueView:
        return self._get_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/point-plan/queue",
            RunDomainQueueView,
        )

    def get_next_queued_run_domain(
        self,
        run_id: str,
    ) -> RunDomainQueueEntryView | None:
        response = self._request(
            "GET",
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/point-plan/queue/next",
        )
        if response.content == b"null":
            return None
        return RunDomainQueueEntryView.model_validate_json(response.content)

    def enqueue_run_domain(
        self,
        run_id: str,
        command: RunDomainEnqueueCommand,
    ) -> RunDomainQueueEntryView:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/point-plan/queue",
            command,
            RunDomainQueueEntryView,
        )

    def provision_run_instruments(
        self,
        run_id: str,
        command: RunInstrumentProvisionCommand,
    ) -> RunInstrumentProvisionReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/instruments/provision",
            command,
            RunInstrumentProvisionReceipt,
        )

    def execute_run_hardware(
        self,
        run_id: str,
        command: RunHardwareBatchCommand,
    ) -> RunHardwareBatchReceipt:
        command = self._externalize_hardware_command(
            run_id,
            command.lease_id,
            command,
        )
        response = self._post_idempotent_response(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/hardware/execute",
            command,
        )
        return decode_run_hardware_receipt(response.content)

    def finish_run_hardware(
        self,
        run_id: str,
        command: RunHardwareFinishCommand,
    ) -> RunHardwareFinalizationReceipt:
        return self._post_idempotent_model(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/hardware/finish",
            command,
            RunHardwareFinalizationReceipt,
        )

    def initialize_measurements(
        self,
        run_id: str,
        command: MeasurementHeaderCommand,
    ) -> MeasurementDatasetReceipt:
        return self._post_model(
            f"{_API_PREFIX}/runs/{run_id}/measurements/header",
            command,
            MeasurementDatasetReceipt,
        )

    def ingest_measurements(
        self,
        run_id: str,
        *,
        lease_id: str,
        append: MeasurementDatasetAppend,
        dataset_schema: MeasurementDatasetSchema,
    ) -> MeasurementIngestReceipt:
        """Append at the next acquisition-log position (not retry-idempotent).

        Received records may still be volatile; only ``durable_record_count``
        confirms storage. After an uncertain response, reconcile the live/durable
        prefix and executor state before sending another range. A conflict alone
        does not prove that the previous content was accepted. Ingest does not
        advance scan coverage or seal the dataset.
        """
        response = self._request(
            "POST",
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/measurements/ingest",
            content=encode_measurement_append(append, dataset_schema),
            headers={
                "Content-Type": "application/vnd.apache.arrow.file",
                "X-Scopecat-Lease-ID": lease_id,
            },
        )
        return MeasurementIngestReceipt.model_validate_json(response.content)

    def flush_measurements(
        self,
        run_id: str,
        command: MeasurementFlushCommand,
    ) -> MeasurementFlushReceipt:
        """Persist the buffered prefix without advancing coverage or sealing it.

        Repeating a successful flush is safe under the same active lease; the
        durable count remains unchanged and no new chunk receipts are returned.
        """
        return self._post_model(
            f"{_API_PREFIX}/runs/{run_id}/measurements/flush",
            command,
            MeasurementFlushReceipt,
        )

    def seal_measurements(
        self,
        run_id: str,
        command: MeasurementSealCommand,
    ) -> MeasurementDatasetReceipt:
        return self._post_model(
            f"{_API_PREFIX}/runs/{run_id}/measurements/seal",
            command,
            MeasurementDatasetReceipt,
        )

    def commit_terminal(
        self,
        run_id: str,
        command: TerminalRunCommitCommand,
    ) -> RunSnapshot:
        return self._post_model(
            f"{_API_PREFIX}/runs/{run_id}/terminal",
            command,
            RunSnapshot,
        )

    def _get_model[ModelT: BaseModel](
        self,
        path: str,
        model: type[ModelT],
        *,
        params: dict[str, str | int] | None = None,
    ) -> ModelT:
        response = self._request("GET", path, params=params)
        return model.model_validate_json(response.content)

    def _externalize_invoke_command(
        self,
        session_id: str,
        command: InvokeCommand,
    ) -> InvokeCommand:
        return command.model_copy(
            update={
                "payloads": self._externalize_payloads(
                    command.payloads,
                    upload_object=lambda content, content_hash: (
                        self._put_session_payload_object(
                            session_id,
                            command.command_id,
                            content,
                            content_hash=content_hash,
                        )
                    ),
                )
            }
        )

    def _externalize_hardware_command(
        self,
        run_id: str,
        lease_id: str,
        command: RunHardwareBatchCommand,
    ) -> RunHardwareBatchCommand:
        uploaded: dict[str, PayloadObjectReceipt] = {}
        actions = tuple(
            action.model_copy(
                update={
                    "payloads": self._externalize_payloads(
                        action.payloads,
                        upload_object=lambda content, content_hash: (
                            self._put_run_payload_object(
                                run_id,
                                lease_id,
                                command.batch.operation_id,
                                content,
                                content_hash=content_hash,
                            )
                        ),
                        uploaded=uploaded,
                    )
                }
            )
            if isinstance(action, RunHardwareInvoke)
            else action
            for action in command.batch.actions
        )
        return command.model_copy(
            update={"batch": command.batch.model_copy(update={"actions": actions})}
        )

    def _externalize_payloads(
        self,
        payloads: dict[str, CommandPayload],
        *,
        upload_object: Callable[[_PayloadUploadContent, str], PayloadObjectReceipt],
        uploaded: dict[str, PayloadObjectReceipt] | None = None,
    ) -> dict[str, CommandPayload]:
        externalized: dict[str, CommandPayload] = {}
        uploaded_by_hash = uploaded if uploaded is not None else {}
        for payload_id, payload in payloads.items():
            if isinstance(payload.body, BlobPayloadBody):
                externalized[payload_id] = payload
                continue
            receipt = uploaded_by_hash.get(payload.content_hash)
            if receipt is None:
                content = (
                    payload.inline_bytes()
                    if isinstance(payload.body, InlinePayloadBody)
                    else payload.inline_segments()
                )
                receipt = upload_object(content, payload.content_hash)
                uploaded_by_hash[payload.content_hash] = receipt
            externalized[payload_id] = payload.model_copy(
                update={"body": BlobPayloadBody(ref=receipt.ref)}
            )
        return externalized

    def _put_session_payload_object(
        self,
        session_id: str,
        command_id: str,
        content: _PayloadUploadContent,
        *,
        content_hash: str,
    ) -> PayloadObjectReceipt:
        return self._put_payload_object(
            (
                f"{_API_PREFIX}/instrument-sessions/"
                f"{quote(session_id, safe='')}/payload-objects"
            ),
            content,
            content_hash=content_hash,
            headers={"X-Scopecat-Payload-Command-ID": command_id},
        )

    def _put_run_payload_object(
        self,
        run_id: str,
        lease_id: str,
        operation_id: str,
        content: _PayloadUploadContent,
        *,
        content_hash: str,
    ) -> PayloadObjectReceipt:
        return self._put_payload_object(
            f"{_API_PREFIX}/runs/{quote(run_id, safe='')}/payload-objects",
            content,
            content_hash=content_hash,
            headers={
                "X-Scopecat-Lease-ID": lease_id,
                "X-Scopecat-Payload-Operation-ID": operation_id,
            },
        )

    def _put_payload_object(
        self,
        scope_path: str,
        content: _PayloadUploadContent,
        *,
        content_hash: str,
        headers: dict[str, str] | None = None,
    ) -> PayloadObjectReceipt:
        """Publish one immutable payload through an authorized scope."""

        if isinstance(content, bytes):
            actual_hash = sha256_content_hash(content)
            size_bytes = len(content)
        else:
            actual_hash = sha256_content_hash_segments(content)
            size_bytes = sum(memoryview(segment).nbytes for segment in content)
        if content_hash != actual_hash:
            raise ValueError("payload content does not match content_hash")
        path = f"{scope_path}/{actual_hash.removeprefix('sha256:')}"
        response = self._put_payload_content(path, content, headers=headers)
        receipt = PayloadObjectReceipt.model_validate_json(response.content)
        if receipt.content_hash != content_hash or receipt.size_bytes != size_bytes:
            raise ValueError("daemon payload object receipt does not match upload")
        return receipt

    @staticmethod
    def _instrument_session_path(
        session_id: str,
        instrument_id: str,
        suffix: str,
    ) -> str:
        return (
            f"{_API_PREFIX}/instrument-sessions/{quote(session_id, safe='')}/"
            f"instruments/{quote(instrument_id, safe='')}/{suffix}"
        )

    @staticmethod
    def _procedure_path(procedure_run_id: str, suffix: str) -> str:
        return f"{_API_PREFIX}/procedures/{quote(procedure_run_id, safe='')}/{suffix}"

    @staticmethod
    def _procedure_step_path(
        command: ProcedureStepCompleteCommand
        | ProcedureStepFailCommand
        | ProcedureStepAttentionCommand
        | ProcedureStepAttentionRetryCommand
        | ProcedureStepInputWaitCommand
        | ProcedureStepResourceWaitCommand
        | ProcedureStepInputSubmitCommand,
        suffix: str,
    ) -> str:
        return (
            f"{_API_PREFIX}/procedures/"
            f"{quote(command.procedure_run_id, safe='')}/steps/"
            f"{quote(command.step_key, safe='')}/attempts/{command.attempt}/{suffix}"
        )

    def _post_model[ModelT: BaseModel](
        self,
        path: str,
        body: BaseModel,
        model: type[ModelT],
    ) -> ModelT:
        response = self._post_response(path, body)
        return model.model_validate_json(response.content)

    def _post_response(
        self,
        path: str,
        body: BaseModel,
    ) -> httpx2.Response:
        return self._request(
            "POST",
            path,
            json=body.model_dump(mode="json"),
        )

    def _post_idempotent_model[ModelT: BaseModel](
        self,
        path: str,
        body: BaseModel,
        model: type[ModelT],
    ) -> ModelT:
        """Retry one transport failure with the exact operation command."""

        response = self._post_idempotent_response(path, body)
        return model.model_validate_json(response.content)

    def _post_idempotent_response(
        self,
        path: str,
        body: BaseModel,
    ) -> httpx2.Response:
        """Return one idempotent response after retrying a transport failure."""

        try:
            return self._post_response(path, body)
        except httpx2.TransportError:
            return self._post_response(path, body)

    def _put_payload_content(
        self,
        path: str,
        content: _PayloadUploadContent,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx2.Response:
        """Retry one transport failure against an immutable content PUT."""

        request_headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(_payload_upload_size(content)),
            **(headers or {}),
        }
        request_content = (
            content if isinstance(content, bytes) else _payload_upload_chunks(content)
        )
        try:
            return self._request(
                "PUT",
                path,
                content=request_content,
                headers=request_headers,
            )
        except httpx2.TransportError:
            request_content = (
                content
                if isinstance(content, bytes)
                else _payload_upload_chunks(content)
            )
            return self._request(
                "PUT",
                path,
                content=request_content,
                headers=request_headers,
            )

    def _post_empty_idempotent_model[ModelT: BaseModel](
        self,
        path: str,
        model: type[ModelT],
    ) -> ModelT:
        """Retry one transport failure against an idempotent empty command."""

        try:
            response = self._request("POST", path)
        except httpx2.TransportError:
            response = self._request("POST", path)
        return model.model_validate_json(response.content)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: object | None = None,
        content: bytes | Iterable[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx2.Response:
        if self.is_closed:
            raise SessionClosedError(
                "This Scopecat session is closed. Open a new connection with "
                "sc.open_project(project_root).connect(), then use "
                "lab.get_run(run_id) to read the retained run. Capture "
                "run.snapshot before closing to retain its state without HTTP."
            )
        response = self._http.request(
            method,
            path,
            params=params,
            json=json,
            content=content,
            headers=headers,
        )
        if response.status_code == 404:
            raise DaemonNotFoundError(_error_detail(response), response=response)
        if response.status_code == 409:
            raise DaemonConflictError(_error_detail(response), response=response)
        if response.status_code == 503:
            raise DaemonUnavailableError(_error_detail(response), response=response)
        response.raise_for_status()
        return response


class _ErrorResponse(BaseModel):
    detail: str


def _payload_upload_size(content: _PayloadUploadContent) -> int:
    if isinstance(content, bytes):
        return len(content)
    return sum(memoryview(segment).nbytes for segment in content)


def _payload_upload_chunks(segments: tuple[Buffer, ...]) -> Iterator[bytes]:
    pending = bytearray()
    for segment in segments:
        view = memoryview(segment).cast("B")
        offset = 0
        while offset < view.nbytes:
            take = min(
                _PAYLOAD_UPLOAD_CHUNK_BYTES - len(pending),
                view.nbytes - offset,
            )
            pending.extend(view[offset : offset + take])
            offset += take
            if len(pending) == _PAYLOAD_UPLOAD_CHUNK_BYTES:
                yield bytes(pending)
                pending.clear()
    if pending:
        yield bytes(pending)


def _error_detail(response: httpx2.Response) -> str:
    try:
        return _ErrorResponse.model_validate_json(response.content).detail
    except ValidationError:
        return response.text or response.reason_phrase


__all__ = [
    "DaemonClient",
    "DaemonClientError",
    "DaemonConflictError",
    "DaemonHealth",
    "DaemonNotFoundError",
    "DaemonUnavailableError",
]
