from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx2
import pytest
from pydantic import BaseModel
from scopecat_testkit.domain import domain_execution_identity
from scopecat_testkit.workflow_fixtures import load_config

import scopecat.daemon.execution as daemon_execution
from scopecat.adaptive_domains import DomainProposalAttempt, ResolvedDomainFragment
from scopecat.control.models import (
    AdaptiveRegionSpec,
    PointCoordinateSpec,
    RunPlanSummary,
)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.execution import daemon_execution_session
from scopecat.daemon.hardware_receipt_wire import encode_run_hardware_receipt
from scopecat.daemon.points import (
    RunDomainDecisionCommand,
    RunDomainDecisionView,
    RunPointPlanCloseCommand,
    RunPointPlanView,
)
from scopecat.daemon.wire import (
    ExecutorLease,
    ExecutorStartRequest,
    MeasurementFlushCommand,
    MeasurementFlushReceipt,
    MeasurementHeaderCommand,
    MeasurementIngestReceipt,
    MeasurementSealCommand,
    RunAdmission,
    RunCoverageAdvanceCommand,
    RunCoverageState,
    RunDomainJobStatePage,
    RunDomainJobStateView,
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionBatchReceipt,
    RunDomainJobTransitionView,
    RunHardwareBatchCommand,
    RunHardwareFinishCommand,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
    RunSubmission,
    TerminalRunCommitCommand,
)
from scopecat.execution.services import RunDomainJobTransitionWriter
from scopecat.kernel.point_identity import LogicalPointId, PointDomainId
from scopecat.kernel.points import AcceptedRunPoint, PointProposalAttempt
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.kernel.state import StateValue
from scopecat.measurements.recording_arrow import decode_measurement_append
from scopecat.optimization import DomainProposalDecision, DomainProposalSummary
from scopecat.records.config import config_content_hash
from scopecat.records.execution import (
    DomainExecutionReceipt,
    DomainJobCheckpoint,
    DomainJobCheckpointTransition,
    DomainJobInvocationTransition,
)
from scopecat.records.instrument import InstrumentStateSnapshot, state_member_target
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
    MeasurementDatasetReceipt,
    MeasurementDatasetSeal,
    measurement_fragment_content_hash,
)
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.runs.repository import TerminalRunCommit
from scopecat.sdk.instruments.commands import InstrumentStateAssignment
from scopecat.sdk.instruments.execution import (
    RunHardwareApply,
    RunHardwareBatch,
    RunHardwareBatchReceipt,
    RunHardwareFinalizationActionReceipt,
    RunHardwareFinalizationReceipt,
)
from scopecat.sdk.instruments.members import PropertyRef

_NOW = datetime(2026, 7, 23, 9, tzinfo=UTC)


