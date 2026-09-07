"""Execution ports for a client-owned program admitted by the daemon."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Literal, Protocol, cast

from pydantic import BaseModel, JsonValue, TypeAdapter

from scopecat.adaptive_domains import DomainProposalAttempt
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.points import (
    AcceptedRunPointView,
    RunDomainDecisionCommand,
    RunDomainProposalAttemptView,
    RunPointCoordinateValue,
    RunPointPlanCloseCommand,
)
from scopecat.daemon.wire import (
    ExecutorHeartbeat,
    ExecutorLease,
    ExecutorStartRequest,
    MeasurementFlushCommand,
    MeasurementHeaderCommand,
    MeasurementSealCommand,
    RunAdmission,
    RunCoverageAdvanceCommand,
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionItem,
    RunHardwareBatchCommand,
    RunHardwareFinishCommand,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
    RunRecoveryGroupCommitCommand,
    RunSubmission,
    TerminalModelWrite,
    TerminalRunCommitCommand,
)
from scopecat.execution.services import ExecutionSession, QueuedOperatorDomainRequest
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.problems import Problem
from scopecat.optimization import DomainProposalDecision
from scopecat.records.config import config_content_hash
from scopecat.records.execution import (
    DomainExecutionId,
    DomainExecutionReceipt,
    DomainJobCheckpoint,
    DomainJobCheckpointTransition,
    DomainJobInvocationTransition,
    DomainJobTerminalTransition,
    DomainJobTransitionRecord,
    RecoveryGroupCompletion,
)
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementPartitionedArray,
    MeasurementRecord,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetBatch,
    MeasurementDatasetHeader,
    MeasurementDatasetReceipt,
    MeasurementDatasetSeal,
)
from scopecat.records.run import RunSnapshot
from scopecat.runs.repository import TerminalRunCommit
from scopecat.sdk.domain.invocation import DomainInvocationIntent
from scopecat.sdk.instruments.execution import (
    RunHardwareBatch,
    RunHardwareBatchReceipt,
    RunHardwareFinalizationReceipt,
)

_JSON_DOCUMENT = TypeAdapter(dict[str, JsonValue])
_PROVISION_OPERATION_ID = "lifecycle.provide-instruments"
_MEASUREMENT_TRANSPORT_RECORD_LIMIT = 64
_MEASUREMENT_TRANSPORT_VALUE_BYTE_LIMIT = 8 * 1024 * 1024
_MEASUREMENT_TRANSPORT_LATENCY_SECONDS = 0.1
_COVERAGE_TRANSPORT_POINT_LIMIT = 256
_COVERAGE_TRANSPORT_LATENCY_SECONDS = 0.1
_DOMAIN_TRANSITION_TRANSPORT_ITEM_LIMIT = 64
_RECOVERY_GROUP_TRANSPORT_ITEM_LIMIT = 256


class ExecutorLeaseLostError(RuntimeError):
    """An executor can no longer commit effects to its run."""

    def __init__(self, lease: ExecutorLease, cause: Exception) -> None:
        super().__init__(
            f"executor lease {lease.lease_id!r} for run "
            f"{lease.run_id!r} is no longer live: {cause}"
        )
        self.lease = lease
        self.cause = cause


def daemon_execution_session(
    client: DaemonClient,
    submission: RunSubmission,
    admission: RunAdmission,
    *,
    executor_id: str,
    lease_supervisor: LeaseSupervisor | None = None,
    on_resource_busy: Literal["keep_queued", "fail"] = "fail",
) -> ExecutionSession:
    """Bind client-owned code to the admitted daemon-owned run."""

    if admission.snapshot.outcome is not None:
        raise ValueError("terminal run cannot start execution")
    if admission.submission_id != submission.submission_id:
        raise ValueError("submission and admission ids do not match")
    if admission.snapshot.config_content_hash != config_content_hash(submission.config):
        raise ValueError("submission and admission config snapshots do not match")
    if admission.snapshot.config_source != submission.config_source:
        raise ValueError("submission and admission config sources do not match")

    return _daemon_execution_session(
        client,
        admission.snapshot,
        executor_id=executor_id,
        lease_supervisor=lease_supervisor,
        on_resource_busy=on_resource_busy,
        has_prior_execution_segment=lambda: bool(
            client.get_run_execution_segments(
                admission.run_id,
                limit=1,
            ).items
        ),
    )


def daemon_resumption_session(
    client: DaemonClient,
    snapshot: RunSnapshot,
    *,
    executor_id: str,
    has_prior_execution_segment: bool,
    lease_supervisor: LeaseSupervisor | None = None,
) -> ExecutionSession:
    """Bind a validated local program to an existing non-terminal run."""

    if snapshot.outcome is not None:
        raise ValueError("terminal run cannot resume execution")
    return _daemon_execution_session(
        client,
        snapshot,
        executor_id=executor_id,
        lease_supervisor=lease_supervisor,
        on_resource_busy="keep_queued",
        has_prior_execution_segment=lambda: has_prior_execution_segment,
    )


def _daemon_execution_session(
    client: DaemonClient,
    snapshot: RunSnapshot,
    *,
    executor_id: str,
    lease_supervisor: LeaseSupervisor | None,
    on_resource_busy: Literal["keep_queued", "fail"],
    has_prior_execution_segment: Callable[[], bool],
) -> ExecutionSession:
    """Construct transport-backed execution ports after admission validation."""

    authority = _LeaseAuthority(
        client=client,
        run_id=snapshot.run_id,
        executor_id=executor_id,
        lease_supervisor=lease_supervisor,
        on_resource_busy=on_resource_busy,
    )
    instruments = _DaemonRunInstrumentHost(authority)
    coverage = _DaemonRunCoverage(authority)
    recovery_groups = _DaemonRunRecoveryGroups(authority)
    domain_job_transitions = _DaemonRunDomainJobTransitions(authority)
    domain_proposals = _DaemonRunDomainProposals(authority)

    def begin() -> None:
        authority.start()
        if not authority.cancellation_requested():
            instruments.provision()

    return ExecutionSession(
        accepted=snapshot,
        begin=begin,
        commit_terminal=authority.commit_terminal,
        measurements=_DaemonMeasurementRepository(authority),
        instruments=instruments,
        domain_job_transitions=domain_job_transitions,
        coverage=coverage,
        recovery_groups=recovery_groups,
        domain_proposals=domain_proposals,
        cancellation_requested=authority.cancellation_requested,
        effects_ready=lambda: instruments.provisioned,
        durable_completed_point_count=lambda: (
            client.get_run_coverage(authority.run_id).completed_point_count
        ),
        durable_recovery_groups=lambda: _read_recovery_groups(
            client,
            authority.run_id,
        ),
        has_prior_execution_segment=has_prior_execution_segment,
    )


def _read_recovery_groups(
    client: DaemonClient,
    run_id: str,
) -> tuple[RecoveryGroupCompletion, ...]:
    completed: list[RecoveryGroupCompletion] = []
    before: int | None = None
    while True:
        page = client.get_run_recovery_groups(
            run_id,
            limit=100,
            before=before,
        )
        if page.run_id != run_id:
            raise ValueError("recovery group page does not match its request")
        completed[0:0] = [item.completion for item in page.items]
        if page.next_cursor is None:
            return tuple(completed)
        before = page.next_cursor


class LeaseSupervisor(Protocol):
    """Observe lease start and reject work after background renewal fails."""

    def start(
        self,
        lease: ExecutorLease,
        heartbeat: Callable[[], ExecutorLease],
    ) -> None: ...

    def require_live(self) -> None: ...

    def cancellation_requested(self) -> bool: ...


class _LeaseAuthority:
    def __init__(
        self,
        *,
        client: DaemonClient,
        run_id: str,
        executor_id: str,
        lease_supervisor: LeaseSupervisor | None,
        on_resource_busy: Literal["keep_queued", "fail"] = "keep_queued",
    ) -> None:
        self.client = client
        self.run_id = run_id
        self.executor_id = executor_id
        self._lease: ExecutorLease | None = None
        self._lease_supervisor = lease_supervisor
        self._on_resource_busy: Literal["keep_queued", "fail"] = on_resource_busy
        self._lock = Lock()

    def start(self) -> None:
        with self._lock:
            if self._lease is not None:
                return
        lease = self.client.start_executor(
            self.run_id,
            ExecutorStartRequest(
                executor_id=self.executor_id,
                on_resource_busy=self._on_resource_busy,
            ),
        )
        with self._lock:
            self._lease = lease
        if self._lease_supervisor is not None:
            self._lease_supervisor.start(lease, self.heartbeat)

    def fence(self) -> str:
        if self._lease_supervisor is not None:
            self._lease_supervisor.require_live()
        with self._lock:
            lease = self._lease
        if lease is None:
            raise RuntimeError("executor has not started")
        return lease.lease_id

    def heartbeat(self) -> ExecutorLease:
        lease_id = self.fence()
        lease = self.client.heartbeat_executor(
            self.run_id,
            ExecutorHeartbeat(
                lease_id=lease_id,
            ),
        )
        with self._lock:
            if self._lease is None:
                raise RuntimeError("executor has not started")
            self._lease = lease
        return lease

    def cancellation_requested(self) -> bool:
        if self._lease_supervisor is not None:
            return self._lease_supervisor.cancellation_requested()
        with self._lock:
            lease = self._lease
        return lease is not None and lease.cancellation_requested_at is not None

    def commit_terminal(self, commit: TerminalRunCommit) -> RunSnapshot:
        lease_id = self.fence()
        return self.client.commit_terminal(
            self.run_id,
            TerminalRunCommitCommand(
                lease_id=lease_id,
                outcome=commit.outcome,
                contents=commit.contents,
                models=tuple(
                    TerminalModelWrite(
                        ref=write.ref,
                        value=_json_document(write.value),
                    )
                    for write in commit.models
                ),
            ),
        )


class _DaemonRunCoverage:
    """Coalesce no-dataset point progress before durable daemon writes."""

    def __init__(self, authority: _LeaseAuthority) -> None:
        self._authority = authority
        self._pending_start: int | None = None
        self._pending_count = 0
        self._last_send_at: float | None = None

    def advance(self, *, start_index: int, point_count: int) -> None:
        if point_count < 1:
            raise ValueError("coverage advance must be non-empty")
        if self._pending_start is None:
            self._pending_start = start_index
        elif start_index != self._pending_start + self._pending_count:
            raise ValueError("coverage advances must be contiguous")
        self._pending_count += point_count
        now = monotonic()
        if (
            self._last_send_at is None
            or self._pending_count >= _COVERAGE_TRANSPORT_POINT_LIMIT
            or now - self._last_send_at >= _COVERAGE_TRANSPORT_LATENCY_SECONDS
        ):
            self._send_pending(now=now)

    def flush(self) -> None:
        self._send_pending()

    def _send_pending(self, *, now: float | None = None) -> None:
        start_index = self._pending_start
        point_count = self._pending_count
        if start_index is None:
            return
        state = self._authority.client.advance_run_coverage(
            self._authority.run_id,
            RunCoverageAdvanceCommand(
                lease_id=self._authority.fence(),
                start_index=start_index,
                point_count=point_count,
            ),
        )
        expected_count = start_index + point_count
        if state.run_id != self._authority.run_id:
            raise ValueError("run coverage receipt does not match its request")
        if state.completed_point_count != expected_count:
            raise ValueError("run coverage receipt did not commit the requested prefix")
        self._pending_start = None
        self._pending_count = 0
        self._last_send_at = monotonic() if now is None else now


class _DaemonRunRecoveryGroups:
    """Commit output-backed recovery proofs through the active lease fence."""

    def __init__(self, authority: _LeaseAuthority) -> None:
        self._authority = authority

    def commit(self, groups: tuple[RecoveryGroupCompletion, ...]) -> None:
        for start in range(0, len(groups), _RECOVERY_GROUP_TRANSPORT_ITEM_LIMIT):
            selected = groups[start : start + _RECOVERY_GROUP_TRANSPORT_ITEM_LIMIT]
            receipt = self._authority.client.commit_run_recovery_groups(
                self._authority.run_id,
                RunRecoveryGroupCommitCommand(
                    lease_id=self._authority.fence(),
                    groups=selected,
                ),
            )
            if (
                receipt.run_id != self._authority.run_id
                or tuple(item.completion for item in receipt.items) != selected
            ):
                raise ValueError("recovery group receipt does not match its request")


class _DaemonRunDomainJobTransitions:
    """Coalesce eligible target transitions into bounded durable appends."""

    def __init__(self, authority: _LeaseAuthority) -> None:
        self._authority = authority
        self._pending: list[RunDomainJobTransitionItem] = []

    def invocation(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        execution_id: DomainExecutionId,
        intent: DomainInvocationIntent,
        write_ahead: bool,
    ) -> None:
        self._stage(
            logical_compute_node_id=logical_compute_node_id,
            point_ordinals=point_ordinals,
            transition=DomainJobInvocationTransition(
                execution_id=execution_id,
                intent=intent,
            ),
            write_ahead=write_ahead,
        )

    def checkpoint(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        checkpoint: DomainJobCheckpoint,
    ) -> None:
        self._stage(
            logical_compute_node_id=logical_compute_node_id,
            point_ordinals=point_ordinals,
            transition=DomainJobCheckpointTransition(checkpoint=checkpoint),
            write_ahead=True,
        )

    def terminal(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        receipt: DomainExecutionReceipt,
        write_ahead: bool,
    ) -> None:
        self._stage(
            logical_compute_node_id=logical_compute_node_id,
            point_ordinals=point_ordinals,
            transition=DomainJobTerminalTransition(receipt=receipt),
            write_ahead=write_ahead,
        )

    def flush(self) -> None:
        self._send_pending()

    def _stage(
        self,
        *,
        logical_compute_node_id: str,
        point_ordinals: tuple[int, ...],
        transition: DomainJobTransitionRecord,
        write_ahead: bool,
    ) -> None:
        self._pending.append(
            RunDomainJobTransitionItem(
                logical_compute_node_id=logical_compute_node_id,
                point_ordinals=point_ordinals,
                transition=transition,
            )
        )
        if write_ahead or len(self._pending) >= _DOMAIN_TRANSITION_TRANSPORT_ITEM_LIMIT:
            self._send_pending()

    def _send_pending(self) -> None:
        items = tuple(self._pending)
        if not items:
            return
        receipt = self._authority.client.commit_run_domain_job_transitions(
            self._authority.run_id,
            RunDomainJobTransitionBatchCommand(
                lease_id=self._authority.fence(),
                items=items,
            ),
        )
        if (
            receipt.run_id != self._authority.run_id
            or len(receipt.items) != len(items)
            or any(
                committed.run_id != self._authority.run_id
                or committed.logical_compute_node_id
                != requested.logical_compute_node_id
                or committed.point_ordinals != requested.point_ordinals
                or committed.transition != requested.transition
                for committed, requested in zip(receipt.items, items, strict=True)
            )
        ):
            raise ValueError(
                "domain job transition batch receipt does not match its request"
            )
        self._pending.clear()


class _DaemonRunDomainProposals:
    """Persist durable domain decisions through the fenced executor API."""

    def __init__(self, authority: _LeaseAuthority) -> None:
        self._authority = authority

    def next_queued(self) -> QueuedOperatorDomainRequest | None:
        pending = self._authority.client.get_next_queued_run_domain(
            self._authority.run_id
        )
        if pending is None:
            return None
        return QueuedOperatorDomainRequest(request=pending.request.request())

    def append(
        self,
        proposal: DomainProposalAttempt,
        decision: DomainProposalDecision,
        accepted_points: tuple[AcceptedRunPoint, ...],
        *,
        operator_request_id: str | None = None,
    ) -> None:
        accepted_point_views = tuple(
            AcceptedRunPointView(
                point_index=point.ordinal,
                coordinates=cast(
                    "dict[str, RunPointCoordinateValue]",
                    dict(point.coordinates),
                ),
                proposal_fingerprint=cast("str", point.proposal_fingerprint),
                source=point.source,
                region_id=cast("str", point.region_id),
                domain_proposal_fingerprint=cast(
                    "str", point.domain_proposal_fingerprint
                ),
            )
            for point in accepted_points
        )
        durable = self._authority.client.append_run_domain_decision(
            self._authority.run_id,
            RunDomainDecisionCommand(
                lease_id=self._authority.fence(),
                operation_id=_domain_decision_operation_id(
                    decision,
                    operator_request_id=operator_request_id,
                ),
                operator_request_id=operator_request_id,
                proposal=RunDomainProposalAttemptView.from_proposal(proposal),
                outcome=decision.outcome,
                accepted_points=accepted_point_views,
                reason=decision.reason,
            ),
        )
        if durable.proposal_index != decision.proposal_index:
            raise ValueError("daemon assigned a different proposal index")
        if durable.outcome != decision.outcome:
            raise ValueError("daemon recorded a different proposal outcome")
        if durable.accepted_point_start != decision.accepted_point_start:
            raise ValueError("daemon recorded a different accepted point start")
        if durable.accepted_point_count != decision.accepted_point_count:
            raise ValueError("daemon recorded a different accepted point count")

    def close(self, *, completed_point_count: int, reason: str) -> None:
        self._authority.client.close_run_point_plan(
            self._authority.run_id,
            RunPointPlanCloseCommand(
                lease_id=self._authority.fence(),
                operation_id=_point_plan_close_operation_id(
                    completed_point_count=completed_point_count,
                    reason=reason,
                ),
                based_on_completed_point_count=completed_point_count,
                reason=reason,
            ),
        )


class _DaemonMeasurementRepository:
    def __init__(self, authority: _LeaseAuthority) -> None:
        self._authority = authority
        self._pending: list[MeasurementRecord] = []
        self._pending_value_bytes = 0
        self._last_send_at: float | None = None
        self._header_content_hash: str | None = None
        self._dataset_schema: MeasurementDatasetSchema | None = None
        self._next_acquisition_index: int | None = None

    def initialize(
        self,
        header: MeasurementDatasetHeader,
    ) -> MeasurementDatasetReceipt:
        lease_id = self._authority.fence()
        receipt = self._authority.client.initialize_measurements(
            self._authority.run_id,
            MeasurementHeaderCommand(
                lease_id=lease_id,
                header=header,
            ),
        )
        self._header_content_hash = header.content_hash
        self._dataset_schema = header.dataset_schema
        self._next_acquisition_index = receipt.acquisition_record_count
        return receipt

    def ingest(
        self,
        batch: MeasurementDatasetBatch,
    ) -> tuple[MeasurementDatasetReceipt, ...]:
        self._pending.extend(batch.records)
        self._pending_value_bytes += sum(
            _measurement_record_value_bytes(record) for record in batch.records
        )
        now = monotonic()
        if (
            self._last_send_at is None
            or len(self._pending) >= _MEASUREMENT_TRANSPORT_RECORD_LIMIT
            or self._pending_value_bytes >= _MEASUREMENT_TRANSPORT_VALUE_BYTE_LIMIT
            or now - self._last_send_at >= _MEASUREMENT_TRANSPORT_LATENCY_SECONDS
        ):
            return self._send_pending(now=now)
        return ()

    def _send_pending(
        self,
        *,
        now: float | None = None,
    ) -> tuple[MeasurementDatasetReceipt, ...]:
        if not self._pending:
            return ()
        records = tuple(self._pending)
        header_content_hash = self._header_content_hash
        if header_content_hash is None:
            raise RuntimeError("measurement transport requires an initialized header")
        dataset_schema = self._dataset_schema
        if dataset_schema is None:
            raise RuntimeError("measurement transport requires an initialized schema")
        acquisition_start = self._next_acquisition_index
        if acquisition_start is None:
            raise RuntimeError("measurement transport requires initialized ordering")
        append = MeasurementDatasetAppend(
            run_id=self._authority.run_id,
            header_content_hash=header_content_hash,
            acquisition_start=acquisition_start,
            records=records,
        )
        lease_id = self._authority.fence()
        receipt = self._authority.client.ingest_measurements(
            self._authority.run_id,
            lease_id=lease_id,
            append=append,
            dataset_schema=dataset_schema,
        )
        expected_count = acquisition_start + len(records)
        if receipt.received_record_count != expected_count:
            raise ValueError("measurement ingest did not extend acquisition order")
        self._next_acquisition_index = expected_count
        self._pending.clear()
        self._pending_value_bytes = 0
        self._last_send_at = monotonic() if now is None else now
        return receipt.durable_receipts

    def flush(self) -> tuple[MeasurementDatasetReceipt, ...]:
        receipts = list(self._send_pending())
        receipt = self._authority.client.flush_measurements(
            self._authority.run_id,
            MeasurementFlushCommand(lease_id=self._authority.fence()),
        )
        receipts.extend(receipt.durable_receipts)
        return tuple(receipts)

    def seal(self, seal: MeasurementDatasetSeal) -> MeasurementDatasetReceipt:
        lease_id = self._authority.fence()
        return self._authority.client.seal_measurements(
            self._authority.run_id,
            MeasurementSealCommand(
                lease_id=lease_id,
                seal=seal,
            ),
        )


class _DaemonRunInstrumentHost:
    """Typed transport proxy for drivers retained by the project daemon."""

    def __init__(self, authority: _LeaseAuthority) -> None:
        self._authority = authority
        self._provisioning: RunInstrumentProvisionReceipt | None = None
        self._next_batch_sequence = 0
        self._lock = Lock()

    @property
    def provisioned(self) -> bool:
        with self._lock:
            return self._provisioning is not None

    @property
    def ready(self) -> bool:
        return self._receipt().status == "ready"

    @property
    def setup_problems(self) -> tuple[Problem, ...]:
        return self._receipt().problems

    @property
    def observed_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        return self._receipt().observed_state

    @property
    def baseline_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        return self._receipt().baseline_state

    def provision(self) -> RunInstrumentProvisionReceipt:
        with self._lock:
            if self._provisioning is not None:
                return self._provisioning
        lease_id = self._authority.fence()
        receipt = self._authority.client.provision_run_instruments(
            self._authority.run_id,
            RunInstrumentProvisionCommand(
                lease_id=lease_id,
                operation_id=_PROVISION_OPERATION_ID,
            ),
        )
        if (
            receipt.run_id != self._authority.run_id
            or receipt.operation_id != _PROVISION_OPERATION_ID
        ):
            raise ValueError(
                "run instrument provisioning receipt does not match command"
            )
        with self._lock:
            if self._provisioning is None:
                self._provisioning = receipt
            return self._provisioning

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt:
        with self._lock:
            sequence = self._next_batch_sequence
            receipt = self._authority.client.execute_run_hardware(
                self._authority.run_id,
                RunHardwareBatchCommand(
                    lease_id=self._authority.fence(),
                    sequence=sequence,
                    batch=batch,
                ),
            )
            self._next_batch_sequence = sequence + 1
            return receipt

    def finish(
        self,
        *,
        operation_id: str,
        failed: bool,
    ) -> RunHardwareFinalizationReceipt:
        return self._authority.client.finish_run_hardware(
            self._authority.run_id,
            RunHardwareFinishCommand(
                lease_id=self._authority.fence(),
                operation_id=operation_id,
                failed=failed,
            ),
        )

    def _receipt(self) -> RunInstrumentProvisionReceipt:
        with self._lock:
            receipt = self._provisioning
        if receipt is None:
            raise RuntimeError("run instruments have not been provisioned")
        return receipt


def _domain_decision_operation_id(
    decision: DomainProposalDecision,
    *,
    operator_request_id: str | None,
) -> str:
    return "domain-decision." + stable_content_hash(
        content_fingerprint(
            {
                "schema": "scopecat.domain_decision_operation.v1",
                "proposal_index": decision.proposal_index,
                "proposal_fingerprint": decision.proposal.proposal_fingerprint,
                "operator_request_id": operator_request_id,
                "outcome": decision.outcome,
                "reason": decision.reason,
            }
        )
    )


def _point_plan_close_operation_id(
    *,
    completed_point_count: int,
    reason: str,
) -> str:
    return "point-plan.close." + stable_content_hash(
        content_fingerprint(
            {
                "schema": "scopecat.point_plan_close_operation.v1",
                "completed_point_count": completed_point_count,
                "reason": reason,
            }
        )
    )


def _json_document(model: BaseModel) -> dict[str, JsonValue]:
    return _JSON_DOCUMENT.validate_python(model.model_dump(mode="json"))


def _measurement_record_value_bytes(record: MeasurementRecord) -> int:
    return sum(
        (
            value.values.nbytes
            if isinstance(value, MeasurementArray)
            else value.value_nbytes
        )
        for values in (record.coordinates, record.observables)
        for value in values.values()
        if isinstance(value, MeasurementArray | MeasurementPartitionedArray)
    )


__all__ = [
    "ExecutorLeaseLostError",
    "LeaseSupervisor",
    "daemon_execution_session",
    "daemon_resumption_session",
]
