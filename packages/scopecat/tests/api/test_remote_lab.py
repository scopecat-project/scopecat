# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import cast

import httpx2
import pyarrow as pa
import pytest
from pydantic import BaseModel
from scopecat_testkit.domain import domain_execution_identity
from scopecat_testkit.measurement_models import signal_point_schema, signal_record
from scopecat_testkit.planning import plan_configured_experiment
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider
from scopecat_testkit.workflow_fixtures import (
    load_config,
    load_invocation,
)

import scopecat.api._runner as runner_module
from scopecat.api._runner import _DaemonRunner
from scopecat.api.analysis import AnalysisContext
from scopecat.api.lab import LabClient
from scopecat.api.run import RunHandle
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.changes import parameter_change_proposal_from_updates
from scopecat.config.drafts import ConfigDraft
from scopecat.config.inventory import InstrumentInventoryRekey
from scopecat.config.registry.records import (
    CandidateConfigRegistrySource,
    ConfigActivationOperation,
    ConfigPublishOperation,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    DirectConfigRegistrySource,
    ManualConfigDraftRegistrySource,
)
from scopecat.config.resolution import config_revision_entry_id
from scopecat.control.models import (
    RunExecutionSegment,
    RunExecutionSegmentPage,
    RunResourceRequirement,
)
from scopecat.daemon.client import (
    DaemonClient,
    DaemonConflictError,
    DaemonUnavailableError,
)
from scopecat.daemon.execution import ExecutorLeaseLostError
from scopecat.daemon.points import RunPointPlanView
from scopecat.daemon.views import (
    ActiveConfigView,
    ConfigActivationPage,
    ConfigDraftPreview,
    ConfigRegistryPage,
    MeasurementPreview,
    RunAdmissionView,
    RunConfigView,
    RunControlView,
    RunDetail,
    RunPlanView,
    RunRequestView,
    RunSummary,
    RunSummaryPage,
)
from scopecat.daemon.wire import (
    AttentionResolutionCommand,
    AttentionResolutionReceipt,
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    DirectConfigRevisionSource,
    ExecutorLease,
    InstrumentContractCatalogRequest,
    InstrumentInventoryMigrationCommand,
    InstrumentInventoryMigrationReceipt,
    ManualConfigDraftRevisionSource,
    RunAdmission,
    RunCoverageState,
    RunDomainJobStatePage,
    RunDomainJobStateView,
    RunDomainJobTransitionPage,
    RunDomainJobTransitionView,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
    RunRecoveryGroupPage,
    RunSubmission,
    TerminalRunCommitCommand,
)
from scopecat.execution.program import RunProgram
from scopecat.execution.services import ExecutionSession
from scopecat.kernel.errors import (
    RunCancelled,
    RunFailed,
    RunFailure,
    RunIndeterminate,
    SessionClosedError,
)
from scopecat.kernel.point_identity import LogicalPointId
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.run_outcome import RunCertainty, RunOutcome, RunResult
from scopecat.measurements.points import RunPointCatalog
from scopecat.measurements.projection import MeasurementProjection
from scopecat.measurements.results import MeasurementDataset
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.preview import build_run_program_preview
from scopecat.planning.service import PlannedRun
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import (
    ConfigContentHash,
    ConfigProfileSnapshot,
    config_content_hash,
    instrument_bindings,
)
from scopecat.records.content import ContentEntry
from scopecat.records.execution import DomainJobInvocationTransition
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.run import ConfigRegistryRunConfigSource, RunSnapshot
from scopecat.runs.data import RunMeasurementDatasetResult
from scopecat.runs.repository import TerminalRunCommit
from scopecat.sdk.instruments import InstrumentProviderContext

_NOW = datetime(2026, 7, 23, 9, tzinfo=UTC)


def test_run_handle_exposes_bounded_domain_job_diagnostics() -> None:
    intent, execution_id = domain_execution_identity(
        run_id="run-domain-diagnostics",
        logical_compute_node_id="domain.batch-0",
        invocation_id="invocation-1",
        target_intent={"program": "rb-fragment-7"},
    )
    invocation = DomainJobInvocationTransition(
        execution_id=execution_id,
        intent=intent,
    )
    transition = RunDomainJobTransitionView(
        sequence=9,
        run_id=execution_id.run_id,
        logical_compute_node_id=execution_id.logical_compute_node_id,
        point_ordinals=(4, 5),
        transition=invocation,
    )
    state_page = RunDomainJobStatePage(
        run_id=execution_id.run_id,
        items=(
            RunDomainJobStateView(
                run_id=execution_id.run_id,
                invocation=invocation,
                point_ordinals=transition.point_ordinals,
                state="invocation_unknown",
                invocation_sequence=transition.sequence,
                latest_sequence=transition.sequence,
                transition_count=1,
                latest_transition=invocation,
            ),
        ),
        next_cursor=9,
    )
    transition_page = RunDomainJobTransitionPage(
        run_id=execution_id.run_id,
        items=(transition,),
        next_cursor=9,
    )
    segment_page = RunExecutionSegmentPage(
        items=(
            RunExecutionSegment(
                sequence=3,
                segment_id="segment-1",
                run_id=execution_id.run_id,
                ordinal=0,
                executor_id="notebook-1",
                run_contract_fingerprint="a" * 64,
                started_at=_NOW,
                start_point_count=0,
            ),
        ),
        next_cursor=2,
    )
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/execution-segments"):
            return httpx2.Response(200, content=segment_page.model_dump_json())
        if request.url.path.endswith("/domain-jobs"):
            return httpx2.Response(200, content=state_page.model_dump_json())
        if request.url.path.endswith("/domain-jobs/transitions"):
            return httpx2.Response(200, content=transition_page.model_dump_json())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    run = RunHandle(
        session=LabClient(_client(handler)),
        id=execution_id.run_id,
    )

    assert run.execution_segments(limit=3, before=4) == segment_page
    assert run.domain_jobs(limit=7, before=12) == state_page
    assert run.domain_job_transitions(limit=5, before=10) == transition_page
    assert [(request.url.path, dict(request.url.params)) for request in requests] == [
        (
            "/api/v1/runs/run-domain-diagnostics/execution-segments",
            {"limit": "3", "before": "4"},
        ),
        (
            "/api/v1/runs/run-domain-diagnostics/domain-jobs",
            {"limit": "7", "before": "12"},
        ),
        (
            "/api/v1/runs/run-domain-diagnostics/domain-jobs/transitions",
            {"limit": "5", "before": "10"},
        ),
    ]