def test_daemon_execution_ports_round_trip_through_fenced_http_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daemon_execution, "monotonic", lambda: 1.0)
    submission = RunSubmission(
        submission_id="submission-1",
        config=load_config(),
        request=RunRequest(experiment_id="scratch"),
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=None,
            initial_point_count=1,
            point_limit=3,
            adaptive_coordinate_ids=("frequency",),
            adaptive_scope="per_region",
            adaptive_region_count=1,
            adaptive_regions=(
                AdaptiveRegionSpec(
                    id="region-0", coordinates={}, initial_point_count=1
                ),
            ),
            coordinates=(
                PointCoordinateSpec(
                    id="frequency",
                    kind="quantity",
                    dimension="frequency",
                    unit="GHz",
                ),
            ),
        ),
    )
    admission = RunAdmission(
        submission_id=submission.submission_id,
        snapshot=RunSnapshot(
            run_id="run-1",
            created_at=_NOW,
            config_content_hash=config_content_hash(submission.config),
        ),
    )
    record = _measurement()
    header = _measurement_header()
    append = _measurement_append(record, header)
    seal = _measurement_seal(append, header)
    started_manifest = admission.snapshot
    fences: list[tuple[str, str]] = []
    terminal_commands: list[TerminalRunCommitCommand] = []
    hardware_operation_ids: list[str] = []
    hardware_sequences: list[int] = []
    coverage_ranges: list[tuple[int, int]] = []
    domain_job_transitions: list[str] = []
    domain_job_transition_batch_sizes: list[int] = []
    domain_job_transition_views: list[RunDomainJobTransitionView] = []
    measurement_ingest_ranges: list[tuple[int, int]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/executor/start"):
            command = ExecutorStartRequest.model_validate_json(request.content)
            assert command.executor_id == "notebook-1"
            assert command.on_resource_busy == "fail"
            return _model(_lease())
        if path.endswith("/instruments/provision"):
            command = RunInstrumentProvisionCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            return _model(
                RunInstrumentProvisionReceipt(
                    run_id="run-1",
                    operation_id=command.operation_id,
                    status="ready",
                    instrument_ids=("source-0",),
                    observed_state=(InstrumentStateSnapshot(instrument_id="source-0"),),
                    baseline_state=(InstrumentStateSnapshot(instrument_id="source-0"),),
                )
            )
        if path.endswith("/hardware/execute"):
            command = RunHardwareBatchCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            hardware_operation_ids.append(command.batch.operation_id)
            hardware_sequences.append(command.sequence)
            return httpx2.Response(
                200,
                content=encode_run_hardware_receipt(
                    RunHardwareBatchReceipt(operation_id=command.batch.operation_id)
                ),
            )
        if path.endswith("/coverage/advance"):
            command = RunCoverageAdvanceCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            coverage_ranges.append((command.start_index, command.point_count))
            return _model(
                RunCoverageState(
                    run_id="run-1",
                    completed_point_count=command.start_index + command.point_count,
                )
            )
        if path.endswith("/domain-jobs/transitions"):
            command = RunDomainJobTransitionBatchCommand.model_validate_json(
                request.content
            )
            _remember_fence(fences, "run-1", command)
            domain_job_transition_batch_sizes.append(len(command.items))
            committed: list[RunDomainJobTransitionView] = []
            for item in command.items:
                transition = item.transition
                if isinstance(transition, DomainJobCheckpointTransition):
                    domain_job_transitions.append(
                        f"checkpoint:{transition.checkpoint.revision}"
                    )
                elif isinstance(transition, DomainJobInvocationTransition):
                    domain_job_transitions.append("invocation")
                else:
                    domain_job_transitions.append(
                        f"terminal:{transition.receipt.status}"
                    )
                view = RunDomainJobTransitionView(
                    sequence=len(domain_job_transition_views) + 1,
                    run_id="run-1",
                    logical_compute_node_id=item.logical_compute_node_id,
                    point_ordinals=item.point_ordinals,
                    transition=transition,
                )
                domain_job_transition_views.append(view)
                committed.append(view)
            return _model(
                RunDomainJobTransitionBatchReceipt(
                    run_id="run-1",
                    items=tuple(committed),
                )
            )
        if path.endswith("/domain-jobs") and request.method == "GET":
            invocation = domain_job_transition_views[0].transition
            assert isinstance(invocation, DomainJobInvocationTransition)
            latest = domain_job_transition_views[-1]
            return _model(
                RunDomainJobStatePage(
                    run_id="run-1",
                    items=(
                        RunDomainJobStateView(
                            run_id="run-1",
                            invocation=invocation,
                            point_ordinals=latest.point_ordinals,
                            state="terminal",
                            invocation_sequence=domain_job_transition_views[0].sequence,
                            latest_sequence=latest.sequence,
                            transition_count=len(domain_job_transition_views),
                            latest_transition=latest.transition,
                        ),
                    ),
                )
            )
        if path.endswith("/point-plan/queue/next") and request.method == "GET":
            return httpx2.Response(200, content=b"null")
        if path.endswith("/point-plan/decisions"):
            command = RunDomainDecisionCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            return _model(
                RunDomainDecisionView(
                    operation_id=command.operation_id,
                    proposal_index=0,
                    occurred_at=_NOW,
                    proposal=command.proposal,
                    outcome="accepted",
                    accepted_point_start=command.accepted_points[0].point_index,
                    accepted_point_count=len(command.accepted_points),
                )
            )
        if path.endswith("/point-plan/close"):
            command = RunPointPlanCloseCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            return _model(
                RunPointPlanView(
                    run_id="run-1",
                    initial_point_count=1,
                    accepted_point_count=2,
                    point_limit=3,
                    decision_count=1,
                    optimizer_attempt_count=1,
                    operator_request_count=0,
                    plan_closed=True,
                    stop_reason=command.reason,
                )
            )
        if path.endswith("/hardware/finish"):
            command = RunHardwareFinishCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            hardware_operation_ids.append(command.operation_id)
            return _model(
                RunHardwareFinalizationReceipt(
                    operation_id=command.operation_id,
                    actions=(
                        RunHardwareFinalizationActionReceipt(
                            operation_id=f"{command.operation_id}.safe.source-0.0",
                            instrument_id="source-0",
                            kind="safe_operation",
                            status="completed",
                            metadata={"device_status": "safe"},
                        ),
                    ),
                )
            )
        if path.endswith("/measurements/ingest"):
            assert request.headers["content-type"] == (
                "application/vnd.apache.arrow.file"
            )
            fences.append(("run-1", request.headers["x-scopecat-lease-id"]))
            decoded = decode_measurement_append(
                request.content,
                header.dataset_schema,
            )
            measurement_ingest_ranges.append(
                (decoded.acquisition_start, len(decoded.records))
            )
            return _model(
                MeasurementIngestReceipt(
                    run_id="run-1",
                    received_record_count=(
                        decoded.acquisition_start + len(decoded.records)
                    ),
                    durable_record_count=0,
                )
            )
        if path.endswith("/measurements/flush"):
            command = MeasurementFlushCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            return _model(
                MeasurementFlushReceipt(
                    run_id="run-1",
                    durable_record_count=1,
                    durable_receipts=(_measurement_receipt(append),),
                )
            )
        if path.endswith("/measurements/header"):
            command = MeasurementHeaderCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            return _model(_header_receipt(command.header))
        if path.endswith("/measurements/seal"):
            command = MeasurementSealCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            return _model(_seal_receipt(command.seal))
        if path.endswith("/terminal"):
            command = TerminalRunCommitCommand.model_validate_json(request.content)
            _remember_fence(fences, "run-1", command)
            terminal_commands.append(command)
            return _model(
                started_manifest.model_copy(
                    update={
                        "outcome": command.outcome,
                        "contents": command.contents,
                    }
                )
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = _client(handler)
    session = daemon_execution_session(
        client,
        submission,
        admission,
        executor_id="notebook-1",
    )
    accepted = session.accepted
    assert session.begin() is None

    measurements = session.measurements
    instruments = session.instruments
    coverage = session.coverage
    domain_proposals = session.domain_proposals
    domain_job_transition_writer = session.domain_job_transitions
    assert coverage is not None
    assert domain_proposals is not None
    assert domain_job_transition_writer is not None
    assert instruments.observed_state == (
        InstrumentStateSnapshot(instrument_id="source-0"),
    )
    assert instruments.baseline_state == (
        InstrumentStateSnapshot(instrument_id="source-0"),
    )
    assert domain_proposals.next_queued() is None

    batch = RunHardwareBatch(
        operation_id="hardware.batch-1",
        actions=(
            RunHardwareApply(
                effect_id="point-0.apply.source-0",
                point_index=0,
                instrument_id="source-0",
                assignments=(
                    InstrumentStateAssignment(
                        resource_id="source-0",
                        target=state_member_target(
                            PropertyRef(
                                "test.set_frequency/v1",
                                (),
                                "frequency",
                            )
                        ),
                        value=StateValue(Quantity(5.0, "GHz")),
                    ),
                ),
            ),
        ),
    )
    assert instruments.execute(batch).operation_id == batch.operation_id
    coverage.advance(start_index=0, point_count=1)
    coverage.advance(start_index=1, point_count=2)
    coverage.flush()
    intent, execution_id = domain_execution_identity(
        run_id="run-1",
        logical_compute_node_id="domain.batch-0",
        invocation_id="invocation-1",
        target_intent={"selected_channel": "a"},
    )
    domain_job_transition_writer.invocation(
        logical_compute_node_id=execution_id.logical_compute_node_id,
        point_ordinals=(0, 1),
        execution_id=execution_id,
        intent=intent,
        write_ahead=False,
    )
    domain_job_transition_writer.checkpoint(
        logical_compute_node_id="domain.batch-0",
        point_ordinals=(0, 1),
        checkpoint=DomainJobCheckpoint(
            execution_key=execution_id.execution_key,
            job_id="provider-job",
            revision=1,
            resume_token={"cursor": "poll-1"},
        ),
    )
    domain_job_transition_writer.terminal(
        logical_compute_node_id="domain.batch-0",
        point_ordinals=(0, 1),
        receipt=DomainExecutionReceipt(
            execution_key=execution_id.execution_key,
            status="completed",
            result_fingerprint="results-v1",
            result_count=2,
        ),
        write_ahead=False,
    )
    domain_job_transition_writer.flush()
    [domain_job_state] = client.get_run_domain_jobs("run-1").items
    assert domain_job_state.state == "terminal"
    assert domain_job_state.transition_count == 3
    assert domain_job_state.invocation.intent.target_intent == {"selected_channel": "a"}
    strict_intent, strict_execution_id = domain_execution_identity(
        run_id="run-1",
        logical_compute_node_id="domain.batch-1",
        invocation_id="invocation-2",
    )
    domain_job_transition_writer.invocation(
        logical_compute_node_id=strict_execution_id.logical_compute_node_id,
        point_ordinals=(2,),
        execution_id=strict_execution_id,
        intent=strict_intent,
        write_ahead=True,
    )
    domain_job_transition_writer.terminal(
        logical_compute_node_id=strict_execution_id.logical_compute_node_id,
        point_ordinals=(2,),
        receipt=DomainExecutionReceipt(
            execution_key=strict_execution_id.execution_key,
            status="completed",
            result_fingerprint="results-v2",
            result_count=1,
        ),
        write_ahead=True,
    )
    _stage_buffered_domain_jobs(domain_job_transition_writer, count=32)
    proposal = DomainProposalAttempt(
        ResolvedDomainFragment.points(({"frequency": Quantity(5.2, "GHz")},)),
        region_ids=("region-0",),
        source="optimizer",
        based_on_region_revisions={"region-0": 1},
    )
    candidate = PointProposalAttempt(
        {"frequency": Quantity(5.2, "GHz")},
        source="optimizer",
        region_id="region-0",
        domain_proposal_fingerprint=proposal.proposal_fingerprint,
        based_on_region_revision=1,
    )
    accepted_point = AcceptedRunPoint.accept(
        candidate,
        logical_id=LogicalPointId(PointDomainId("scratch", "points"), 1),
    )
    domain_proposals.append(
        proposal,
        DomainProposalDecision(
            proposal_index=0,
            proposal=DomainProposalSummary.from_proposal(proposal),
            outcome="accepted",
            accepted_point_start=accepted_point.ordinal,
            accepted_point_count=1,
        ),
        (accepted_point,),
    )
    domain_proposals.close(completed_point_count=2, reason="test complete")
    finalization = instruments.finish(operation_id="hardware.finish", failed=False)
    assert finalization.operation_id == "hardware.finish"
    [finalization_action] = finalization.actions
    assert finalization_action.operation_id == "hardware.finish.safe.source-0.0"
    assert finalization_action.metadata == {"device_status": "safe"}

    assert measurements.initialize(header) == _header_receipt(header)
    assert measurements.ingest(append) == ()
    second = append.model_copy(
        update={
            "acquisition_start": 1,
            "records": (
                record.model_copy(
                    update={"logical_point_id": "point-1", "point_index": 1}
                ),
            ),
        }
    )
    third = append.model_copy(
        update={
            "acquisition_start": 2,
            "records": (
                record.model_copy(
                    update={"logical_point_id": "point-2", "point_index": 2}
                ),
            ),
        }
    )
    assert measurements.ingest(second) == ()
    assert measurements.ingest(third) == ()
    assert measurements.flush() == (_measurement_receipt(append),)
    assert measurement_ingest_ranges == [(0, 1), (1, 2)]
    assert measurements.seal(seal) == _seal_receipt(seal)

    outcome = _outcome()
    terminal = RunSnapshot(
        run_id=admission.run_id,
        created_at=accepted.created_at,
        config_content_hash=accepted.config_content_hash,
        outcome=outcome,
    )
    committed = session.commit_terminal(
        TerminalRunCommit(
            run_id=admission.run_id,
            outcome=outcome,
        )
    )

    assert committed == terminal
    assert terminal_commands[0].outcome == outcome
    assert terminal_commands[0].contents == ()
    assert terminal_commands[0].models == ()
    assert fences
    assert set(fences) == {("run-1", "lease-1")}
    assert hardware_operation_ids == [
        "hardware.batch-1",
        "hardware.finish",
    ]
    assert hardware_sequences == [0]
    assert coverage_ranges == [(0, 1), (1, 2)]
    assert domain_job_transitions[:5] == [
        "invocation",
        "checkpoint:1",
        "terminal:completed",
        "invocation",
        "terminal:completed",
    ]
    assert len(domain_job_transitions) == 69
    assert domain_job_transition_batch_sizes == [2, 1, 1, 1, 64]


def test_daemon_execution_rejects_provision_receipt_for_another_operation() -> None:
    submission = RunSubmission(
        submission_id="submission-1",
        config=load_config(),
        request=RunRequest(experiment_id="scratch"),
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
        ),
    )
    admission = RunAdmission(
        submission_id=submission.submission_id,
        snapshot=RunSnapshot(
            run_id="run-1",
            created_at=_NOW,
            config_content_hash=config_content_hash(submission.config),
        ),
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/executor/start"):
            return _model(_lease())
        if request.url.path.endswith("/instruments/provision"):
            return _model(
                RunInstrumentProvisionReceipt(
                    run_id="run-1",
                    operation_id="another-operation",
                    status="ready",
                )
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    session = daemon_execution_session(
        _client(handler),
        submission,
        admission,
        executor_id="notebook-1",
    )

    with pytest.raises(ValueError, match="does not match command"):
        session.begin()


@pytest.mark.parametrize("resuming", [False, True])
def test_initial_lease_cancellation_skips_remote_provisioning(resuming: bool) -> None:
    submission = RunSubmission(
        submission_id="submission-1",
        config=load_config(),
        request=RunRequest(experiment_id="scratch"),
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=0,
            initial_point_count=0,
            point_limit=0,
        ),
    )
    admission = RunAdmission(
        submission_id=submission.submission_id,
        snapshot=RunSnapshot(
            run_id="run-1",
            created_at=_NOW,
            config_content_hash=config_content_hash(submission.config),
        ),
    )
    provisioned = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal provisioned
        if request.url.path.endswith("/executor/start"):
            command = ExecutorStartRequest.model_validate_json(request.content)
            assert command.on_resource_busy == ("keep_queued" if resuming else "fail")
            return _model(
                _lease().model_copy(update={"cancellation_requested_at": _NOW})
            )
        if request.url.path.endswith("/instruments/provision"):
            provisioned = True
            command = RunInstrumentProvisionCommand.model_validate_json(request.content)
            return _model(
                RunInstrumentProvisionReceipt(
                    run_id="run-1",
                    operation_id=command.operation_id,
                    status="ready",
                )
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    session = (
        daemon_execution.daemon_resumption_session(
            _client(handler),
            admission.snapshot,
            executor_id="notebook-1",
            has_prior_execution_segment=True,
        )
        if resuming
        else daemon_execution_session(
            _client(handler),
            submission,
            admission,
            executor_id="notebook-1",
        )
    )

    session.begin()

    assert not provisioned
    assert session.cancellation_requested()
    assert not session.effects_ready()


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> DaemonClient:
    return DaemonClient(
        "http://daemon.local",
        transport=httpx2.MockTransport(handler),
    )


class _Fenced(Protocol):
    lease_id: str


def _stage_buffered_domain_jobs(
    writer: RunDomainJobTransitionWriter,
    *,
    count: int,
) -> None:
    for index in range(count):
        intent, execution_id = domain_execution_identity(
            run_id="run-1",
            logical_compute_node_id=f"domain.buffered-{index}",
            invocation_id=f"buffered-invocation-{index}",
        )
        writer.invocation(
            logical_compute_node_id=execution_id.logical_compute_node_id,
            point_ordinals=(index,),
            execution_id=execution_id,
            intent=intent,
            write_ahead=False,
        )
        writer.terminal(
            logical_compute_node_id=execution_id.logical_compute_node_id,
            point_ordinals=(index,),
            receipt=DomainExecutionReceipt(
                execution_key=execution_id.execution_key,
                status="completed",
                result_fingerprint=f"buffered-results-{index}",
                result_count=1,
            ),
            write_ahead=False,
        )


def _remember_fence(
    fences: list[tuple[str, str]],
    run_id: str,
    command: _Fenced,
) -> None:
    fences.append((run_id, command.lease_id))


def _model(model: BaseModel) -> httpx2.Response:
    return httpx2.Response(200, json=model.model_dump(mode="json"))


def _lease() -> ExecutorLease:
    return ExecutorLease(
        lease_id="lease-1",
        segment_id="segment-1",
        run_id="run-1",
        executor_id="notebook-1",
        issued_at=_NOW,
        expires_at=_NOW + timedelta(seconds=30),
        heartbeat_interval_seconds=10,
    )


def _measurement() -> MeasurementRecord:
    return MeasurementRecord(
        run_id="run-1",
        logical_point_id="point-0",
        point_index=0,
        coordinates={},
        observables={
            "signal": MeasurementScalar.create(
                dtype="float64",
                value=1.25,
                unit="ratio",
            )
        },
    )


def _measurement_header() -> MeasurementDatasetHeader:
    return MeasurementDatasetHeader(
        run_id="run-1",
        recording_contract_fingerprint="contract-1",
        dataset_schema=MeasurementDatasetSchema(
            dataset_id="raw-measurements",
            point_domain=MeasurementProductGridPointDomain(axes=[]),
            dimensions=[MeasurementDimension(id="point", kind="point", size=1)],
            variables=[
                MeasurementVariable(
                    id="signal",
                    role="observable",
                    dtype="float64",
                    unit="ratio",
                    dims=["point"],
                )
            ],
        ),
        expected_record_count=1,
        record_count_limit=1,
    )


def _measurement_append(
    record: MeasurementRecord,
    header: MeasurementDatasetHeader,
) -> MeasurementDatasetAppend:
    return MeasurementDatasetAppend(
        run_id="run-1",
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=(record,),
    )


def _header_receipt(
    header: MeasurementDatasetHeader,
) -> MeasurementDatasetReceipt:
    return MeasurementDatasetReceipt(
        operation_id=header.operation_id,
        dataset_content_hash=header.content_hash,
        acquisition_record_count=0,
    )


def _measurement_receipt(
    append: MeasurementDatasetAppend,
) -> MeasurementDatasetReceipt:
    return MeasurementDatasetReceipt(
        operation_id=append.operation_id,
        dataset_content_hash=append.content_hash,
        acquisition_record_count=len(append.records),
    )


def _measurement_seal(
    append: MeasurementDatasetAppend,
    header: MeasurementDatasetHeader,
) -> MeasurementDatasetSeal:
    return MeasurementDatasetSeal(
        run_id=append.run_id,
        header_content_hash=header.content_hash,
        record_count=1,
        fragment_record_count=1,
        fragment_content_hash=measurement_fragment_content_hash(
            header_content_hash=header.content_hash,
            record_content_hashes=append.record_content_hashes,
        ),
    )


def _seal_receipt(
    seal: MeasurementDatasetSeal,
) -> MeasurementDatasetReceipt:
    return MeasurementDatasetReceipt(
        operation_id=seal.operation_id,
        dataset_content_hash="sealed-dataset-content",
        acquisition_record_count=1,
    )


def _outcome() -> RunOutcome:
    return RunOutcome(
        run_id="run-1",
        result="succeeded",
        certainty="known",
        finished_at=_NOW + timedelta(seconds=2),
    )


@pytest.mark.parametrize("has_dataset", [True, False])
def test_slow_rpc_clock_does_not_force_per_point_measurement_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
    has_dataset: bool,
) -> None:
    import scopecat.daemon.execution as execution

    now = 0.0
    monkeypatch.setattr(execution, "monotonic", lambda: now)
    ranges: list[tuple[int, int]] = []
    flushes = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal now, flushes
        # Each RPC is slower than the progress reporting interval.
        now += 1.0
        if request.url.path.endswith("/executor/start"):
            return _model(_lease())
        if request.url.path.endswith("/measurements/header"):
            return _model(_header_receipt(_measurement_header()))
        if request.url.path.endswith("/measurements/flush"):
            flushes += 1
            return _model(
                MeasurementFlushReceipt(
                    run_id="run-1",
                    durable_record_count=0,
                    durable_receipts=(),
                )
            )
        assert request.url.path.endswith("/coverage/advance")
        command = RunCoverageAdvanceCommand.model_validate_json(request.content)
        ranges.append((command.start_index, command.point_count))
        return _model(
            RunCoverageState(
                run_id="run-1",
                completed_point_count=command.start_index + command.point_count,
            )
        )

    with _client(handler) as client:
        authority = execution._LeaseAuthority(
            client=client,
            run_id="run-1",
            executor_id="test",
            lease_supervisor=None,
        )
        authority.start()
        measurements = execution._DaemonMeasurementRepository(authority)
        if has_dataset:
            measurements.initialize(_measurement_header())
        coverage = execution._DaemonRunCoverage(
            authority,
            measurements,
            execution._DaemonRunRecoveryGroups(authority),
        )
        for point in range(33):
            now += 1.0
            coverage.advance(start_index=point, point_count=1)
        if has_dataset:
            assert ranges == [(0, 1)]
            assert flushes == 1
        else:
            assert ranges == [(point, 1) for point in range(33)]
            assert flushes == 0
        for point in range(33, 257):
            now += 1.0
            coverage.advance(start_index=point, point_count=1)
        if has_dataset:
            assert ranges == [(0, 1), (1, 256)]
            assert flushes == 2
        coverage.advance(start_index=257, point_count=1)
        coverage.flush()
        if has_dataset:
            assert ranges == [(0, 1), (1, 256), (257, 1)]
            assert flushes == 3
