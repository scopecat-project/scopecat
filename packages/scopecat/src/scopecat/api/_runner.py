"""Local planning and execution for daemon-admitted experiment invocations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from time import monotonic
from typing import Literal, cast, override
from uuid import uuid4

import httpx2

from scopecat.adaptive_coordination import derive_adaptive_region_layout
from scopecat.api.review import ExperimentReviewHandle, create_experiment_review
from scopecat.authoring import MetadataValue
from scopecat.authoring.experiments import ExperimentInvocation
from scopecat.control.models import (
    AdaptiveRegionSpec,
    PointCoordinateValue,
    RunDomainTargetRequirement,
    RunPlanSummary,
    RunResourceRequirement,
)
from scopecat.daemon.client import (
    DaemonClient,
    DaemonConflictError,
    DaemonUnavailableError,
)
from scopecat.daemon.execution import (
    ExecutorLeaseLostError,
    LeaseSupervisor,
    daemon_execution_session,
    daemon_resumption_session,
)
from scopecat.daemon.views import RunDetail
from scopecat.daemon.wire import (
    AttentionResolutionCommand,
    ExecutorLease,
    RunSubmission,
)
from scopecat.execution.interpreter import execute_admitted_run
from scopecat.inspection import CompiledProgramInspectionQuery
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.kernel.errors import RunCancelled, RunFailed, RunIndeterminate
from scopecat.planning.point_selection import point_coordinate_contract
from scopecat.planning.preview import PreviewCoordinateMode, build_run_program_preview
from scopecat.planning.preview_models import ExperimentPreview
from scopecat.planning.provider_validation import instrument_contract_fingerprint
from scopecat.planning.service import PlannedRun, plan_experiment_invocation
from scopecat.planning.system import (
    ExperimentSystemBuilder,
    build_experiment_system,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.content import Sha256ContentHash
from scopecat.records.plan_ref import ExperimentPlanRef, ProcedureChildSubmission
from scopecat.records.run import (
    ConfigRegistryRunConfigSource,
    RunConfigSource,
    RunSnapshot,
)
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleSelector


class RunResourcesBlocked(Exception):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"resources are busy for {run_id}")


@dataclass(frozen=True, slots=True)
class _DaemonRunner:
    client: DaemonClient
    build_experiment_system: ExperimentSystemBuilder | None

    def execute(
        self,
        planned: PlannedRun,
        *,
        executor_id: str = "notebook",
        submission_id: str | None = None,
        wait_for_resources: bool = False,
        procedure_child: ProcedureChildSubmission | None = None,
    ) -> RunSnapshot:
        """Admit a plan remotely while executing its Python closures locally."""

        submission, _ = _prepare_run_submission(
            planned,
            submission_id=submission_id or uuid4().hex,
        )
        if procedure_child is not None:
            submission = submission.model_copy(
                update={"procedure_child": procedure_child}
            )
        admission = self.client.submit_run(submission)
        if admission.snapshot.outcome is not None:
            return _resolve_terminal_snapshot(admission.snapshot)
        heartbeat = _LeaseHeartbeat(
            lambda run_id: self.client.run_cancellation(run_id).requested
        )
        session = daemon_execution_session(
            self.client,
            submission,
            admission,
            executor_id=executor_id,
            lease_supervisor=heartbeat,
            on_resource_busy="keep_queued" if wait_for_resources else "fail",
        )
        try:
            return execute_admitted_run(
                program=planned.program,
                session=session,
            )
        except DaemonConflictError:
            if wait_for_resources:
                detail = self.client.get_run(admission.run_id)
                if (
                    detail.control.state == "queued"
                    and not self.client.get_run_execution_segments(
                        admission.run_id, limit=1
                    ).items
                ):
                    raise RunResourcesBlocked(admission.run_id) from None
            raise
        finally:
            heartbeat.close()

    def run(
        self,
        experiment: ExperimentInvocation,
        *,
        config: ConfigProfileSnapshot | None = None,
        config_source: RunConfigSource | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        executor_id: str = "notebook",
        submission_id: str | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> RunSnapshot:
        planned = self._plan(
            experiment,
            config=config,
            config_source=config_source,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=samples,
        )
        return self.execute(
            planned,
            executor_id=executor_id,
            submission_id=submission_id,
        )

    def resume(
        self,
        experiment: ExperimentInvocation,
        *,
        run_id: str,
        executor_id: str = "notebook",
    ) -> RunSnapshot:
        """Rebuild local code and continue one compatible existing run."""

        detail = self.client.get_run(run_id)
        if detail.snapshot.outcome is not None:
            return _resolve_terminal_snapshot(detail.snapshot)
        if detail.control.state not in ("queued", "attention_required"):
            raise ValueError(
                f"run {run_id!r} cannot resume from state {detail.control.state!r}"
            )
        config = self.client.run_config(run_id).config
        request = self.client.run_request(run_id).request
        planned = self._plan(
            experiment,
            config=config,
            config_source=detail.snapshot.config_source,
            name=request.display_name,
            tags=request.tags,
            description=request.description,
            metadata=cast("Mapping[str, MetadataValue]", request.metadata),
            operator=request.operator,
            samples=request.samples,
        )
        _validate_resumed_plan(planned, detail=detail, request=request)
        preview = self.client.measurement_preview(run_id, limit=1)
        if (
            preview.dataset_schema is not None
            and preview.dataset_schema != planned.program.measurements.schema
        ):
            raise ValueError(
                "resumed measurement schema does not match the durable dataset"
            )
        segments = self.client.get_run_execution_segments(run_id, limit=1)
        has_prior_execution_segment = bool(segments.items)
        if has_prior_execution_segment:
            _validate_resumable_program(planned)
        if detail.control.state == "attention_required":
            receipt = self.client.resolve_attention(
                run_id,
                AttentionResolutionCommand.continue_run(
                    run_contract_fingerprint=(
                        detail.control.admission.run_contract_fingerprint
                    )
                ),
            )
            if receipt.run_id != run_id or receipt.state != "queued":
                raise ValueError("attention resolution did not queue the resumed run")

        heartbeat = _LeaseHeartbeat(
            lambda run_id: self.client.run_cancellation(run_id).requested
        )
        session = daemon_resumption_session(
            self.client,
            detail.snapshot,
            executor_id=executor_id,
            has_prior_execution_segment=has_prior_execution_segment,
            lease_supervisor=heartbeat,
        )
        try:
            return execute_admitted_run(
                program=planned.program,
                session=session,
            )
        finally:
            heartbeat.close()

    def preview(
        self,
        experiment: ExperimentInvocation,
        *,
        config: ConfigProfileSnapshot | None = None,
        config_source: RunConfigSource | None = None,
        point: int | Literal["first", "middle", "last"] = "first",
        coordinates: Mapping[str, object] | None = None,
        coordinate_mode: PreviewCoordinateMode = "exact",
        inspection_query: CompiledProgramInspectionQuery | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentPreview:
        planned = self._plan(
            experiment,
            config=config,
            config_source=config_source,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=samples,
        )
        return build_run_program_preview(
            planned.program,
            invocation=experiment,
            point=point,
            coordinates=coordinates,
            coordinate_mode=coordinate_mode,
            inspection_query=inspection_query,
        )

    def review(
        self,
        experiment: ExperimentInvocation,
        *,
        config: ConfigProfileSnapshot | None = None,
        config_source: RunConfigSource | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentReviewHandle:
        planned = self._plan(
            experiment,
            config=config,
            config_source=config_source,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=samples,
        )
        return create_experiment_review(
            client=self.client,
            program=planned.program,
            invocation=experiment,
            session_id=uuid4().hex,
            worker_id=uuid4().hex,
            title=name or planned.program.experiment_id,
        )

    def _plan(
        self,
        experiment: ExperimentInvocation,
        *,
        config: ConfigProfileSnapshot | None,
        config_source: RunConfigSource | None,
        name: str | None,
        tags: tuple[str, ...],
        description: str | None,
        metadata: Mapping[str, MetadataValue] | None,
        operator: str | None,
        samples: tuple[SampleSelector, ...] = (),
        plan_ref: ExperimentPlanRef | None = None,
    ) -> PlannedRun:
        if isinstance(config_source, ContextRunConfigSource):
            binding = config_source.sample
            exact = SampleSelector(
                role=binding.role,
                sample_id=binding.sample_id,
                revision=binding.revision,
                context_id=binding.context_id,
            )
            for selector in samples:
                if selector.role == binding.role and (
                    selector.sample_id != binding.sample_id
                    or selector.revision not in (None, binding.revision)
                    or selector.context_id not in (None, binding.context_id)
                ):
                    raise ValueError(
                        "explicit sample does not match the selected parameter context"
                    )
            samples = (
                *(selector for selector in samples if selector.role != binding.role),
                exact,
            )
        selected_source = config_source
        if config is None:
            active = self.client.active_config()
            selected_config = active.config
            selected_source = selected_source or ConfigRegistryRunConfigSource(
                selector="active",
                entry_id=active.entry.id,
                config_ref=active.entry.config_ref,
                content_hash=active.entry.content_hash,
                registry_generation=active.activation.generation,
            )
        else:
            selected_config = config
        instrument_catalog = self.client.resolve_instrument_contracts(
            selected_config,
        )
        selected_system = build_experiment_system(
            self.build_experiment_system,
            selected_config,
            instrument_catalog,
        )
        return plan_experiment_invocation(
            experiment,
            config=selected_config,
            system=selected_system,
            config_source=selected_source,
            display_name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=samples,
            plan_ref=plan_ref,
        )


class _LeaseHeartbeat(LeaseSupervisor):
    def __init__(self, cancellation: Callable[[str], bool] | None = None) -> None:
        self._stop = Event()
        self._lock = Lock()
        self._failure: tuple[ExecutorLease, Exception] | None = None
        self._cancellation_requested = Event()
        self._thread: Thread | None = None
        self._cancellation = cancellation
        self._cancellation_thread: Thread | None = None

    @override
    def start(
        self,
        lease: ExecutorLease,
        heartbeat: Callable[[], ExecutorLease],
    ) -> None:
        if lease.cancellation_requested_at is not None:
            self._cancellation_requested.set()
        self._thread = Thread(
            target=self._run,
            args=(lease, heartbeat),
            name=f"scopecat-lease-{lease.run_id}",
            daemon=True,
        )
        self._thread.start()
        if self._cancellation is not None:
            self._cancellation_thread = Thread(
                target=self._watch_cancellation,
                args=(lease, self._cancellation),
                name=f"scopecat-cancellation-{lease.run_id}",
                daemon=True,
            )
            self._cancellation_thread.start()

    @override
    def require_live(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            lease, cause = failure
            raise ExecutorLeaseLostError(lease, cause) from cause

    @override
    def cancellation_requested(self) -> bool:
        return self._cancellation_requested.is_set()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._cancellation_thread is not None:
            self._cancellation_thread.join()

    def _watch_cancellation(
        self, lease: ExecutorLease, cancellation: Callable[[str], bool]
    ) -> None:
        # Read-only control traffic must not renew authority or wait for renewal.
        while not self._stop.wait(0.25):
            try:
                requested = cancellation(lease.run_id)
            except DaemonUnavailableError, httpx2.TransportError:
                continue
            except Exception as error:
                with self._lock:
                    self._failure = (lease, error)
                return
            if requested:
                self._cancellation_requested.set()
                return

    def _run(
        self,
        lease: ExecutorLease,
        heartbeat: Callable[[], ExecutorLease],
    ) -> None:
        current = lease
        deadline, delay = _executor_lease_timing(current)
        while not self._stop.wait(delay):
            try:
                current = heartbeat()
                if current.cancellation_requested_at is not None:
                    self._cancellation_requested.set()
            except Exception as error:
                if isinstance(error, (DaemonUnavailableError, httpx2.TransportError)):
                    remaining = deadline - monotonic()
                    if remaining > 0:
                        delay = min(
                            0.25, current.heartbeat_interval_seconds / 2, remaining
                        )
                        continue
                with self._lock:
                    self._failure = (current, error)
                return
            deadline, delay = _executor_lease_timing(current)


def _executor_lease_timing(lease: ExecutorLease) -> tuple[float, float]:
    now = datetime.now(UTC)
    interval = lease.heartbeat_interval_seconds
    remaining = max((lease.expires_at - now).total_seconds(), 0.0)
    renewal_at = lease.expires_at - timedelta(seconds=interval * 2)
    delay = max((renewal_at - now).total_seconds(), 0.0)
    return monotonic() + remaining, delay


def _run_plan_summary(planned: PlannedRun) -> RunPlanSummary:
    program = planned.program
    adaptive = program.adaptive_domain_plan
    region_layout = (
        None
        if adaptive is None
        else derive_adaptive_region_layout(adaptive, program.points)
    )
    adaptive_regions = () if region_layout is None else region_layout.regions
    sampled_adaptive_regions = adaptive_regions[:256]
    coordinates, sampled_points, sampled_points_truncated = point_coordinate_contract(
        program.points
    )
    host = program.host
    descriptions = (
        ()
        if host is None
        else tuple(
            host.advertised_descriptions[instrument_id]
            for instrument_id in host.resource_order
        )
    )
    return RunPlanSummary(
        experiment_id=program.experiment_id,
        experiment_kind=program.points.experiment_kind,
        point_plan_fingerprint=_point_plan_fingerprint(planned),
        measurement_contract_fingerprint=_measurement_contract_fingerprint(planned),
        point_count=program.points.contract.point_count,
        initial_point_count=len(program.points.points),
        point_limit=program.points.contract.point_limit,
        adaptive_coordinate_ids=(
            ()
            if program.adaptive_domain_plan is None
            else program.adaptive_domain_plan.adaptive_coordinate_ids
            or program.points.coordinate_ids
        ),
        adaptive_scope=(
            None
            if program.adaptive_domain_plan is None
            else program.adaptive_domain_plan.scope
        ),
        per_region_point_limit=(
            None if adaptive is None else adaptive.per_region_point_limit
        ),
        adaptive_region_count=len(adaptive_regions),
        adaptive_regions=tuple(
            AdaptiveRegionSpec(
                id=region.id,
                coordinates=cast(
                    "dict[str, PointCoordinateValue]",
                    dict(region.coordinates),
                ),
                initial_point_count=region.point_count,
            )
            for region in sampled_adaptive_regions
        ),
        adaptive_regions_truncated=(
            len(sampled_adaptive_regions) < len(adaptive_regions)
        ),
        coordinates=coordinates,
        sampled_points=sampled_points,
        sampled_points_truncated=sampled_points_truncated,
        record_ids=tuple(record.id for record in program.measurements.records),
        host_instrument_order=program.resource_order,
        host_provider_id=None if host is None else host.provider_id,
        host_contract_fingerprint=(
            None
            if host is None
            else instrument_contract_fingerprint(host.provider_id, descriptions)
        ),
        domain_target_requirement=(
            None
            if program.domain_target_requirement is None
            else RunDomainTargetRequirement(
                id=program.domain_target_requirement.id,
                kind=program.domain_target_requirement.kind,
                instrument_ids=program.domain_target_requirement.instrument_ids,
            )
        ),
        run_resource_requirements=tuple(
            RunResourceRequirement(
                id=requirement.id,
                kind=requirement.kind,
            )
            for requirement in program.resource_requirements
        ),
    )


def _point_plan_fingerprint(planned: PlannedRun) -> str:
    """Identify every accepted point without expanding the durable plan view."""

    return stable_content_hash(
        content_fingerprint(
            {
                "schema": "scopecat.run_point_plan.v1",
                "points": tuple(
                    {
                        "logical_id": point.logical_id.value,
                        "coordinates": dict(point.coordinates),
                    }
                    for point in planned.program.points.points
                ),
            }
        )
    )


def _measurement_contract_fingerprint(planned: PlannedRun) -> str:
    """Identify the complete measurement projection and durable dataset schema."""

    return planned.program.measurements.recording_contract_fingerprint


def _prepare_run_submission(
    planned: PlannedRun,
    *,
    submission_id: str,
) -> tuple[RunSubmission, Sha256ContentHash]:
    """Build one exact admission command and its prefixed intent content hash."""

    submission = RunSubmission(
        submission_id=submission_id,
        config=planned.config,
        config_source=planned.config_source,
        request=planned.request,
        plan=_run_plan_summary(planned),
    )
    return submission, f"sha256:{submission.intent_content_hash}"


def _validate_resumed_plan(
    planned: PlannedRun,
    *,
    detail: RunDetail,
    request: RunRequest,
) -> None:
    if planned.request != request:
        raise ValueError("resumed invocation does not reproduce the accepted request")
    submission, _ = _prepare_run_submission(
        planned,
        submission_id="resume-contract-validation",
    )
    if (
        submission.intent_content_hash
        != detail.control.admission.run_contract_fingerprint
    ):
        raise ValueError("resumed program does not match the accepted run contract")


def _validate_resumable_program(planned: PlannedRun) -> None:
    if planned.program.adaptive_domain_plan is not None:
        raise ValueError("adaptive runs do not yet support interpreter continuation")
    if planned.program.domain_target_requirement is not None:
        raise ValueError("domain-target runs do not yet support suffix continuation")


def _resolve_terminal_snapshot(snapshot: RunSnapshot) -> RunSnapshot:
    outcome = snapshot.outcome
    if outcome is None:
        raise AssertionError("terminal admission returned a non-terminal snapshot")
    if outcome.result == "succeeded":
        return snapshot
    if outcome.certainty == "indeterminate":
        raise RunIndeterminate(run_id=snapshot.run_id, outcome=outcome)
    if outcome.result == "cancelled":
        raise RunCancelled(run_id=snapshot.run_id, outcome=outcome)
    raise RunFailed(run_id=snapshot.run_id, outcome=outcome)


__all__ = ["_DaemonRunner", "_prepare_run_submission"]