def test_lab_runs_preserves_bounded_page_navigation() -> None:
    snapshot = RunSnapshot(
        run_id="run-page",
        created_at=_NOW,
        config_content_hash=config_content_hash(load_config()),
    )
    summary = RunSummary(
        control=RunControlView(
            sequence=12,
            admission=RunAdmissionView(
                run_id=snapshot.run_id,
                run_contract_fingerprint="a" * 64,
                plan=RunPlanView(
                    experiment_id="page-test",
                    experiment_kind="test",
                    point_plan_fingerprint="a" * 64,
                    measurement_contract_fingerprint="b" * 64,
                    point_count=1,
                    initial_point_count=1,
                    point_limit=1,
                ),
                admitted_at=_NOW,
            ),
            state="closed",
            updated_at=_NOW,
            completed_point_count=1,
            point_plan=RunPointPlanView(
                run_id=snapshot.run_id,
                initial_point_count=1,
                accepted_point_count=1,
                point_limit=1,
                decision_count=0,
                optimizer_attempt_count=0,
                operator_request_count=0,
                plan_closed=True,
                stop_reason="static point plan",
            ),
        ),
        snapshot=snapshot,
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/runs"
        assert dict(request.url.params) == {
            "limit": "1",
            "before": "9",
            "state": "closed",
        }
        return _model(RunSummaryPage(items=(summary,), next_cursor=8))

    page = LabClient(_client(handler)).runs(limit=1, before=9, state="closed")

    assert tuple(run.id for run in page.items) == (snapshot.run_id,)
    assert page.next_cursor == 8


def test_remote_run_uses_full_dataset_batches_and_projected_arrow_pages() -> None:
    schema = signal_point_schema(size=3)
    dataset_entry = ContentEntry(
        role="dataset",
        id="raw-measurements",
        kind="measurement_dataset",
        content_hash="measurement-content",
        schema=schema.model_dump(mode="json"),
        metadata={"experiment": "remote-page-test"},
    )
    snapshot = RunSnapshot(
        run_id="run-batches",
        config_content_hash=config_content_hash(load_config()),
    )
    detail = RunDetail(
        control=RunControlView(
            sequence=1,
            admission=RunAdmissionView(
                run_id=snapshot.run_id,
                run_contract_fingerprint="a" * 64,
                plan=RunPlanView(
                    experiment_id="remote-batches",
                    experiment_kind="test",
                    point_plan_fingerprint="a" * 64,
                    measurement_contract_fingerprint="b" * 64,
                    point_count=3,
                    initial_point_count=3,
                    point_limit=3,
                ),
                admitted_at=_NOW,
            ),
            state="closed",
            updated_at=_NOW,
            completed_point_count=3,
            point_plan=RunPointPlanView(
                run_id=snapshot.run_id,
                initial_point_count=3,
                accepted_point_count=3,
                point_limit=3,
                decision_count=0,
                optimizer_attempt_count=0,
                operator_request_count=0,
                plan_closed=True,
                stop_reason="static point plan",
            ),
        ),
        snapshot=snapshot,
    )
    records = tuple(
        signal_record(point_index=index).model_copy(update={"run_id": snapshot.run_id})
        for index in range(3)
    )
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/v1/runs/run-batches":
            return _model(detail)
        if request.url.path == (
            "/api/v1/runs/run-batches/contents/dataset/raw-measurements"
        ):
            return _model(dataset_entry)
        if request.url.path == ("/api/v1/runs/run-batches/datasets/raw-measurements"):
            return _model(
                RunMeasurementDatasetResult(
                    dataset_entry=dataset_entry,
                    dataset=MeasurementDataset(
                        dataset_schema=schema,
                        records=records,
                        metadata=dataset_entry.metadata,
                    ),
                )
            )
        if request.url.path == "/api/v1/runs/run-batches/measurements/arrow":
            query = json.loads(request.content)
            offset = query["offset"]
            items = records[offset : offset + query["limit"]]
            table = pa.table(
                {
                    "point_index": [record.point_index for record in items],
                    "logical_point_id": [record.logical_point_id for record in items],
                    "signal": [
                        cast("MeasurementScalar", record.observables["signal"]).value
                        for record in items
                    ],
                    "signal__unavailable_reason": [None] * len(items),
                }
            )
            sink = pa.BufferOutputStream()
            with pa.ipc.new_stream(sink, table.schema) as writer:
                writer.write_table(table)
            next_offset = offset + len(items)
            return httpx2.Response(
                200,
                content=sink.getvalue().to_pybytes(),
                headers={
                    "X-Scopecat-Snapshot-Size": str(len(records)),
                    **(
                        {"X-Scopecat-Next-Offset": str(next_offset)}
                        if next_offset < len(records)
                        else {}
                    ),
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    lab = LabClient(_client(handler))
    run = RunHandle(session=lab, id=snapshot.run_id)

    reader = cast(
        "Iterator[object]",
        AnalysisContext(run=run)
        .measurements()
        .project({"signal": "signal"}, diagnostics="reason")
        .to_record_batch_reader(batch_size=2),
    )
    assert [request.url.path for request in requests] == [
        "/api/v1/runs/run-batches/contents/dataset/raw-measurements",
        "/api/v1/runs/run-batches/measurements/arrow",
    ]

    assert len(list(reader)) == 2
    assert [request.url.path for request in requests] == [
        "/api/v1/runs/run-batches/contents/dataset/raw-measurements",
        "/api/v1/runs/run-batches/measurements/arrow",
        "/api/v1/runs/run-batches/measurements/arrow",
    ]
    queries = [
        json.loads(request.content)
        for request in requests
        if request.url.path.endswith("/measurements/arrow")
    ]
    assert [query["offset"] for query in queries] == [0, 2]
    assert [query["snapshot_size"] for query in queries] == [None, 3]
    assert all(
        query["columns"] == [{"name": "signal", "variable_id": "signal"}]
        for query in queries
    )


def test_lab_preview_and_run_are_direct_prepare_shortcuts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = load_invocation()
    preview_result = object()
    run_result = object()
    prepared_calls: list[tuple[object, object]] = []
    forwarded: list[tuple[str, dict[str, object]]] = []

    class Prepared:
        def preview(self, **kwargs: object) -> object:
            forwarded.append(("preview", kwargs))
            return preview_result

        def run(self, **kwargs: object) -> object:
            forwarded.append(("run", kwargs))
            return run_result

    def prepare(
        _lab: LabClient,
        experiment: object,
        *,
        config: object = None,
    ) -> Prepared:
        prepared_calls.append((experiment, config))
        return Prepared()

    monkeypatch.setattr(LabClient, "prepare", prepare)
    lab = object.__new__(LabClient)

    assert lab.preview(invocation, config="active", name="preview") is preview_result
    assert lab.run(invocation, config="candidate", name="run") is run_result
    assert prepared_calls == [(invocation, "active"), (invocation, "candidate")]
    assert forwarded == [
        (
            "preview",
            {
                "point": "first",
                "coordinates": None,
                "coordinate_mode": "exact",
                "inspection_query": None,
                "name": "preview",
                "tags": (),
                "description": None,
                "metadata": None,
                "operator": None,
                "sample": None,
                "samples": (),
            },
        ),
        (
            "run",
            {
                "name": "run",
                "tags": (),
                "description": None,
                "metadata": None,
                "operator": None,
                "sample": None,
                "samples": (),
            },
        ),
    ]


def test_execute_submits_complete_plan_and_heartbeats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned_without_source = _planned()
    planned = replace(
        planned_without_source,
        config_source=ConfigRegistryRunConfigSource(
            selector="active",
            entry_id="baseline",
            config_ref="config-registry/configs/baseline.json",
            content_hash=config_content_hash(planned_without_source.config),
            registry_generation=3,
        ),
    )
    preview = build_run_program_preview(planned.program)

    def fail_preview(_program: RunProgram) -> None:
        pytest.fail("execution admission must not build a preview")

    monkeypatch.setattr(
        runner_module,
        "build_run_program_preview",
        fail_preview,
    )
    heartbeat_seen = Event()
    heartbeat_count = 0
    submissions: list[RunSubmission] = []
    forwarded: dict[str, object] = {}

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        nonlocal heartbeat_count
        path = http_request.url.path
        if path.endswith("/runs"):
            submission = RunSubmission.model_validate_json(http_request.content)
            submissions.append(submission)
            return _model(_admission(submission), status_code=201)
        if path.endswith("/executor/start"):
            return _model(_lease(heartbeat_interval=0.01))
        if path.endswith("/instruments/provision"):
            return _model(_provisioning_receipt(planned.program, http_request))
        if path.endswith("/executor/heartbeat"):
            heartbeat_count += 1
            heartbeat_seen.set()
            return _model(
                _lease(heartbeat_interval=0.01).model_copy(
                    update={"cancellation_requested_at": _NOW}
                )
            )
        raise AssertionError(f"unexpected request: {http_request.method} {path}")

    def execute(
        *,
        program: RunProgram,
        session: ExecutionSession,
    ) -> RunSnapshot:
        forwarded["program"] = program
        accepted = session.accepted
        session.begin()
        assert heartbeat_seen.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not session.cancellation_requested():
            if time.monotonic() >= deadline:
                raise AssertionError("heartbeat did not expose cancellation request")
            time.sleep(0.001)
        return _terminal_manifest(accepted)

    monkeypatch.setattr(runner_module, "execute_admitted_run", execute)

    result = _DaemonRunner(_client(handler), None).execute(
        planned,
        executor_id="notebook-1",
        submission_id="submission-1",
    )
    completed_heartbeats = heartbeat_count
    time.sleep(0.03)

    [submission] = submissions
    assert submission.submission_id == "submission-1"
    assert submission.config == planned.config
    assert submission.config_source == planned.config_source
    assert submission.request == planned.request
    assert submission.plan.experiment_id == preview.experiment_id
    assert submission.plan.experiment_kind == preview.experiment_kind
    assert submission.plan.point_count == preview.point_count
    assert submission.plan.coordinate_ids == preview.coordinate_ids
    assert submission.plan.record_ids == tuple(record.id for record in preview.records)
    assert submission.plan.host_instrument_order == planned.program.resource_order
    assert planned.program.host is not None
    assert submission.plan.run_resource_requirements == tuple(
        RunResourceRequirement(id=requirement.id, kind=requirement.kind)
        for requirement in planned.program.resource_requirements
    )
    assert forwarded["program"] == planned.program
    assert result.status == "completed"
    assert completed_heartbeats >= 1
    assert heartbeat_count == completed_heartbeats


def test_execute_replays_terminal_success_after_submission_response_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned = _planned()
    prepared, intent_hash = runner_module._prepare_run_submission(
        planned,
        submission_id="stable-submission",
    )
    assert intent_hash == f"sha256:{prepared.intent_content_hash}"

    durable_admission: RunAdmission | None = None
    submissions: list[RunSubmission] = []
    requests: list[str] = []

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        nonlocal durable_admission
        path = http_request.url.path
        requests.append(path)
        if not path.endswith("/runs"):
            raise AssertionError(f"terminal replay must not execute: {path}")
        submission = RunSubmission.model_validate_json(http_request.content)
        submissions.append(submission)
        if durable_admission is None:
            accepted = _admission(submission)
            durable_admission = accepted.model_copy(
                update={"snapshot": _terminal_manifest(accepted.snapshot)}
            )
            raise httpx2.ReadError("submission response was lost", request=http_request)
        return _model(durable_admission, status_code=201)

    def fail_execution(**_kwargs: object) -> RunSnapshot:
        pytest.fail("terminal admission must not restart execution")

    monkeypatch.setattr(runner_module, "execute_admitted_run", fail_execution)
    runner = _DaemonRunner(_client(handler), None)

    with pytest.raises(httpx2.ReadError, match="response was lost"):
        runner.execute(
            planned,
            submission_id="stable-submission",
            executor_id="stable-executor",
        )
    replayed = runner.execute(
        planned,
        submission_id="stable-submission",
        executor_id="stable-executor",
    )

    assert durable_admission is not None
    assert replayed == durable_admission.snapshot
    assert replayed.status == "completed"
    assert submissions == [prepared, prepared]
    assert requests == ["/api/v1/runs", "/api/v1/runs"]


def test_lab_resume_replans_and_authorizes_a_new_execution_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned = _planned()
    submission, _ = runner_module._prepare_run_submission(
        planned,
        submission_id="original-submission",
    )
    snapshot = _admission(submission).snapshot
    summary = submission.plan
    detail = RunDetail(
        control=RunControlView(
            sequence=1,
            admission=RunAdmissionView(
                run_id=snapshot.run_id,
                run_contract_fingerprint=submission.intent_content_hash,
                plan=RunPlanView(
                    experiment_id=summary.experiment_id,
                    experiment_kind=summary.experiment_kind,
                    point_plan_fingerprint=summary.point_plan_fingerprint,
                    measurement_contract_fingerprint=(
                        summary.measurement_contract_fingerprint
                    ),
                    point_count=summary.point_count,
                    initial_point_count=summary.initial_point_count,
                    point_limit=summary.point_limit,
                    coordinates=summary.coordinates,
                    sampled_points=summary.sampled_points,
                    record_ids=summary.record_ids,
                    run_resource_requirements=summary.run_resource_requirements,
                ),
                admitted_at=_NOW,
            ),
            state="attention_required",
            updated_at=_NOW,
            attention_reason="executor_disconnected",
            completed_point_count=1,
            point_plan=RunPointPlanView(
                run_id=snapshot.run_id,
                initial_point_count=summary.initial_point_count,
                accepted_point_count=summary.initial_point_count,
                point_limit=summary.point_limit,
                decision_count=0,
                optimizer_attempt_count=0,
                operator_request_count=0,
                plan_closed=True,
                stop_reason="static point plan",
            ),
        ),
        snapshot=snapshot,
    )
    incompatible_detail = detail.model_copy(
        update={
            "control": detail.control.model_copy(
                update={
                    "admission": detail.control.admission.model_copy(
                        update={"run_contract_fingerprint": "b" * 64}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError, match="accepted run contract"):
        runner_module._validate_resumed_plan(
            planned,
            detail=incompatible_detail,
            request=planned.request,
        )
    [record] = planned.program.measurements.records
    incompatible_measurements = MeasurementProjection(
        planned.program.measurements.catalog,
        (replace(record, dtype="int64"),),
    )
    incompatible_recording = replace(
        planned,
        program=replace(planned.program, measurements=incompatible_measurements),
    )
    assert incompatible_recording.request == planned.request
    assert tuple(
        item.id for item in incompatible_recording.program.measurements.records
    ) == tuple(item.id for item in planned.program.measurements.records)
    with pytest.raises(ValueError, match="accepted run contract"):
        runner_module._validate_resumed_plan(
            incompatible_recording,
            detail=detail,
            request=planned.request,
        )
    segment_page = RunExecutionSegmentPage(
        items=(
            RunExecutionSegment(
                sequence=1,
                segment_id="segment-0",
                run_id=snapshot.run_id,
                ordinal=0,
                executor_id="lost-notebook",
                run_contract_fingerprint=submission.intent_content_hash,
                started_at=_NOW,
                start_point_count=0,
                ended_at=_NOW + timedelta(seconds=1),
                end_point_count=1,
                result="interrupted",
                certainty="indeterminate",
                reason="executor_disconnected",
            ),
        )
    )
    planned_schema = planned.program.measurements.schema
    assert planned_schema is not None
    preview_schema = planned_schema.model_copy(
        update={"dataset_id": "incompatible-measurements"}
    )
    requests: list[str] = []

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        path = http_request.url.path
        requests.append(path)
        if path == "/api/v1/runs/run-1":
            return _model(detail)
        if path.endswith("/config"):
            return _model(
                RunConfigView(
                    run_id=snapshot.run_id,
                    config_content_hash=snapshot.config_content_hash,
                    config=planned.config,
                )
            )
        if path.endswith("/request"):
            return _model(
                RunRequestView(run_id=snapshot.run_id, request=planned.request)
            )
        if path == "/api/v1/instrument-contracts/resolve":
            return _model(_instrument_catalog(planned.config))
        if path.endswith("/measurements/preview"):
            assert dict(http_request.url.params) == {"limit": "1"}
            return _model(
                MeasurementPreview(
                    dataset_schema=preview_schema,
                )
            )
        if path.endswith("/execution-segments"):
            assert dict(http_request.url.params) == {"limit": "1"}
            return _model(segment_page)
        if path.endswith("/attention"):
            command = AttentionResolutionCommand.model_validate_json(
                http_request.content
            )
            assert command == AttentionResolutionCommand.continue_run(
                run_contract_fingerprint=submission.intent_content_hash
            )
            return _model(
                AttentionResolutionReceipt(
                    run_id=snapshot.run_id,
                    disposition="continue",
                    state="queued",
                    released_resource_count=1,
                )
            )
        raise AssertionError(f"unexpected request: {http_request.method} {path}")

    captured: dict[str, object] = {}

    def execute(
        *,
        program: RunProgram,
        session: ExecutionSession,
    ) -> RunSnapshot:
        captured["program"] = program
        captured["session"] = session
        assert session.has_prior_execution_segment()
        return _terminal_manifest(session.accepted)

    monkeypatch.setattr(runner_module, "execute_admitted_run", execute)
    lab = LabClient(_client(handler))

    with pytest.raises(ValueError, match="durable dataset"):
        lab.resume("run-1", load_invocation(), executor_id="notebook-2")
    assert "/api/v1/runs/run-1/attention" not in requests

    preview_schema = planned_schema
    requests.clear()
    resumed = lab.resume("run-1", load_invocation(), executor_id="notebook-2")

    assert resumed.id == snapshot.run_id
    resumed_program = captured["program"]
    assert isinstance(resumed_program, RunProgram)
    assert resumed_program.config_content_hash == planned.program.config_content_hash
    assert resumed_program.resource_requirements == (
        planned.program.resource_requirements
    )
    assert resumed_program.points.contract == planned.program.points.contract
    assert requests == [
        "/api/v1/runs/run-1",
        "/api/v1/runs/run-1/config",
        "/api/v1/runs/run-1/request",
        "/api/v1/instrument-contracts/resolve",
        "/api/v1/runs/run-1/measurements/preview",
        "/api/v1/runs/run-1/execution-segments",
        "/api/v1/runs/run-1/attention",
    ]


def test_run_contract_identifies_points_beyond_the_bounded_plan_preview() -> None:
    planned = _planned()
    prototype = planned.program.points.points[0]
    coordinate_id = planned.program.points.coordinate_ids[0]

    def with_last_frequency(value: float) -> PlannedRun:
        points = tuple(
            AcceptedRunPoint(
                logical_id=LogicalPointId(
                    domain_id=prototype.logical_id.domain_id,
                    logical_ordinal=index,
                ),
                coordinates={
                    coordinate_id: Quantity(
                        value=value if index == 256 else 5.0 + index / 1000,
                        unit="GHz",
                    )
                },
            )
            for index in range(257)
        )
        catalog = RunPointCatalog(
            contract=replace(
                planned.program.points.contract,
                point_count=len(points),
                point_limit=len(points),
            ),
            points=points,
        )
        return replace(planned, program=replace(planned.program, points=catalog))

    first = runner_module._run_plan_summary(with_last_frequency(5.256))
    changed = runner_module._run_plan_summary(with_last_frequency(5.5))

    assert first.sampled_points_truncated
    assert first.sampled_points == changed.sampled_points
    assert first.point_plan_fingerprint != changed.point_plan_fingerprint
    assert (
        first.measurement_contract_fingerprint
        == changed.measurement_contract_fingerprint
    )


def test_equivalent_replans_have_the_same_durable_run_contract() -> None:
    first, _ = runner_module._prepare_run_submission(
        _planned(),
        submission_id="first-attempt",
    )
    second, _ = runner_module._prepare_run_submission(
        _planned(),
        submission_id="second-attempt",
    )

    assert first.plan.point_plan_fingerprint == second.plan.point_plan_fingerprint
    assert (
        first.plan.measurement_contract_fingerprint
        == second.plan.measurement_contract_fingerprint
    )
    assert first.intent_content_hash == second.intent_content_hash


@pytest.mark.parametrize(
    ("result", "certainty", "error_type"),
    [
        ("failed", "known", RunFailed),
        ("cancelled", "known", RunCancelled),
        ("failed", "indeterminate", RunIndeterminate),
    ],
)
def test_execute_replays_terminal_failure_with_existing_kernel_semantics(
    result: RunResult,
    certainty: RunCertainty,
    error_type: type[RunFailure],
) -> None:
    planned = _planned()
    requests: list[str] = []
    outcome = RunOutcome(
        run_id="run-1",
        result=result,
        certainty=certainty,
        problems=(
            problem(
                "execution.replayed_terminal",
                "the durable run already terminated",
                phase=ProblemPhase.EXECUTION,
            ),
        ),
    )

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        path = http_request.url.path
        requests.append(path)
        if not path.endswith("/runs"):
            raise AssertionError(f"terminal replay must not execute: {path}")
        submission = RunSubmission.model_validate_json(http_request.content)
        admitted = _admission(submission)
        return _model(
            admitted.model_copy(
                update={
                    "snapshot": admitted.snapshot.model_copy(
                        update={"outcome": outcome}
                    )
                }
            ),
            status_code=201,
        )

    with pytest.raises(error_type) as error:
        _DaemonRunner(_client(handler), None).execute(
            planned,
            submission_id="stable-submission",
            executor_id="stable-executor",
        )

    assert error.value.run_id == "run-1"
    assert error.value.outcome == outcome
    assert requests == ["/api/v1/runs"]


def test_execute_honors_initial_lease_cancellation_before_remote_effects(
    tmp_path: Path,
) -> None:
    planned = _planned()
    requests: list[str] = []
    admissions: list[RunAdmission] = []

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        path = http_request.url.path
        requests.append(path)
        if path.endswith("/runs"):
            submission = RunSubmission.model_validate_json(http_request.content)
            admission = _admission(submission)
            admissions.append(admission)
            return _model(admission, status_code=201)
        if path.endswith("/coverage"):
            return _model(RunCoverageState(run_id="run-1", completed_point_count=0))
        if path.endswith("/recovery-groups"):
            return _model(RunRecoveryGroupPage(run_id="run-1", items=()))
        if path.endswith("/executor/start"):
            return _model(
                _lease(heartbeat_interval=10).model_copy(
                    update={"cancellation_requested_at": _NOW}
                )
            )
        if path.endswith("/terminal"):
            command = TerminalRunCommitCommand.model_validate_json(http_request.content)
            assert command.outcome.result == "cancelled"
            assert command.outcome.certainty == "known"
            return _model(
                admissions[-1].snapshot.model_copy(
                    update={
                        "outcome": command.outcome,
                        "contents": command.contents,
                    }
                )
            )
        raise AssertionError(f"unexpected request: {http_request.method} {path}")

    with pytest.raises(RunCancelled) as error:
        _DaemonRunner(_client(handler), None).execute(
            planned,
            executor_id="notebook-1",
        )

    assert error.value.outcome.result == "cancelled"
    assert requests == [
        "/api/v1/runs",
        "/api/v1/runs/run-1/coverage",
        "/api/v1/runs/run-1/recovery-groups",
        "/api/v1/runs/run-1/executor/start",
        "/api/v1/runs/run-1/terminal",
    ]


def test_execute_fences_effects_after_heartbeat_loses_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned = _planned()
    heartbeat_attempted = Event()
    admissions: list[RunAdmission] = []

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        path = http_request.url.path
        if path.endswith("/runs"):
            submission = RunSubmission.model_validate_json(http_request.content)
            admission = _admission(submission)
            admissions.append(admission)
            return _model(admission, status_code=201)
        if path.endswith("/executor/start"):
            return _model(_lease(heartbeat_interval=0.005))
        if path.endswith("/instruments/provision"):
            return _model(_provisioning_receipt(planned.program, http_request))
        if path.endswith("/executor/heartbeat"):
            heartbeat_attempted.set()
            return httpx2.Response(409, json={"detail": "executor lease expired"})
        if path.endswith("/terminal"):
            return _model(
                admissions[-1].snapshot.model_copy(
                    update={
                        "outcome": RunOutcome(
                            run_id=admissions[-1].snapshot.run_id,
                            result="succeeded",
                            certainty="known",
                        )
                    }
                )
            )
        raise AssertionError(f"unexpected request: {http_request.method} {path}")

    def execute(
        *,
        program: RunProgram,
        session: ExecutionSession,
    ) -> RunSnapshot:
        del program
        session.begin()
        assert heartbeat_attempted.wait(timeout=5)
        terminal = TerminalRunCommit(
            run_id=session.run_id,
            outcome=RunOutcome(
                run_id=session.run_id,
                result="succeeded",
                certainty="known",
            ),
        )
        deadline = time.monotonic() + 5
        while True:
            session.commit_terminal(terminal)
            if time.monotonic() >= deadline:
                raise AssertionError("heartbeat failure did not fence effects")
            time.sleep(0.001)

    monkeypatch.setattr(runner_module, "execute_admitted_run", execute)

    with pytest.raises(
        ExecutorLeaseLostError,
        match=r"lease 'lease-1'.*is no longer live",
    ) as error:
        _DaemonRunner(_client(handler), None).execute(
            planned,
            executor_id="notebook-1",
        )

    assert isinstance(error.value.cause, DaemonConflictError)


def test_cancellation_does_not_wait_for_lease_renewal() -> None:
    requested = Event()
    lease = _lease(heartbeat_interval=10)
    renewals = 0

    def heartbeat() -> ExecutorLease:
        nonlocal renewals
        renewals += 1
        return lease

    supervisor = runner_module._LeaseHeartbeat(lambda _run_id: requested.is_set())
    supervisor.start(lease, heartbeat)
    try:
        requested.set()
        deadline = time.monotonic() + 2
        while not supervisor.cancellation_requested():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        supervisor.require_live()
        assert renewals == 0
    finally:
        supervisor.close()


def test_executor_heartbeat_recovers_from_temporary_unavailability() -> None:
    lease = _lease(heartbeat_interval=0.05)
    supervisor = runner_module._LeaseHeartbeat()
    recovered = Event()
    attempts = 0

    def heartbeat() -> ExecutorLease:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DaemonUnavailableError(
                "project database writer is busy",
                response=httpx2.Response(503),
            )
        recovered.set()
        return lease.model_copy(
            update={"expires_at": datetime.now(UTC) + timedelta(seconds=0.15)}
        )

    supervisor.start(lease, heartbeat)
    try:
        assert recovered.wait(timeout=5)
        supervisor.require_live()
        assert attempts == 2
    finally:
        supervisor.close()


def test_config_operations_reject_a_draft_from_a_different_active_snapshot() -> None:
    active_config = load_config()
    stale_config = active_config.model_copy(update={"id": "stale-config"})
    entry, activation = _config_registry_records(active_config)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.path == "/api/v1/config-registry/active"
        return _model(
            ActiveConfigView(
                entry=entry,
                activation=activation,
                config=active_config,
            )
        )

    draft = ConfigDraft(stale_config).replace_scalar(
        "drive_frequency",
        Quantity(value=5.1, unit="GHz"),
    )

    with pytest.raises(ValueError, match="no longer the active"):
        LabClient(_client(handler)).config.preview(draft)

    assert len(requests) == 1


def test_lab_client_owns_local_config_draft_workflow() -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    preview = _config_draft_preview(
        config=config,
        entry=entry,
        activation=activation,
        candidate_id="notebook-tuning",
    )
    publishes: list[ConfigPublishCommand] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/api/v1/config-registry":
            return _model(ConfigRegistryPage(entries=(entry,), activation=activation))
        if path == "/api/v1/config-registry/active" and request.method == "GET":
            return _model(
                ActiveConfigView(entry=entry, activation=activation, config=config)
            )
        if path == "/api/v1/config-registry/drafts/preview":
            return _model(preview)
        if path == "/api/v1/config-registry/publish-operations":
            command = ConfigPublishCommand.model_validate_json(request.content)
            publishes.append(command)
            return _model(
                _config_draft_default_receipt(command, preview, activation),
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    lab = LabClient(_client(handler), operator="notebook-operator")
    draft = lab.config.edit().replace_scalar(
        "drive_frequency",
        Quantity(value=5.1, unit="GHz"),
    )
    receipt = lab.config.set_default(
        draft,
        entry_id="notebook-tuning",
        note="typed notebook edit",
    )

    assert receipt.entry.id == "notebook-tuning"
    assert publishes[0].actor == "notebook-operator"
    source = publishes[0].source
    assert isinstance(source, ManualConfigDraftRevisionSource)
    assert source.expected_result_content_hash == preview.result_content_hash


def test_lab_config_intents_hide_registry_coordination() -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    seen: list[ConfigPublishCommand | ConfigEntryActivationCommand] = []
    published: ConfigPublishReceipt | None = None

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal published
        path = request.url.path
        if path == "/api/v1/config-registry":
            return _model(ConfigRegistryPage(entries=(entry,), activation=activation))
        if path == "/api/v1/config-registry/active" and request.method == "GET":
            if published is not None:
                return _model(
                    ActiveConfigView(
                        entry=published.entry,
                        activation=published.activation,
                        config=config,
                    )
                )
            return _model(
                ActiveConfigView(entry=entry, activation=activation, config=config)
            )
        if path == "/api/v1/config-registry/publish-operations":
            command = ConfigPublishCommand.model_validate_json(request.content)
            seen.append(command)
            published = _direct_config_publish_receipt(command, activation)
            return _model(published)
        if path == "/api/v1/config-registry/activations":
            assert published is not None
            return _model(
                ConfigActivationPage(items=(published.activation, activation))
            )
        if path == "/api/v1/config-registry/activation-operations":
            assert published is not None
            command = ConfigEntryActivationCommand.model_validate_json(request.content)
            seen.append(command)
            restored = ConfigRegistryActivationRecord(
                generation=published.activation.generation + 1,
                action="activation",
                entry_id=entry.id,
                entry_content_hash=entry.content_hash,
                previous_entry_id=published.entry.id,
                previous_entry_content_hash=published.entry.content_hash,
                actor=command.actor,
                note=command.note,
            )
            return _model(
                ConfigActivationReceipt(
                    operation=ConfigActivationOperation(
                        operation_id=command.operation_id,
                        intent_hash=command.intent_hash,
                        entry_id=command.entry_id,
                        expected_generation=command.expected_generation,
                        actor=command.actor,
                        note=command.note,
                        activation_generation=restored.generation,
                    ),
                    activation=restored,
                )
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    lab = LabClient(_client(handler), operator="notebook-operator")

    set_receipt = lab.config.set_default(config, note="use tuned values")
    undo_receipt = lab.config.undo(note="restore prior values")

    assert set_receipt.entry.id == config_revision_entry_id(config)
    assert undo_receipt.activation.entry_id == entry.id
    assert undo_receipt.activation.generation == activation.generation + 2
    assert seen == [
        ConfigPublishCommand(
            operation_id=cast("ConfigPublishCommand", seen[0]).operation_id,
            source=DirectConfigRevisionSource(config=config),
            entry_id=config_revision_entry_id(config),
            actor="notebook-operator",
            expected_generation=activation.generation,
            note="use tuned values",
        ),
        ConfigEntryActivationCommand(
            operation_id=cast("ConfigEntryActivationCommand", seen[1]).operation_id,
            entry_id=entry.id,
            actor="notebook-operator",
            expected_generation=activation.generation + 1,
            note="restore prior values",
        ),
    ]
    operation_id = cast("ConfigPublishCommand", seen[0]).operation_id
    assert operation_id.startswith("config-publish:")
    assert len(operation_id.removeprefix("config-publish:")) == 32
    activation_operation_id = cast("ConfigEntryActivationCommand", seen[1]).operation_id
    assert activation_operation_id.startswith("config-activation:")
    assert len(activation_operation_id.removeprefix("config-activation:")) == 32


def test_lab_config_undo_pages_to_the_previous_distinct_exact_entry() -> None:
    config = load_config()
    baseline_entry, baseline_activation = _config_registry_records(config)
    current_entry = baseline_entry.model_copy(
        update={
            "id": "current",
            "config_ref": "config-registry/entries/current/config.json",
        }
    )
    current_activation = ConfigRegistryActivationRecord(
        generation=102,
        action="activation",
        entry_id=current_entry.id,
        entry_content_hash=current_entry.content_hash,
        previous_entry_id=current_entry.id,
        previous_entry_content_hash=current_entry.content_hash,
        actor="operator",
    )
    older_current_activation = current_activation.model_copy(update={"generation": 3})
    baseline_activation = baseline_activation.model_copy(update={"generation": 2})
    seen: list[ConfigEntryActivationCommand] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/api/v1/config-registry/active":
            return _model(
                ActiveConfigView(
                    entry=current_entry,
                    activation=current_activation,
                    config=config,
                )
            )
        if path == "/api/v1/config-registry/activations":
            before = request.url.params.get("before")
            if before is None:
                return _model(
                    ConfigActivationPage(
                        items=(current_activation, older_current_activation),
                        next_cursor=3,
                    )
                )
            assert before == "3"
            return _model(ConfigActivationPage(items=(baseline_activation,)))
        if path == "/api/v1/config-registry/activation-operations":
            command = ConfigEntryActivationCommand.model_validate_json(request.content)
            seen.append(command)
            restored = ConfigRegistryActivationRecord(
                generation=103,
                action="activation",
                entry_id=baseline_entry.id,
                entry_content_hash=baseline_entry.content_hash,
                previous_entry_id=current_entry.id,
                previous_entry_content_hash=current_entry.content_hash,
                actor=command.actor,
                note=command.note,
            )
            return _model(
                ConfigActivationReceipt(
                    operation=ConfigActivationOperation(
                        operation_id=command.operation_id,
                        intent_hash=command.intent_hash,
                        entry_id=command.entry_id,
                        expected_generation=command.expected_generation,
                        actor=command.actor,
                        note=command.note,
                        activation_generation=restored.generation,
                    ),
                    activation=restored,
                )
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    lab = LabClient(_client(handler), operator="notebook-operator")
    receipt = lab.config.undo(operation_id="restore-older-baseline")

    assert receipt.activation.entry_id == baseline_entry.id
    assert seen == [
        ConfigEntryActivationCommand(
            operation_id="restore-older-baseline",
            entry_id=baseline_entry.id,
            actor="notebook-operator",
            expected_generation=current_activation.generation,
        )
    ]


def test_lab_config_undo_requires_a_previous_distinct_entry() -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    requests: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/v1/config-registry/active":
            return _model(
                ActiveConfigView(entry=entry, activation=activation, config=config)
            )
        if request.url.path == "/api/v1/config-registry/activations":
            return _model(ConfigActivationPage(items=(activation,)))
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    lab = LabClient(_client(handler), operator="notebook-operator")

    with pytest.raises(ValueError, match="no previous active entry"):
        lab.config.undo()
    assert requests == [
        ("GET", "/api/v1/config-registry/active"),
        ("GET", "/api/v1/config-registry/activations"),
    ]


def test_lab_config_activation_uses_explicit_operation_and_exact_lookup() -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    command = ConfigEntryActivationCommand(
        operation_id="activate-baseline",
        entry_id=entry.id,
        actor="notebook-operator",
        expected_generation=activation.generation,
        note="confirm baseline",
    )
    receipt = ConfigActivationReceipt(
        operation=ConfigActivationOperation(
            operation_id=command.operation_id,
            intent_hash=command.intent_hash,
            entry_id=command.entry_id,
            expected_generation=command.expected_generation,
            actor=command.actor,
            note=command.note,
            activation_generation=activation.generation,
        ),
        activation=activation,
    )
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == ("/api/v1/config-registry/activation-operations")
            assert (
                ConfigEntryActivationCommand.model_validate_json(request.content)
                == command
            )
            return _model(receipt)
        assert request.method == "GET"
        assert request.url.path == (
            "/api/v1/config-registry/activation-operations/activate-baseline"
        )
        return _model(receipt)

    lab = LabClient(_client(handler), operator="notebook-operator")

    activated = lab.config.activate_entry(
        entry.id,
        operation_id=command.operation_id,
        expected_generation=command.expected_generation,
        note=command.note,
    )
    reopened = lab.config.activation_operation(command.operation_id)

    assert activated == receipt
    assert reopened == receipt
    assert [request.method for request in requests] == ["POST", "GET"]


def test_lab_config_publish_uses_exact_command_and_lookup() -> None:
    config = load_config()
    _entry, activation = _config_registry_records(config)
    command = ConfigPublishCommand(
        operation_id="procedure:publish-baseline",
        source=DirectConfigRevisionSource(config=config),
        entry_id="published-baseline",
        actor="procedure-worker",
        expected_generation=activation.generation,
    )
    receipt = _direct_config_publish_receipt(command, activation)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/v1/config-registry/publish-operations"
            assert ConfigPublishCommand.model_validate_json(request.content) == command
        else:
            assert request.method == "GET"
            assert request.url.path == (
                "/api/v1/config-registry/publish-operations/procedure:publish-baseline"
            )
        return _model(receipt)

    lab = LabClient(_client(handler), operator="notebook-operator")

    published = lab.config.publish_config(command)
    reopened = lab.config.publish_operation(command.operation_id)

    assert published == receipt
    assert reopened == receipt
    assert [request.method for request in requests] == ["POST", "GET"]


def test_lab_candidate_accept_resolves_default_entry_before_publish() -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    draft = ConfigDraft(config).replace_scalar(
        "drive_frequency",
        Quantity(value=5.1, unit="GHz"),
    )
    proposal = parameter_change_proposal_from_updates(
        source_run_id="run-source",
        source_config=config,
        analysis_title="Fit",
        analysis_record_id="analysis-fit-r1",
        proposal_id="fit",
        updates=draft.updates,
        reason="fit",
        confidence=0.9,
    )
    candidate = CandidateConfig(parameter_proposal=proposal)
    resolved = resolve_candidate_config_from_snapshot(
        candidate,
        source_config=config,
    )
    commands: list[ConfigPublishCommand] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/api/v1/runs/run-source/config":
            return _model(
                RunConfigView(
                    run_id="run-source",
                    config_content_hash=config_content_hash(config),
                    config=config,
                )
            )
        if path == "/api/v1/config-registry":
            return _model(ConfigRegistryPage(entries=(entry,), activation=activation))
        assert path == "/api/v1/config-registry/publish-operations"
        command = ConfigPublishCommand.model_validate_json(request.content)
        commands.append(command)
        return _model(
            _candidate_config_publish_receipt(
                command,
                resolved,
                base_content_hash=config_content_hash(config),
                previous_activation=activation,
            )
        )

    lab = LabClient(_client(handler), operator="notebook-operator")

    receipt = lab.config.accept(candidate)

    [command] = commands
    assert command.entry_id == "candidate-fit-run-source"
    assert command.operation_id.startswith("config-publish:")
    assert receipt.entry.id == command.entry_id


def test_lab_config_inventory_migration_assembles_registry_coordination() -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    changes = (
        InstrumentInventoryRekey(
            instrument_id="source-0",
            from_exclusivity_key="source-0",
            to_exclusivity_key="rack-a/source",
        ),
    )
    migrated_entry = ConfigRegistryEntry(
        id="inventory-v2",
        config_ref="config-registry/entries/inventory-v2/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor="notebook-operator",
        note="move source",
        recorded_at=_NOW + timedelta(seconds=1),
    )
    receipt = InstrumentInventoryMigrationReceipt(
        entry=migrated_entry,
        activation=ConfigRegistryActivationRecord(
            generation=activation.generation + 1,
            action="inventory_migration",
            entry_id=migrated_entry.id,
            entry_content_hash=migrated_entry.content_hash,
            previous_entry_id=entry.id,
            previous_entry_content_hash=entry.content_hash,
            actor="notebook-operator",
            note="move source",
            recorded_at=_NOW + timedelta(seconds=1),
        ),
        changes=changes,
    )
    seen: list[InstrumentInventoryMigrationCommand] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/api/v1/config-registry":
            return _model(ConfigRegistryPage(entries=(entry,), activation=activation))
        if path == "/api/v1/config-registry/instrument-inventory-migrations":
            seen.append(
                InstrumentInventoryMigrationCommand.model_validate_json(request.content)
            )
            return _model(receipt)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    lab = LabClient(_client(handler), operator="notebook-operator")

    assert (
        lab.config.migrate_instrument_inventory(
            config,
            changes=changes,
            entry_id=migrated_entry.id,
            note="move source",
        )
        == receipt
    )
    assert seen == [
        InstrumentInventoryMigrationCommand(
            config=config,
            entry_id=migrated_entry.id,
            changes=changes,
            actor="notebook-operator",
            expected_generation=activation.generation,
            note="move source",
        )
    ]


def test_run_invocation_plans_against_explicit_snapshot_without_local_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    catalog = _instrument_catalog(config)
    system = ExperimentSystem(instrument_catalog=catalog)
    captured: dict[str, object] = {}

    def execute_run(
        self: _DaemonRunner,
        planned: PlannedRun,
        *,
        executor_id: str,
        submission_id: str | None = None,
    ) -> RunSnapshot:
        del self
        captured.update(
            planned=planned,
            executor_id=executor_id,
            submission_id=submission_id,
        )
        accepted = RunSnapshot(
            run_id="run-scratch",
            config_content_hash=planned.program.config_content_hash,
        )
        return _terminal_manifest(accepted)

    monkeypatch.setattr(_DaemonRunner, "execute", execute_run)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/instrument-contracts/resolve"
        assert (
            InstrumentContractCatalogRequest.model_validate_json(request.content).config
            == config
        )
        return _model(catalog)

    result = _DaemonRunner(
        _client(handler),
        lambda _config, _catalog: system,
    ).run(
        load_invocation(),
        config=config,
        name="scratch fit",
        tags=("calibration", "demo"),
        description="fit one trace",
        metadata={"sample": "q0"},
        operator="alice",
        executor_id="notebook-1",
        submission_id="scratch-submission",
    )

    planned = captured["planned"]
    assert isinstance(planned, PlannedRun)
    assert planned.config == config
    assert planned.request.operator == "alice"
    assert planned.request.display_name == "scratch fit"
    assert planned.request.tags == ("calibration", "demo")
    assert planned.request.description == "fit one trace"
    assert planned.request.metadata == {"sample": "q0"}
    assert captured["executor_id"] == "notebook-1"
    assert captured["submission_id"] == "scratch-submission"
    planned_system = planned.system
    assert planned_system is not None
    assert planned_system is system
    assert planned_system.instrument_catalog == catalog
    assert result.status == "completed"


def test_run_invocation_uses_active_config_and_bound_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    catalog = _instrument_catalog(config)
    system = ExperimentSystem(instrument_catalog=catalog)
    captured: dict[str, object] = {}
    built_from: list[tuple[ConfigProfileSnapshot, InstrumentContractCatalog]] = []

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        if http_request.url.path == "/api/v1/config-registry/active":
            return _model(
                ActiveConfigView(entry=entry, activation=activation, config=config)
            )
        assert http_request.url.path == "/api/v1/instrument-contracts/resolve"
        assert (
            InstrumentContractCatalogRequest.model_validate_json(
                http_request.content
            ).config
            == config
        )
        return _model(catalog)

    def execute_run(
        self: _DaemonRunner,
        planned: PlannedRun,
        *,
        executor_id: str,
        submission_id: str | None = None,
    ) -> RunSnapshot:
        del self, executor_id, submission_id
        captured["planned"] = planned
        return _terminal_manifest(
            RunSnapshot(
                run_id="run-scratch",
                config_content_hash=planned.program.config_content_hash,
            )
        )

    monkeypatch.setattr(_DaemonRunner, "execute", execute_run)

    def build_experiment_system(
        selected: ConfigProfileSnapshot,
        instrument_catalog: InstrumentContractCatalog,
    ) -> ExperimentSystem:
        built_from.append((selected, instrument_catalog))
        return system

    result = _DaemonRunner(
        _client(handler),
        build_experiment_system,
    ).run(load_invocation())

    planned = captured["planned"]
    assert isinstance(planned, PlannedRun)
    assert planned.config == config
    planned_system = planned.system
    assert planned_system is not None
    assert planned_system is system
    assert planned_system.instrument_catalog == catalog
    assert built_from == [(config, catalog)]
    assert result.status == "completed"


def test_run_invocation_uses_daemon_catalog_without_a_local_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    catalog = _instrument_catalog(config)
    captured: dict[str, object] = {}

    def execute_run(
        self: _DaemonRunner,
        planned: PlannedRun,
        *,
        executor_id: str,
        submission_id: str | None = None,
    ) -> RunSnapshot:
        del self, executor_id, submission_id
        captured["planned"] = planned
        return _terminal_manifest(
            RunSnapshot(
                run_id="run-scratch",
                config_content_hash=planned.program.config_content_hash,
            )
        )

    monkeypatch.setattr(_DaemonRunner, "execute", execute_run)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/instrument-contracts/resolve"
        assert (
            InstrumentContractCatalogRequest.model_validate_json(request.content).config
            == config
        )
        return _model(catalog)

    runner = _DaemonRunner(
        _client(handler),
        None,
    )

    result = runner.run(load_invocation(), config=config)

    planned = captured["planned"]
    assert isinstance(planned, PlannedRun)
    assert planned.system == ExperimentSystem(instrument_catalog=catalog)
    assert result.status == "completed"


def test_preview_invocation_uses_active_config_without_admission() -> None:
    config = load_config()
    entry, activation = _config_registry_records(config)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/v1/config-registry/active":
            return _model(
                ActiveConfigView(entry=entry, activation=activation, config=config)
            )
        assert request.url.path == "/api/v1/instrument-contracts/resolve"
        assert (
            InstrumentContractCatalogRequest.model_validate_json(request.content).config
            == config
        )
        return _model(_instrument_catalog(config))

    preview = _DaemonRunner(
        _client(handler),
        lambda _config, catalog: ExperimentSystem(instrument_catalog=catalog),
    ).preview(load_invocation())

    assert preview.point_count is not None
    assert preview.point_count > 0
    assert [request.url.path for request in requests] == [
        "/api/v1/config-registry/active",
        "/api/v1/instrument-contracts/resolve",
    ]


def _planned() -> PlannedRun:
    config = load_config()
    return plan_configured_experiment(
        load_invocation(),
        config=config,
        system=ExperimentSystem(
            instrument_catalog=_instrument_catalog(config),
        ),
    )


def _instrument_catalog(
    config: ConfigProfileSnapshot,
) -> InstrumentContractCatalog:
    provider = TestSignalInstrumentProvider()
    described = provider.describe(
        InstrumentProviderContext(bindings=instrument_bindings(config))
    )
    return InstrumentContractCatalog(
        config_content_hash=config_content_hash(config),
        provider_id=described.provider_id,
        instruments=described.instruments,
        problems=described.problems,
    )


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> DaemonClient:
    return DaemonClient(
        "http://daemon.local",
        transport=httpx2.MockTransport(handler),
    )


def _config_registry_records(
    config: ConfigProfileSnapshot,
) -> tuple[
    ConfigRegistryEntry,
    ConfigRegistryActivationRecord,
]:
    entry = ConfigRegistryEntry(
        id="baseline",
        config_ref="config-registry/entries/baseline/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor="notebook",
        recorded_at=_NOW,
    )
    activation = ConfigRegistryActivationRecord(
        generation=1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        actor="operator",
        recorded_at=_NOW,
    )
    return entry, activation


def _config_draft_preview(
    *,
    config: ConfigProfileSnapshot,
    entry: ConfigRegistryEntry,
    activation: ConfigRegistryActivationRecord,
    candidate_id: str,
) -> ConfigDraftPreview:
    check = (
        ConfigDraft(config)
        .replace_scalar(
            "drive_frequency",
            Quantity(value=5.1, unit="GHz"),
        )
        .check(candidate_id=candidate_id)
    )
    assert check.candidate is not None
    return ConfigDraftPreview(
        valid=True,
        base_entry=entry,
        base_generation=activation.generation,
        base_content_hash=entry.content_hash,
        config=check.candidate,
        result_content_hash=config_content_hash(check.candidate),
        deltas=check.deltas,
        problems=check.problems,
    )


def _config_draft_default_receipt(
    command: ConfigPublishCommand,
    preview: ConfigDraftPreview,
    previous_activation: ConfigRegistryActivationRecord,
) -> ConfigPublishReceipt:
    assert preview.result_content_hash is not None
    assert command.entry_id is not None
    source = command.source
    assert isinstance(source, ManualConfigDraftRevisionSource)
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref=f"config-registry/entries/{command.entry_id}/config.json",
        content_hash=preview.result_content_hash,
        source=ManualConfigDraftRegistrySource(
            base_entry_id=source.draft.base_entry_id,
            base_config_content_hash=source.draft.base_content_hash,
            base_registry_generation=source.draft.base_generation,
        ),
        actor=command.actor,
        note=command.note,
    )
    activation = ConfigRegistryActivationRecord(
        generation=previous_activation.generation + 1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        previous_entry_id=previous_activation.entry_id,
        previous_entry_content_hash=previous_activation.entry_content_hash,
        actor=command.actor,
        note=command.note,
        recorded_at=_NOW + timedelta(seconds=1),
    )
    return ConfigPublishReceipt(
        operation=ConfigPublishOperation(
            operation_id=command.operation_id,
            intent_hash=command.intent_hash,
            source_intent_hash=command.source_intent_hash,
            entry_id=command.entry_id,
            expected_generation=command.expected_generation,
            actor=command.actor,
            note=command.note,
            activation_generation=activation.generation,
        ),
        entry=entry,
        deltas=preview.deltas,
        activation=activation,
    )


def _direct_config_publish_receipt(
    command: ConfigPublishCommand,
    previous_activation: ConfigRegistryActivationRecord,
) -> ConfigPublishReceipt:
    source = command.source
    assert isinstance(source, DirectConfigRevisionSource)
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref=f"config-registry/entries/{command.entry_id}/config.json",
        content_hash=config_content_hash(source.config),
        source=DirectConfigRegistrySource(),
        actor=command.actor,
        note=command.note,
    )
    activation = ConfigRegistryActivationRecord(
        generation=previous_activation.generation + 1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        previous_entry_id=previous_activation.entry_id,
        previous_entry_content_hash=previous_activation.entry_content_hash,
        actor=command.actor,
        note=command.note,
        recorded_at=_NOW + timedelta(seconds=1),
    )
    return ConfigPublishReceipt(
        operation=ConfigPublishOperation(
            operation_id=command.operation_id,
            intent_hash=command.intent_hash,
            source_intent_hash=command.source_intent_hash,
            entry_id=command.entry_id,
            expected_generation=command.expected_generation,
            actor=command.actor,
            note=command.note,
            activation_generation=activation.generation,
        ),
        entry=entry,
        activation=activation,
    )


def _candidate_config_publish_receipt(
    command: ConfigPublishCommand,
    config: ConfigProfileSnapshot,
    *,
    base_content_hash: ConfigContentHash,
    previous_activation: ConfigRegistryActivationRecord,
) -> ConfigPublishReceipt:
    source = command.source
    assert isinstance(source, CandidateConfigRevisionSource)
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref=f"config-registry/entries/{command.entry_id}/config.json",
        content_hash=config_content_hash(config),
        source=CandidateConfigRegistrySource(
            run_id=source.run_id,
            proposal_id=source.proposal_id,
            base_config_content_hash=base_content_hash,
            acceptance=source.acceptance,
        ),
        actor=command.actor,
        note=command.note,
    )
    activation = ConfigRegistryActivationRecord(
        generation=previous_activation.generation + 1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        previous_entry_id=previous_activation.entry_id,
        previous_entry_content_hash=previous_activation.entry_content_hash,
        actor=command.actor,
        note=command.note,
        recorded_at=_NOW + timedelta(seconds=1),
    )
    return ConfigPublishReceipt(
        operation=ConfigPublishOperation(
            operation_id=command.operation_id,
            intent_hash=command.intent_hash,
            source_intent_hash=command.source_intent_hash,
            entry_id=command.entry_id,
            expected_generation=command.expected_generation,
            actor=command.actor,
            note=command.note,
            activation_generation=activation.generation,
        ),
        entry=entry,
        activation=activation,
    )


def _admission(submission: RunSubmission) -> RunAdmission:
    return RunAdmission(
        submission_id=submission.submission_id,
        snapshot=RunSnapshot(
            run_id="run-1",
            created_at=_NOW,
            config_content_hash=config_content_hash(submission.config),
            config_source=submission.config_source,
        ),
    )


def _lease(*, heartbeat_interval: float) -> ExecutorLease:
    issued_at = datetime.now(UTC)
    return ExecutorLease(
        lease_id="lease-1",
        segment_id="segment-1",
        run_id="run-1",
        executor_id="notebook-1",
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=heartbeat_interval * 3),
        heartbeat_interval_seconds=heartbeat_interval,
    )


def _provisioning_receipt(
    program: RunProgram,
    request: httpx2.Request,
) -> RunInstrumentProvisionReceipt:
    command = RunInstrumentProvisionCommand.model_validate_json(request.content)
    host = program.host
    instrument_ids = () if host is None else host.resource_order
    return RunInstrumentProvisionReceipt(
        run_id="run-1",
        operation_id=command.operation_id,
        status="ready",
        instrument_ids=instrument_ids,
        observed_state=tuple(
            InstrumentStateSnapshot(instrument_id=instrument_id)
            for instrument_id in instrument_ids
        ),
        baseline_state=tuple(
            InstrumentStateSnapshot(instrument_id=instrument_id)
            for instrument_id in instrument_ids
        ),
    )


def _terminal_manifest(accepted: RunSnapshot) -> RunSnapshot:
    outcome = RunOutcome(
        run_id=accepted.run_id,
        result="succeeded",
        certainty="known",
    )
    return accepted.model_copy(
        update={
            "outcome": outcome,
        }
    )


def _model(model: BaseModel, *, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, json=model.model_dump(mode="json"))


def test_closed_session_guides_lazy_reads_without_http() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={})

    client = DaemonClient("http://daemon.local", transport=httpx2.MockTransport(handle))
    lab = LabClient(client)
    run = RunHandle(session=lab, id="retained-run")
    client.close()
    assert lab.is_closed
    with pytest.raises(SessionClosedError, match=r"lab\.get_run\(run_id\)") as raised:
        _ = run.status
    assert "run.snapshot before closing" in str(raised.value)
    assert not requests


def test_lab_close_preserves_connection_ownership() -> None:
    with DaemonClient("http://daemon.local") as supplied:
        lab = LabClient(supplied)
        lab.close()
        lab.close()
        assert not supplied.is_closed
        assert not lab.is_closed
    assert lab.is_closed

    for _ in range(2):
        with LabClient("http://daemon.local") as owned:
            assert not owned.is_closed
        assert owned.is_closed
        owned.close()
