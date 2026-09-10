# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Barrier
from typing import cast

import httpx2
import numpy as np
import pyarrow as pa
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from scopecat.analysis.datasets import DerivedDataset, derived_dataset
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.analysis.service import MeasurementAnalysisInput
from scopecat.api.analysis import Analysis, AnalysisContext, analysis_step
from scopecat.api.lab import LabClient
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    CalibrationCohortCreateCommand,
    CalibrationCohortMember,
    CalibrationCohortMemberSpec,
    CalibrationCohortSpec,
    CalibrationConfigSourceRef,
    CalibrationDefinitionRef,
    CalibrationForcedDueReason,
    CalibrationPublicationAttentionCommand,
    CalibrationPublicationDeferCommand,
    CalibrationPublicationGetQuery,
    CalibrationPublicationPolicyRef,
    CalibrationPublicationReadyQuery,
    CalibrationStatusQuery,
    CalibrationSuccessRef,
    CalibrationTargetRef,
    ProcedureCloseCommand,
    ProcedureDefinitionRef,
    ProcedureStepBeginCommand,
    ProcedureStepCompleteCommand,
    ProcedureWorkerLeaseAcquireCommand,
    RunOutputRef,
    calibration_freshness_fingerprint,
    calibration_key,
)
from scopecat.config.candidate_merges import merge_common_base_parameter_proposals
from scopecat.config.changes import parameter_change_proposal_from_updates
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.parameters import replace_scalar_parameter
from scopecat.config.registry import (
    CalibrationCohortMergeContribution,
    CalibrationCohortMergeRegistrySource,
    CandidateConfigRegistrySource,
    ConfigCompositionEvidenceStepRef,
    ConfigCompositionPolicyRef,
    CrossRunCandidateAcceptance,
    VerifiedParameterProposalProofV1,
)
from scopecat.control.models import (
    DurableEvent,
    DurableEventInput,
    EventPage,
    RunPlanSummary,
    RunResourceRequirement,
)
from scopecat.daemon.client import (
    DaemonClient,
    DaemonConflictError,
    DaemonNotFoundError,
)
from scopecat.daemon.wire import (
    AnalysisParameterProposalOutputPayload,
    AnalysisSaveCommand,
    CalibrationCohortMergeRevisionSource,
    CalibrationPublicationCommand,
    CalibrationPublicationReceipt,
    CandidateConfigRevisionSource,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    DirectConfigRevisionSource,
    ExecutorStartRequest,
    MeasurementFlushCommand,
    MeasurementHeaderCommand,
    MeasurementSealCommand,
    RunCoverageAdvanceCommand,
    RunSubmission,
    SampleCreateCommand,
    TerminalRunCommitCommand,
)
from scopecat.execution.evidence import build_terminal_contents
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.measurements.dataset import Dataset
from scopecat.measurements.recording_arrow import (
    encode_measurement_append,
)
from scopecat.records.analysis import (
    AnalysisRecord,
    MeasurementAnalysisRecordInput,
    ProjectAnalysisDecisionReference,
    ProjectAnalysisSubject,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
    SampleAnalysisSubject,
)
from scopecat.records.config import (
    ConfigProfileSnapshot,
)
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityIndex,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
    MeasurementDatasetSeal,
    measurement_dataset_content_hash,
    measurement_fragment_content_hash,
)
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
)
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleRevisionDraft, SampleSelector

from scopecat_server import BackendConflict, BackendNotFound, LocalDaemonRuntime
from scopecat_server.storage.sqlite.calibration_cohorts import (
    CalibrationCohortConflict,
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.control_plane import (
    SQLiteControlPlane,
)

_FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "core"
    / "simple_scan"
    / "config-snapshot.json"
)


@dataclass(frozen=True, slots=True)
class _CandidateDecision:
    accepted: bool


_CANDIDATE_DECISION_SCHEMA = AnalysisFactSchema(
    "tests.candidate-decision.v1",
    _CandidateDecision,
)


def _config() -> ConfigProfileSnapshot:
    return load_config_snapshot_document(_FIXTURE)


def _events(
    runtime: LocalDaemonRuntime,
    *,
    run_id: str | None = None,
) -> EventPage:
    return runtime.application.runs.list_events(
        limit=500,
        after=None,
        run_id=run_id,
    )


def _submission(
    submission_id: str = "submission-1",
    *,
    point_count: int = 1,
) -> RunSubmission:
    return RunSubmission(
        submission_id=submission_id,
        config=_config(),
        request=RunRequest(experiment_id="scratch"),
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=point_count,
            initial_point_count=point_count,
            point_limit=point_count,
            run_resource_requirements=(
                RunResourceRequirement(id="source-0", kind="instrument"),
            ),
        ),
    )


def _daemon_client(transport: TestClient) -> DaemonClient:
    def send(request: httpx2.Request) -> httpx2.Response:
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )


def _complete_signal_run(
    runtime: LocalDaemonRuntime,
    *,
    submission_id: str,
    signal: float | tuple[float, ...],
    entities: tuple[EntityRef, ...] | None = None,
    submission: RunSubmission | None = None,
) -> str:
    admission = runtime.application.submit_run(submission or _submission(submission_id))
    run_id = admission.run_id
    lease = runtime.application.executor.start_executor(
        run_id,
        ExecutorStartRequest(executor_id=f"executor-{submission_id}"),
    )
    header = MeasurementDatasetHeader(
        run_id=run_id,
        recording_contract_fingerprint="test.project-analysis.v1",
        dataset_schema=MeasurementDatasetSchema(
            dataset_id="raw-measurements",
            point_domain=MeasurementProductGridPointDomain(axes=[]),
            dimensions=[
                MeasurementDimension(id="point", kind="point", size=1),
                *(
                    [
                        MeasurementDimension(
                            id="entity",
                            kind="entity",
                            size=len(entities),
                            index=MeasurementEntityIndex(values=entities),
                        )
                    ]
                    if entities is not None
                    else []
                ),
            ],
            variables=[
                MeasurementVariable(
                    id="signal",
                    role="observable",
                    dtype="float64",
                    unit="ratio",
                    dims=["point"] if entities is None else ["point", "entity"],
                )
            ],
        ),
        expected_record_count=1,
        record_count_limit=1,
    )
    record = MeasurementRecord(
        run_id=run_id,
        logical_point_id="point-0",
        point_index=0,
        coordinates={},
        observables={
            "signal": MeasurementScalar.create(
                dtype="float64",
                value=cast("float", signal),
                unit="ratio",
            )
            if entities is None
            else MeasurementArray.create(
                values=np.asarray(signal, dtype=np.float64), unit="ratio"
            )
        },
    )
    append = MeasurementDatasetAppend(
        run_id=run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=(record,),
    )
    runtime.application.executor.initialize_measurements(
        run_id,
        MeasurementHeaderCommand(lease_id=lease.lease_id, header=header),
    )
    runtime.application.executor.ingest_measurements(
        run_id,
        lease_id=lease.lease_id,
        content=encode_measurement_append(append, header.dataset_schema),
    )
    runtime.application.executor.flush_measurements(
        run_id,
        MeasurementFlushCommand(lease_id=lease.lease_id),
    )
    runtime.application.executor.advance_run_coverage(
        run_id,
        RunCoverageAdvanceCommand(
            lease_id=lease.lease_id,
            start_index=0,
            point_count=1,
        ),
    )
    runtime.application.executor.seal_measurements(
        run_id,
        MeasurementSealCommand(
            lease_id=lease.lease_id,
            seal=MeasurementDatasetSeal(
                run_id=run_id,
                header_content_hash=header.content_hash,
                record_count=1,
                fragment_record_count=1,
                fragment_content_hash=measurement_fragment_content_hash(
                    header_content_hash=header.content_hash,
                    record_content_hashes=append.record_content_hashes,
                ),
            ),
        ),
    )
    outcome = RunOutcome(
        run_id=run_id,
        result="succeeded",
        certainty="known",
    )
    runtime.application.executor.commit_terminal(
        run_id,
        TerminalRunCommitCommand(
            lease_id=lease.lease_id,
            outcome=outcome,
            contents=build_terminal_contents(
                outcome=outcome,
                measurement_count=1,
                dataset_content_hash=measurement_dataset_content_hash(
                    header_content_hash=header.content_hash,
                    record_content_hashes=append.record_content_hashes,
                ),
                dataset_schema=header.dataset_schema,
                expected_record_count=1,
                instrument_state=None,
            ),
        ),
    )
    return run_id


def _analysis_proposal(run_id: str) -> ParameterChangeProposal:
    return parameter_change_proposal_from_updates(
        source_run_id=run_id,
        source_config=_config(),
        analysis_title="fit",
        analysis_record_id="analysis-fit-r1",
        proposal_id="drive-frequency",
        updates=(
            replace_scalar_parameter(
                "drive_frequency",
                Quantity(value=5.1, unit="GHz"),
            ),
        ),
        reason="fit converged",
        confidence=0.9,
    )


def _analysis_command(proposal: ParameterChangeProposal) -> AnalysisSaveCommand:
    return AnalysisSaveCommand(
        title="fit",
        analysis_key="fit",
        outputs=(
            AnalysisParameterProposalOutputPayload(
                kind="parameter_change_proposal",
                id=proposal.id,
                title=proposal.id,
                content=proposal,
            ),
        ),
    )


def test_project_analysis_compares_completed_runs_and_reloads_outputs(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        baseline_id = _complete_signal_run(
            runtime,
            submission_id="comparison-baseline",
            signal=0.8,
        )
        candidate_id = _complete_signal_run(
            runtime,
            submission_id="comparison-candidate",
            signal=1.1,
        )
        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            baseline = lab.get_run(baseline_id)
            candidate = lab.get_run(candidate_id)

            @analysis_step(id="candidate-verification")
            def candidate_verification(
                context: AnalysisContext,
                *,
                report: str,
            ) -> Analysis:
                baseline_data = context.measurements(
                    baseline,
                    id="baseline",
                    role="baseline",
                )
                candidate_data = context.measurements(
                    candidate,
                    id="candidate",
                    role="candidate",
                )
                baseline_peak = cast(
                    "float", baseline_data.data_vars["signal"].values[0]
                )
                candidate_peak = cast(
                    "float", candidate_data.data_vars["signal"].values[0]
                )
                return (
                    context.result("Candidate verification")
                    .dataset(
                        "comparison",
                        pa.table(
                            {
                                "role": ["baseline", "candidate"],
                                "run_id": [baseline.id, candidate.id],
                                "peak_signal": [baseline_peak, candidate_peak],
                            }
                        ),
                    )
                    .fact("candidate-improved", candidate_peak >= baseline_peak)
                    .artifact(
                        "report",
                        text=report,
                        filename="candidate-verification.md",
                        media_type="text/markdown",
                    )
                )

            def publish(report: str) -> PublishedAnalysis:
                return lab.analyze(candidate_verification(report=report))

            first = publish("# Candidate verification\n\nInitial comparison.\n")
            retry = publish("# Candidate verification\n\nInitial comparison.\n")
            revised = publish("# Candidate verification\n\nReviewed comparison.\n")

            assert first.id == "analysis-candidate-verification-r1"
            assert retry.id == first.id
            assert revised.id == "analysis-candidate-verification-r2"
            assert retry.published_at == first.published_at
            assert revised.published_at >= first.published_at
            assert revised.revision == 2
            assert revised.view.analysis.subject.kind == "project"
            measurement_inputs = tuple(
                item
                for item in revised.inputs
                if isinstance(item, MeasurementAnalysisRecordInput)
            )
            assert len(measurement_inputs) == len(revised.inputs)
            assert [
                (item.id, item.run_id, item.role) for item in measurement_inputs
            ] == [
                ("baseline", baseline.id, "baseline"),
                ("candidate", candidate.id, "candidate"),
            ]
            assert (
                revised.inputs[0].content_hash
                == baseline.measurements().entry.content_hash
            )
            assert (
                revised.inputs[1].content_hash
                == candidate.measurements().entry.content_hash
            )
            assert revised.fact("candidate-improved").value is True
            assert revised.dataset("comparison").table.to_pylist() == [
                {
                    "role": "baseline",
                    "run_id": baseline.id,
                    "peak_signal": 0.8,
                },
                {
                    "role": "candidate",
                    "run_id": candidate.id,
                    "peak_signal": 1.1,
                },
            ]
            assert revised.artifact("report").text().endswith("Reviewed comparison.\n")
            assert lab.published_analysis("candidate-verification").id == revised.id
            published_page = lab.analysis_summaries(limit=1)
            assert [item.entry.id for item in published_page.items] == [revised.id]
            assert published_page.items[0].published_at == revised.published_at
            assert published_page.next_cursor is not None
            older_published_page = lab.analysis_summaries(
                limit=1,
                before=published_page.next_cursor,
            )
            assert [item.entry.id for item in older_published_page.items] == [first.id]
            assert older_published_page.next_cursor is None
            summary_page = runtime.application.analyses.list(limit=1)
            assert [
                (
                    summary.entry.id,
                    summary.input_count,
                    summary.output_count,
                )
                for summary in summary_page.items
            ] == [(revised.id, 2, 3)]
            assert summary_page.next_cursor is not None
            assert [
                summary.entry.id
                for summary in runtime.application.analyses.list(
                    limit=1,
                    before=summary_page.next_cursor,
                ).items
            ] == [first.id]
            assert not any(
                entry.kind == "analysis"
                for entry in baseline.contents(role="record", kind="analysis").items
            )
            assert not any(
                entry.kind == "analysis"
                for entry in candidate.contents(role="record", kind="analysis").items
            )
            content_page = transport.get(
                f"/api/v1/analyses/{revised.id}/contents",
                params={"limit": 1},
            ).json()
            assert [entry["id"] for entry in content_page["items"]] == [revised.id]
            assert content_page["next_cursor"] is not None
            artifact_id = f"{revised.id}-report"
            exact_content = transport.get(
                f"/api/v1/analyses/{revised.id}/contents/{artifact_id}"
            ).json()
            assert exact_content["id"] == artifact_id
            assert exact_content["role"] == "artifact"
            record_bytes = _daemon_client(transport).project_analysis_content_bytes(
                revised.id,
                revised.id,
            )
            assert record_bytes.entry.role == "record"
            assert (
                AnalysisRecord.model_validate_json(
                    record_bytes.content_bytes()
                ).revision
                == 2
            )
            project_analysis_events = [
                event
                for event in _events(runtime).items
                if event.kind == "project_analysis_saved"
            ]
            assert [event.run_id for event in project_analysis_events] == [None, None]
            assert [
                event.payload["record_id"] for event in project_analysis_events
            ] == [first.id, revised.id]
            assert [event.payload["revision"] for event in project_analysis_events] == [
                1,
                2,
            ]
            assert [
                event.payload["publication_hash"] for event in project_analysis_events
            ] == [
                first.view.analysis.publication_hash,
                revised.view.analysis.publication_hash,
            ]
            assert [
                event.payload["input_run_ids"] for event in project_analysis_events
            ] == [sorted([baseline.id, candidate.id])] * 2

            missing_analysis = transport.get("/api/v1/analyses/missing")
            missing_content = transport.get(
                f"/api/v1/analyses/{revised.id}/contents/missing/bytes"
            )
            assert missing_analysis.status_code == 404
            assert missing_content.status_code == 404

    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
    ):
        restored = LabClient(_daemon_client(transport)).published_analysis(
            "candidate-verification"
        )
        assert restored.id == "analysis-candidate-verification-r2"
        assert restored.artifact("report").text().endswith("Reviewed comparison.\n")


def test_sample_analysis_is_scoped_to_runs_bound_to_that_sample(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        runtime.application.samples.create(
            SampleCreateCommand(
                operation_id="create:die-1",
                sample_id="die-1",
                kind="die",
                actor="operator",
                content=SampleRevisionDraft(display_name="Die 1"),
            )
        )
        runtime.application.samples.create(
            SampleCreateCommand(
                operation_id="create:reference-1",
                sample_id="reference-1",
                kind="reference",
                actor="operator",
                content=SampleRevisionDraft(display_name="Reference 1"),
            )
        )
        submission = _submission("sample-analysis-run").model_copy(
            update={
                "request": RunRequest(
                    experiment_id="scratch",
                    samples=(
                        SampleSelector(sample_id="die-1"),
                        SampleSelector(role="reference", sample_id="reference-1"),
                    ),
                )
            }
        )
        sample_run_id = _complete_signal_run(
            runtime,
            submission_id="sample-analysis-run",
            signal=0.9,
            submission=submission,
        )
        other_run_id = _complete_signal_run(
            runtime,
            submission_id="other-analysis-run",
            signal=1.2,
        )
        reference_run_id = _complete_signal_run(
            runtime,
            submission_id="reference-analysis-run",
            signal=1.1,
            submission=_submission("reference-analysis-run").model_copy(
                update={
                    "request": RunRequest(
                        experiment_id="scratch",
                        samples=(SampleSelector(sample_id="reference-1"),),
                    )
                }
            ),
        )

        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            sample_run = lab.get_run(sample_run_id)
            other_run = lab.get_run(other_run_id)
            reference_run = lab.get_run(reference_run_id)

            @analysis_step(id="sample-health")
            def sample_health(context: AnalysisContext) -> Analysis:
                data = context.measurements(sample_run, id="signal")
                return context.result("Sample health").fact(
                    "signal",
                    cast("float", data.data_vars["signal"].values[0]),
                )

            published = lab.analyze(sample_health(), sample="die-1")

            assert isinstance(published.view.analysis.subject, SampleAnalysisSubject)
            assert published.view.analysis.subject.sample_id == "die-1"
            assert published.id.startswith("analysis-sample-")
            assert lab.analysis_summaries().items == ()
            sample_page = lab.analysis_summaries(sample="die-1")
            assert [item.entry.id for item in sample_page.items] == [published.id]
            assert (
                lab.published_analysis("sample-health", sample="die-1").id
                == published.id
            )

            @analysis_step(id="reference-health")
            def reference_health(context: AnalysisContext) -> Analysis:
                context.measurements(sample_run, id="signal")
                return context.result("Reference health").fact("valid", True)

            with pytest.raises(
                DaemonConflictError,
                match="bound as its subject",
            ):
                lab.analyze(reference_health(), sample="reference-1")

            @analysis_step(id="sample-health")
            def reference_sample_health(context: AnalysisContext) -> Analysis:
                data = context.measurements(reference_run, id="signal")
                return context.result("Reference sample health").fact(
                    "signal",
                    cast("float", data.data_vars["signal"].values[0]),
                )

            reference_published = lab.analyze(
                reference_sample_health(),
                sample="reference-1",
            )
            assert reference_published.id != published.id
            assert (
                lab.published_analysis("sample-health", sample="reference-1").id
                == reference_published.id
            )

            @analysis_step(id="mixed-sample-health")
            def mixed_sample_health(context: AnalysisContext) -> Analysis:
                context.measurements(sample_run, id="sample")
                context.measurements(other_run, id="other")
                return context.result("Mixed sample health").fact("valid", False)

            with pytest.raises(
                DaemonConflictError,
                match="bound as its subject",
            ):
                lab.analyze(mixed_sample_health(), sample="die-1")

            record_id = published.id

    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
    ):
        restored = LabClient(_daemon_client(transport)).published_analysis(
            record_id,
            sample="die-1",
        )
        assert isinstance(restored.view.analysis.subject, SampleAnalysisSubject)
        assert restored.view.analysis.subject.sample_id == "die-1"


def test_project_analysis_allocates_distinct_revisions_for_concurrent_saves(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        run_id = _complete_signal_run(
            runtime,
            submission_id="concurrent-project-analysis",
            signal=0.8,
        )
        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            run = lab.get_run(run_id)
            pending = []
            for verdict in ("first", "second"):
                context = lab.analysis(
                    "Concurrent comparison",
                    key="concurrent-comparison",
                )
                context.measurements(run, id="measurement")
                pending.append(context.result().fact("verdict", verdict))

            ready = Barrier(len(pending))

            def save(result: Analysis) -> PublishedAnalysis:
                ready.wait()
                return result.save()

            with ThreadPoolExecutor(max_workers=len(pending)) as executor:
                saved = tuple(executor.map(save, pending))

            assert {item.id for item in saved} == {
                "analysis-concurrent-comparison-r1",
                "analysis-concurrent-comparison-r2",
            }
            assert {item.revision for item in saved} == {1, 2}
            assert {cast("str", item.fact("verdict").value) for item in saved} == {
                "first",
                "second",
            }
            assert len(runtime.application.analyses.list().items) == 2
            assert (
                len(
                    [
                        event
                        for event in _events(runtime).items
                        if event.kind == "project_analysis_saved"
                    ]
                )
                == 2
            )


def test_project_analysis_consumes_project_datasets_facts_and_artifacts(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        run_id = _complete_signal_run(
            runtime,
            submission_id="project-analysis-inputs",
            signal=0.8,
        )
        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            source_context = lab.analysis("Project source", key="project-source")
            source_context.measurements(
                lab.get_run(run_id),
                id="measurements",
            )
            source = (
                source_context.result()
                .dataset("summary", pa.table({"signal": [0.8]}))
                .fact("accepted", True)
                .artifact(
                    "report",
                    text="project source report",
                    filename="source.md",
                    media_type="text/markdown",
                )
                .save()
            )

            consumer_context = lab.analysis(
                "Project consumer",
                key="project-consumer",
            )
            summary = consumer_context.analysis_dataset("project-source", "summary")
            accepted = consumer_context.analysis_fact("project-source", "accepted")
            report = consumer_context.analysis_artifact("project-source", "report")
            consumer = (
                consumer_context.result()
                .fact(
                    "consumed",
                    bool(accepted.value)
                    and summary.table.num_rows == 1
                    and bool(report.text()),
                )
                .save()
            )

            assert consumer.fact("consumed").value is True
            assert {input_ref.kind for input_ref in consumer.inputs} == {
                "analysis_dataset",
                "analysis_fact",
                "analysis_artifact",
            }
            published_inputs = tuple(
                input_ref
                for input_ref in consumer.inputs
                if isinstance(input_ref, PublishedAnalysisRecordInput)
            )
            assert len(published_inputs) == len(consumer.inputs)
            assert all(
                input_ref.source.subject.kind == "project"
                and input_ref.source.analysis_record_id == source.id
                for input_ref in published_inputs
            )
            [consumer_event] = [
                event
                for event in _events(runtime).items
                if event.kind == "project_analysis_saved"
                and event.payload["record_id"] == consumer.id
            ]
            assert consumer_event.payload["input_run_ids"] == [run_id]


def test_project_analysis_publication_rolls_back_index_and_event_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        run_id = _complete_signal_run(
            runtime,
            submission_id="project-analysis-atomic",
            signal=0.8,
        )
        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            context = lab.analysis("Atomic comparison", key="atomic-comparison")
            context.measurements(
                lab.get_run(run_id),
                id="baseline",
                role="baseline",
            )
            result = context.result().fact("decision", True)
            append_event = SQLiteControlPlane.append_event_in_transaction

            def fail_project_analysis_event(
                control: SQLiteControlPlane,
                connection: sqlite3.Connection,
                event: DurableEventInput,
            ) -> DurableEvent:
                if event.kind == "project_analysis_saved":
                    raise RuntimeError("project analysis event publication failed")
                return append_event(control, connection, event)

            with monkeypatch.context() as patch:
                patch.setattr(
                    SQLiteControlPlane,
                    "append_event_in_transaction",
                    fail_project_analysis_event,
                )
                with pytest.raises(
                    RuntimeError,
                    match="project analysis event publication failed",
                ):
                    result.save()

            assert runtime.application.analyses.list().items == ()
            assert [
                event.kind
                for event in _events(runtime).items
                if event.kind == "project_analysis_saved"
            ] == []

            saved = result.save()

            assert saved.id == "analysis-atomic-comparison-r1"
            assert [
                event.kind
                for event in _events(runtime).items
                if event.kind == "project_analysis_saved"
            ] == ["project_analysis_saved"]


def test_candidate_acceptance_requires_matching_cross_run_verification(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        baseline_id = _complete_signal_run(
            runtime,
            submission_id="verified-baseline",
            signal=0.8,
        )
        unrelated_id = _complete_signal_run(
            runtime,
            submission_id="verified-unrelated",
            signal=1.0,
        )
        proposal = _analysis_proposal(baseline_id)
        runtime.application.runs.save_run_analysis(
            baseline_id,
            _analysis_command(proposal),
        )

        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            baseline = lab.get_run(baseline_id)
            proposal_analysis = baseline.published_analysis("fit")
            candidate = proposal_analysis.candidate_config()
            candidate_config, candidate_source = lab.config.resolve_with_source(
                candidate
            )
            assert candidate_source is not None
            candidate_submission = _submission("verified-candidate").model_copy(
                update={
                    "config": candidate_config,
                    "config_source": candidate_source,
                }
            )
            candidate_id = _complete_signal_run(
                runtime,
                submission_id="verified-candidate",
                signal=1.1,
                submission=candidate_submission,
            )

            invalid_context = lab.analysis(
                "Unrelated comparison",
                key="unrelated-comparison",
            )
            invalid_context.measurements(baseline, id="baseline", role="baseline")
            invalid_context.measurements(
                lab.get_run(unrelated_id),
                id="candidate",
                role="candidate",
            )
            invalid_verification = (
                invalid_context.result()
                .fact(
                    "decision",
                    _CandidateDecision(accepted=True),
                    schema=_CANDIDATE_DECISION_SCHEMA,
                )
                .save()
            )
            invalid_decision = invalid_verification.fact("decision")
            invalid_acceptance = CrossRunCandidateAcceptance(
                decision=ProjectAnalysisDecisionReference(
                    analysis_record_id=invalid_verification.id,
                    output_id="decision",
                    schema_id=invalid_decision.schema_id,
                    schema_hash=invalid_decision.schema_hash,
                )
            )
            with pytest.raises(
                BackendConflict,
                match="independent successful run",
            ):
                runtime.application.config.publish_config(
                    ConfigPublishCommand(
                        operation_id="publish:invalid-verified-candidate",
                        source=CandidateConfigRevisionSource(
                            run_id=baseline_id,
                            proposal_id=proposal.id,
                            acceptance=invalid_acceptance,
                        ),
                        entry_id="invalid-verified-candidate",
                        actor="nightly-calibration",
                        expected_generation=1,
                    )
                )

            valid_context = lab.analysis(
                "Candidate comparison",
                key="candidate-comparison",
            )
            valid_context.measurements(baseline, id="baseline", role="baseline")
            valid_context.measurements(
                lab.get_run(candidate_id),
                id="candidate",
                role="candidate",
            )
            verification = (
                valid_context.result()
                .fact(
                    "decision",
                    _CandidateDecision(accepted=True),
                    schema=_CANDIDATE_DECISION_SCHEMA,
                )
                .save()
            )
            verification_decision = verification.fact("decision")
            with pytest.raises(
                BackendConflict,
                match="must identify an exact project analysis",
            ):
                runtime.application.config.publish_config(
                    ConfigPublishCommand(
                        operation_id="publish:logical-key-verification",
                        source=CandidateConfigRevisionSource(
                            run_id=baseline_id,
                            proposal_id=proposal.id,
                            acceptance=CrossRunCandidateAcceptance(
                                decision=ProjectAnalysisDecisionReference(
                                    analysis_record_id="candidate-comparison",
                                    output_id="decision",
                                    schema_id=verification_decision.schema_id,
                                    schema_hash=verification_decision.schema_hash,
                                )
                            ),
                        ),
                        entry_id="logical-key-verification",
                        actor="nightly-calibration",
                        expected_generation=1,
                    )
                )
            accepted = lab.config.accept_verified(
                proposal_analysis,
                verified_by=(verification, "decision"),
                entry_id="verified-candidate-config",
            )

            assert isinstance(accepted.entry.source, CandidateConfigRegistrySource)
            assert accepted.entry.source.acceptance == (
                CrossRunCandidateAcceptance(
                    decision=ProjectAnalysisDecisionReference(
                        analysis_record_id=verification.id,
                        output_id="decision",
                        schema_id=_CANDIDATE_DECISION_SCHEMA.id,
                        schema_hash=_CANDIDATE_DECISION_SCHEMA.schema_hash,
                    )
                )
            )

            rejected_context = lab.analysis(
                "Rejected candidate comparison",
                key="rejected-candidate-comparison",
            )
            rejected_context.measurements(baseline, id="baseline", role="baseline")
            rejected_context.measurements(
                lab.get_run(candidate_id),
                id="candidate",
                role="candidate",
            )
            rejected_verification = (
                rejected_context.result()
                .fact(
                    "decision",
                    _CandidateDecision(accepted=False),
                    schema=_CANDIDATE_DECISION_SCHEMA,
                )
                .save()
            )
            rejected_decision = rejected_verification.fact("decision")
            with pytest.raises(
                BackendConflict,
                match="did not accept the candidate",
            ):
                runtime.application.config.publish_config(
                    ConfigPublishCommand(
                        operation_id="publish:rejected-verified-candidate",
                        source=CandidateConfigRevisionSource(
                            run_id=baseline_id,
                            proposal_id=proposal.id,
                            acceptance=CrossRunCandidateAcceptance(
                                decision=ProjectAnalysisDecisionReference(
                                    analysis_record_id=rejected_verification.id,
                                    output_id="decision",
                                    schema_id=rejected_decision.schema_id,
                                    schema_hash=rejected_decision.schema_hash,
                                )
                            ),
                        ),
                        entry_id="rejected-verified-candidate",
                        actor="nightly-calibration",
                        expected_generation=2,
                    )
                )

            with pytest.raises(
                ValueError,
                match="must contain accepted=true",
            ):
                lab.config.accept_verified(
                    proposal_analysis,
                    verified_by=(rejected_verification, "decision"),
                    entry_id="client-rejected-candidate",
                )


@dataclass(frozen=True, slots=True)
class _CalibrationMergeFixture:
    command: CalibrationPublicationCommand
    members: tuple[CalibrationCohortMember, ...]
    baseline_run_ids: tuple[str, ...]
    calibration_keys: tuple[str, ...]


_CALIBRATION_DEFINITION_HASH = "sha256:" + "1" * 64
_CALIBRATION_PROCEDURE_HASH = "sha256:" + "2" * 64
_CALIBRATION_INPUT_HASH = "sha256:" + "3" * 64
_CALIBRATION_SPEC_POLICY_HASH = "sha256:" + "4" * 64


def _calibration_member_spec(target_id: str) -> CalibrationCohortMemberSpec:
    definition = CalibrationDefinitionRef(
        id="tests.drag-calibration",
        version="1",
        fingerprint=_CALIBRATION_DEFINITION_HASH,
        success_policy="published_result",
    )
    target = CalibrationTargetRef(kind="qubit", id=target_id)
    procedure = ProcedureDefinitionRef(
        id="tests.drag-calibration-procedure",
        version="1",
        fingerprint=_CALIBRATION_PROCEDURE_HASH,
    )
    return CalibrationCohortMemberSpec(
        member_id=f"drag-{target_id}",
        calibration_key=calibration_key(definition.id, target),
        definition=definition,
        target=target,
        procedure=procedure,
        intent={"target": target_id},
        input_fingerprint=_CALIBRATION_INPUT_HASH,
        freshness_fingerprint=calibration_freshness_fingerprint(
            definition=definition,
            target=target,
            procedure=procedure,
            input_fingerprint=_CALIBRATION_INPUT_HASH,
            dependencies=(),
        ),
        due_reasons=(CalibrationForcedDueReason(reason="integration test"),),
    )


def _prepare_calibration_merge(
    runtime: LocalDaemonRuntime,
    lab: LabClient,
    *,
    suffix: str,
    reverse_verification_inputs: bool = False,
    target_ids: tuple[str, ...] = ("q0", "q1"),
    automatic_publication: bool = False,
) -> _CalibrationMergeFixture:
    active = runtime.application.config.get_active_config()
    base = CalibrationConfigSourceRef(
        entry_id=active.entry.id,
        config_ref=active.entry.config_ref,
        content_hash=active.entry.content_hash,
        registry_generation=active.activation.generation,
    )
    run_base = ConfigRegistryRunConfigSource(
        selector="active",
        entry_id=base.entry_id,
        config_ref=base.config_ref,
        content_hash=base.content_hash,
        registry_generation=base.registry_generation,
    )
    specs = tuple(_calibration_member_spec(target_id) for target_id in target_ids)
    composition_policy = ConfigCompositionPolicyRef(
        id="tests.drag-composition",
        version="1",
        fingerprint=_CALIBRATION_SPEC_POLICY_HASH,
    )
    publication_policy = (
        CalibrationPublicationPolicyRef(
            id="tests.drag-automatic-publication",
            version="1",
            fingerprint="sha256:" + "5" * 64,
            calibration=specs[0].definition,
            composition_policy=composition_policy,
        )
        if automatic_publication
        else None
    )
    status = runtime.application.calibration_cohorts.status(
        CalibrationStatusQuery(
            calibration_keys=tuple(item.calibration_key for item in specs),
            fanout_scope="calibration-workers",
        )
    ).snapshot
    created = runtime.application.calibration_cohorts.create(
        CalibrationCohortCreateCommand(
            cohort_id=f"merge-cohort-{suffix}",
            spec=CalibrationCohortSpec(
                definition=specs[0].definition,
                automatic_publication=publication_policy,
                config_source=base,
                fanout_scope=status.fanout_scope,
                max_in_flight=len(specs),
                observed_fanout_active_count=status.fanout_active_count,
                evaluated_at=status.observed_at,
                observations=status.statuses,
                members=specs,
            ),
        )
    )

    contributions: list[CalibrationCohortMergeContribution] = []
    proposals: list[ParameterChangeProposal] = []
    baseline_run_ids: list[str] = []
    for index, member in enumerate(created.members):
        target = member.spec.target.id
        baseline_submission = _submission(f"{suffix}-baseline-{target}").model_copy(
            update={"config_source": run_base}
        )
        baseline_run_id = _complete_signal_run(
            runtime,
            submission_id=f"{suffix}-baseline-{target}",
            signal=0.8 + index / 10,
            submission=baseline_submission,
        )
        proposal = _analysis_proposal(baseline_run_id)
        runtime.application.runs.save_run_analysis(
            baseline_run_id,
            _analysis_command(proposal),
        )
        baseline = lab.get_run(baseline_run_id)
        fit = baseline.published_analysis("fit")
        candidate_config, candidate_source = lab.config.resolve_with_source(
            fit.candidate_config()
        )
        assert candidate_source is not None
        candidate_run_id = _complete_signal_run(
            runtime,
            submission_id=f"{suffix}-candidate-{target}",
            signal=1.1 + index / 10,
            submission=_submission(f"{suffix}-candidate-{target}").model_copy(
                update={
                    "config": candidate_config,
                    "config_source": candidate_source,
                }
            ),
        )
        verification_context = lab.analysis(
            f"Calibration verification {target}",
            key=f"{suffix}-verification-{target}",
        )
        verification_context.measurements(
            baseline,
            id="baseline",
            role="baseline",
        )
        verification_context.measurements(
            lab.get_run(candidate_run_id),
            id="candidate",
            role="candidate",
        )
        verification = (
            verification_context.result()
            .fact(
                "decision",
                _CandidateDecision(accepted=True),
                schema=_CANDIDATE_DECISION_SCHEMA,
            )
            .save()
        )
        decision = verification.fact("decision")

        baseline_output = RunOutputRef(run_id=baseline_run_id)
        fit_output = AnalysisPublicationOutputRef(
            subject=RunAnalysisSubject(run_id=baseline_run_id),
            analysis_record_id=fit.id,
        )
        candidate_output = RunOutputRef(run_id=candidate_run_id)
        verification_output = AnalysisPublicationOutputRef(
            subject=ProjectAnalysisSubject(),
            analysis_record_id=verification.id,
        )
        verification_inputs = (
            (candidate_output, baseline_output)
            if reverse_verification_inputs
            else (baseline_output, candidate_output)
        )
        outputs = (
            ("baseline", baseline_output, ()),
            ("fit", fit_output, (baseline_output,)),
            ("candidate", candidate_output, (fit_output,)),
            (
                "verification",
                verification_output,
                verification_inputs,
            ),
        )
        automation = runtime.application.automation
        acquired = automation.acquire_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=member.procedure_run_id,
                worker_id=f"merge-worker-{target}",
                expected_run_revision=1,
            )
        )
        procedure_run = acquired.run
        evidence_step: ConfigCompositionEvidenceStepRef | None = None
        for step_index, (step_key, output, inputs) in enumerate(outputs):
            begun = automation.begin_step(
                ProcedureStepBeginCommand(
                    procedure_run_id=member.procedure_run_id,
                    lease_token=acquired.lease.lease_token,
                    expected_run_revision=procedure_run.revision,
                    step_key=step_key,
                    operation=output.kind,
                    intent_hash=f"sha256:{step_index + 5:x}".ljust(71, "0"),
                    inputs=inputs,
                )
            )
            completed = automation.complete_step(
                ProcedureStepCompleteCommand(
                    procedure_run_id=member.procedure_run_id,
                    lease_token=acquired.lease.lease_token,
                    expected_run_revision=begun.run.revision,
                    step_key=begun.step.step_key,
                    attempt=begun.step.attempt,
                    expected_step_revision=begun.step.revision,
                    output=output,
                )
            )
            procedure_run = completed.run
            if step_key == "verification":
                evidence_step = ConfigCompositionEvidenceStepRef(
                    procedure_run_id=member.procedure_run_id,
                    step_key=completed.step.step_key,
                    attempt=completed.step.attempt,
                )
        automation.close(
            ProcedureCloseCommand(
                procedure_run_id=member.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=procedure_run.revision,
                status="succeeded",
            )
        )
        assert evidence_step is not None
        contributions.append(
            CalibrationCohortMergeContribution(
                member_id=member.spec.member_id,
                proof=VerifiedParameterProposalProofV1(
                    evidence_step=evidence_step,
                    decision=ProjectAnalysisDecisionReference(
                        analysis_record_id=verification.id,
                        output_id="decision",
                        schema_id=decision.schema_id,
                        schema_hash=decision.schema_hash,
                    ),
                ),
                result_input_fingerprint=(f"sha256:{index + 10:x}".ljust(71, "0")),
            )
        )
        proposals.append(proposal)
        baseline_run_ids.append(baseline_run_id)

    candidate_id = f"merged-calibration-{suffix}"
    merged = merge_common_base_parameter_proposals(
        proposals,
        base_config=active.config,
        candidate_id=candidate_id,
    )
    source = CalibrationCohortMergeRevisionSource(
        cohort_id=created.cohort.cohort_id,
        spec_hash=created.cohort.spec_hash,
        automatic_publication=publication_policy,
        composition_policy_ref=composition_policy,
        base_entry_id=base.entry_id,
        base_content_hash=base.content_hash,
        base_generation=base.registry_generation,
        candidate_id=candidate_id,
        contributions=tuple(contributions),
        expected_result_content_hash=merged.content_hash,
    )
    expected_finalization_revision = None
    if publication_policy is not None:
        finalization = runtime.application.calibration_cohorts.get_publication(
            CalibrationPublicationGetQuery(cohort_id=created.cohort.cohort_id)
        ).finalization
        assert finalization.state == "ready"
        expected_finalization_revision = finalization.revision
    return _CalibrationMergeFixture(
        command=CalibrationPublicationCommand(
            operation_id=f"publish:calibration-merge:{suffix}",
            source=source,
            actor="calibration-finalizer",
            expected_generation=base.registry_generation,
            expected_finalization_revision=expected_finalization_revision,
            entry_id=f"calibration-merge-{suffix}",
        ),
        members=created.members,
        baseline_run_ids=tuple(baseline_run_ids),
        calibration_keys=tuple(item.calibration_key for item in specs),
    )


def _approval_count(
    runtime: LocalDaemonRuntime,
    baseline_run_ids: tuple[str, ...],
) -> int:
    return sum(
        len(
            runtime.application.runs.list_run_contents(
                run_id,
                kind="parameter_change_approval_record",
            ).items
        )
        for run_id in baseline_run_ids
    )


def _publication_side_effects(
    runtime: LocalDaemonRuntime,
    fixture: _CalibrationMergeFixture,
) -> tuple[object, ...]:
    status = runtime.application.calibration_cohorts.status(
        CalibrationStatusQuery(
            calibration_keys=fixture.calibration_keys,
            fanout_scope="calibration-workers",
        )
    ).snapshot
    return (
        runtime.application.config.get_config_registry(),
        runtime.application.config.get_config_activation_history(),
        _events(runtime),
        _approval_count(runtime, fixture.baseline_run_ids),
        tuple(item.latest_success for item in status.statuses),
    )


def test_calibration_merge_publishes_one_replayable_atomic_revision(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        client = _daemon_client(transport)
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(client),
            suffix="ok",
            reverse_verification_inputs=True,
        )
        source = fixture.command.source
        assert isinstance(source, CalibrationCohortMergeRevisionSource)
        reversed_payload = fixture.command.model_dump(mode="python")
        reversed_payload["source"]["contributions"] = tuple(
            reversed(source.contributions)
        )
        reversed_command = CalibrationPublicationCommand.model_validate(
            reversed_payload
        )
        assert reversed_command == fixture.command
        assert fixture.command.expected_finalization_revision is None

        receipt = client.publish_calibration(fixture.command)

        with sqlite3.connect(runtime.state_dir / "control.sqlite3") as connection:
            operation_kind = connection.execute(
                "SELECT kind FROM config_operations WHERE operation_id = ?",
                (fixture.command.operation_id,),
            ).fetchone()
        assert operation_kind is not None
        assert operation_kind[0] == "publish_calibration"
        with pytest.raises(BackendConflict, match="not a config publication"):
            runtime.application.config.get_config_publish_operation(
                fixture.command.operation_id
            )
        with pytest.raises(BackendConflict, match="different intent"):
            runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id=fixture.command.operation_id,
                    source=DirectConfigRevisionSource(config=_config()),
                    actor="operator",
                    expected_generation=fixture.command.expected_generation,
                    entry_id="cross-kind-collision",
                )
            )

        assert receipt.operation.activation_generation == 2
        assert receipt.activation.generation == 2
        assert receipt.entry.content_hash == source.expected_result_content_hash
        assert isinstance(
            receipt.entry.source,
            CalibrationCohortMergeRegistrySource,
        )
        resolved_proofs = tuple(
            contribution.proof for contribution in receipt.entry.source.contributions
        )
        assert tuple(proof.kind for proof in resolved_proofs) == (
            "verified_parameter_proposal_v1",
            "verified_parameter_proposal_v1",
        )
        assert (
            tuple(proof.baseline_run_id for proof in resolved_proofs)
            == fixture.baseline_run_ids
        )
        assert tuple(proof.proposal_id for proof in resolved_proofs) == (
            "drive-frequency",
            "drive-frequency",
        )
        assert all(
            proof.evidence_step.step_key == "verification" for proof in resolved_proofs
        )
        assert len(receipt.calibration_successes) == 2
        assert tuple(
            success.attempt.member_id for success in receipt.calibration_successes
        ) == ("drag-q0", "drag-q1")
        assert all(
            success.publication is not None
            and success.publication.operation_id == fixture.command.operation_id
            and success.publication.result_config_source.entry_id == receipt.entry.id
            for success in receipt.calibration_successes
        )
        assert (
            CalibrationPublicationReceipt(
                operation=receipt.operation,
                entry=receipt.entry,
                deltas=receipt.deltas,
                activation=receipt.activation,
                calibration_successes=tuple(reversed(receipt.calibration_successes)),
            )
            == receipt
        )
        with pytest.raises(ValidationError, match="cover every resolved"):
            CalibrationPublicationReceipt(
                operation=receipt.operation,
                entry=receipt.entry,
                deltas=receipt.deltas,
                activation=receipt.activation,
                calibration_successes=receipt.calibration_successes[:1],
            )
        first_success, second_success = receipt.calibration_successes
        assert first_success.publication is not None
        wrong_operation_success = first_success.model_copy(
            update={
                "publication": first_success.publication.model_copy(
                    update={"operation_id": "different-operation"}
                )
            }
        )
        wrong_source_success = first_success.model_copy(
            update={
                "publication": first_success.publication.model_copy(
                    update={"source_intent_hash": "sha256:" + "f" * 64}
                )
            }
        )
        wrong_result_success = first_success.model_copy(
            update={
                "publication": first_success.publication.model_copy(
                    update={
                        "result_config_source": (
                            first_success.publication.result_config_source.model_copy(
                                update={"entry_id": "different-result"}
                            )
                        )
                    }
                )
            }
        )
        for invalid_success in (
            wrong_operation_success,
            wrong_source_success,
            wrong_result_success,
        ):
            with pytest.raises(ValidationError, match="does not match"):
                CalibrationPublicationReceipt(
                    operation=receipt.operation,
                    entry=receipt.entry,
                    deltas=receipt.deltas,
                    activation=receipt.activation,
                    calibration_successes=(invalid_success, second_success),
                )
        assert len(runtime.application.config.get_config_registry().entries) == 2
        assert _approval_count(runtime, fixture.baseline_run_ids) == 2
        assert (
            len(
                [
                    event
                    for event in _events(runtime).items
                    if event.kind == "parameter_proposal_approved"
                ]
            )
            == 2
        )
        projected = runtime.application.calibration_cohorts.status(
            CalibrationStatusQuery(
                calibration_keys=fixture.calibration_keys,
                fanout_scope="calibration-workers",
            )
        ).snapshot.statuses
        assert all(
            item.latest_success is not None
            and item.latest_success.is_effective
            and item.latest_success.publication is not None
            and item.latest_success.publication.operation_id
            == fixture.command.operation_id
            for item in projected
        )
        assert client.publish_calibration(reversed_command) == receipt

    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
    ):
        client = _daemon_client(transport)
        assert (
            client.calibration_publication_operation(fixture.command.operation_id)
            == receipt
        )
        # The active head is now generation 2 while this command names base 1;
        # success therefore proves replay occurs before live proof and CAS checks.
        assert client.publish_calibration(fixture.command) == receipt


def test_single_member_calibration_merge_is_atomic_and_replayable(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        client = _daemon_client(transport)
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(client),
            suffix="single",
            target_ids=("q0",),
        )
        source = fixture.command.source
        assert isinstance(source, CalibrationCohortMergeRevisionSource)
        assert len(source.contributions) == 1

        receipt = client.publish_calibration(fixture.command)

        assert receipt.operation.activation_generation == 2
        assert receipt.activation.generation == 2
        assert receipt.entry.content_hash == source.expected_result_content_hash
        assert len(receipt.calibration_successes) == 1
        success = receipt.calibration_successes[0]
        assert success.attempt.member_id == "drag-q0"
        assert success.publication is not None
        assert success.publication.operation_id == fixture.command.operation_id
        assert success.publication.result_config_source.entry_id == receipt.entry.id
        assert len(runtime.application.config.get_config_registry().entries) == 2
        assert _approval_count(runtime, fixture.baseline_run_ids) == 1
        assert (
            len(
                [
                    event
                    for event in _events(runtime).items
                    if event.kind == "parameter_proposal_approved"
                ]
            )
            == 1
        )
        status = runtime.application.calibration_cohorts.status(
            CalibrationStatusQuery(
                calibration_keys=fixture.calibration_keys,
                fanout_scope="calibration-workers",
            )
        ).snapshot.statuses
        assert len(status) == 1
        projected_success = status[0].latest_success
        assert projected_success == success
        assert projected_success is not None
        assert projected_success.is_effective

        after_publish = _publication_side_effects(runtime, fixture)
        assert client.publish_calibration(fixture.command) == receipt
        assert _publication_side_effects(runtime, fixture) == after_publish

    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
    ):
        client = _daemon_client(transport)
        assert (
            client.calibration_publication_operation(fixture.command.operation_id)
            == receipt
        )
        assert client.publish_calibration(fixture.command) == receipt
        assert len(restarted.application.config.get_config_registry().entries) == 2
        assert _approval_count(restarted, fixture.baseline_run_ids) == 1


def test_automatic_calibration_merge_completes_ready_work_atomically(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        client = _daemon_client(transport)
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(client),
            suffix="automatic",
            automatic_publication=True,
        )
        source = fixture.command.source
        assert isinstance(source, CalibrationCohortMergeRevisionSource)
        policy = source.automatic_publication
        assert policy is not None

        before = client.get_calibration_publication(source.cohort_id).finalization
        assert before.state == "ready"
        assert fixture.command.expected_finalization_revision == before.revision
        page = client.list_ready_calibration_publications(
            CalibrationPublicationReadyQuery(capabilities=(policy,))
        )
        assert tuple(item.cohort.cohort_id for item in page.items) == (
            source.cohort_id,
        )

        receipt = client.publish_calibration(fixture.command)

        completed = client.get_calibration_publication(source.cohort_id).finalization
        assert completed.state == "published"
        assert completed.revision == before.revision + 1
        assert completed.publication is not None
        assert completed.publication.operation_id == fixture.command.operation_id
        assert completed.publication.published_at == receipt.activation.recorded_at
        assert (
            client.list_ready_calibration_publications(
                CalibrationPublicationReadyQuery(capabilities=(policy,))
            ).items
            == ()
        )
        assert len(receipt.calibration_successes) == 2
        entry_source = receipt.entry.source
        assert entry_source.kind == "calibration_cohort_merge"
        assert entry_source.automatic_publication_policy_id == policy.id
        assert entry_source.automatic_publication_policy_version == policy.version
        assert (
            entry_source.automatic_publication_policy_fingerprint == policy.fingerprint
        )

        after = _publication_side_effects(runtime, fixture)
        # Model a committed response that the worker did not observe. Operation
        # replay must win before the now-stale finalization fence is inspected.
        assert client.publish_calibration(fixture.command) == receipt
        assert _publication_side_effects(runtime, fixture) == after
        assert (
            client.get_calibration_publication(source.cohort_id).finalization
            == completed
        )

    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
    ):
        client = _daemon_client(transport)
        assert client.publish_calibration(fixture.command) == receipt
        assert (
            client.get_calibration_publication(source.cohort_id).finalization
            == completed
        )


@pytest.mark.parametrize("transition", ["attention", "defer"])
def test_automatic_calibration_merge_fences_stale_ready_occurrences(
    tmp_path: Path,
    transition: str,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        client = _daemon_client(transport)
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(client),
            suffix=transition,
            automatic_publication=True,
        )
        source = fixture.command.source
        assert isinstance(source, CalibrationCohortMergeRevisionSource)
        policy = source.automatic_publication
        expected_revision = fixture.command.expected_finalization_revision
        assert policy is not None
        assert expected_revision is not None
        before_effects = _publication_side_effects(runtime, fixture)

        if transition == "attention":
            changed = client.require_calibration_publication_attention(
                CalibrationPublicationAttentionCommand(
                    cohort_id=source.cohort_id,
                    policy=policy,
                    expected_finalization_revision=expected_revision,
                    actor="calibration-finalizer",
                    reason="deterministic proof drift",
                )
            ).finalization
            assert changed.state == "attention_required"
        else:
            changed = client.defer_calibration_publication(
                CalibrationPublicationDeferCommand(
                    cohort_id=source.cohort_id,
                    policy=policy,
                    expected_finalization_revision=expected_revision,
                    retry_after_seconds=600,
                    reason="transient dependency outage",
                )
            ).finalization
            assert changed.state == "ready"
            assert changed.available_at is not None
            assert changed.available_at > changed.updated_at
        assert changed.revision == expected_revision + 1

        with pytest.raises(BackendConflict, match="not eligible"):
            runtime.application.config.publish_calibration(fixture.command)

        rebased_payload = fixture.command.model_dump(mode="python")
        rebased_payload["expected_finalization_revision"] = changed.revision
        rebased = CalibrationPublicationCommand.model_validate(rebased_payload)
        assert rebased.operation_id == fixture.command.operation_id
        assert rebased.intent_hash == fixture.command.intent_hash
        with pytest.raises(BackendConflict, match="not eligible"):
            runtime.application.config.publish_calibration(rebased)

        assert _publication_side_effects(runtime, fixture) == before_effects
        assert (
            runtime.application.calibration_cohorts.get_publication(
                CalibrationPublicationGetQuery(cohort_id=source.cohort_id)
            ).finalization
            == changed
        )
        with pytest.raises(BackendNotFound):
            runtime.application.config.get_calibration_publication_operation(
                fixture.command.operation_id
            )


def test_calibration_merge_proof_and_result_failures_have_no_side_effects(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(_daemon_client(transport)),
            suffix="invalid",
        )
        source = fixture.command.source
        assert isinstance(source, CalibrationCohortMergeRevisionSource)
        before = _publication_side_effects(runtime, fixture)

        first, second = source.contributions
        invalid_sources = (
            source.model_copy(
                update={
                    "contributions": (
                        first.model_copy(
                            update={
                                "proof": first.proof.model_copy(
                                    update={
                                        "evidence_step": (
                                            first.proof.evidence_step.model_copy(
                                                update={"attempt": 2}
                                            )
                                        )
                                    }
                                )
                            }
                        ),
                        second,
                    )
                }
            ),
            source.model_copy(update={"base_entry_id": "wrong-base"}),
            source.model_copy(
                update={
                    "contributions": (
                        first.model_copy(
                            update={
                                "proof": first.proof.model_copy(
                                    update={"decision": second.proof.decision}
                                )
                            }
                        ),
                        second,
                    )
                }
            ),
            source.model_copy(
                update={
                    "contributions": (
                        first.model_copy(
                            update={
                                "proof": first.proof.model_copy(
                                    update={
                                        "evidence_step": (
                                            first.proof.evidence_step.model_copy(
                                                update={"step_key": "baseline"}
                                            )
                                        )
                                    }
                                )
                            }
                        ),
                        second,
                    )
                }
            ),
            source.model_copy(
                update={"expected_result_content_hash": "sha256:" + "f" * 64}
            ),
        )
        for index, invalid_source in enumerate(invalid_sources):
            command = CalibrationPublicationCommand(
                operation_id=f"invalid-calibration-publication-{index}",
                source=invalid_source,
                actor=fixture.command.actor,
                expected_generation=fixture.command.expected_generation,
                entry_id=f"invalid-calibration-entry-{index}",
            )
            with pytest.raises(BackendConflict):
                runtime.application.config.publish_calibration(command)
            assert _publication_side_effects(runtime, fixture) == before
            with pytest.raises(BackendNotFound):
                runtime.application.config.get_calibration_publication_operation(
                    command.operation_id
                )


def test_calibration_merge_cas_and_final_anchor_failures_roll_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(_daemon_client(transport)),
            suffix="rollback",
            automatic_publication=True,
        )
        merge_source = fixture.command.source
        assert isinstance(merge_source, CalibrationCohortMergeRevisionSource)
        assert merge_source.automatic_publication is not None
        intervening_config = _config().model_copy(update={"id": "intervening"})
        normal = runtime.application.config.publish_config(
            ConfigPublishCommand(
                operation_id="publish:intervening",
                source=DirectConfigRevisionSource(config=intervening_config),
                actor="operator",
                expected_generation=1,
                entry_id="intervening",
            )
        )
        assert type(normal) is ConfigPublishReceipt
        superseded = runtime.application.calibration_cohorts.get_publication(
            CalibrationPublicationGetQuery(cohort_id=merge_source.cohort_id)
        ).finalization
        assert superseded.state == "superseded"
        assert superseded.supersession is not None
        assert (
            superseded.supersession.superseded_by_generation
            == normal.activation.generation
        )
        assert not runtime.application.calibration_cohorts.ready_publications(
            CalibrationPublicationReadyQuery(
                capabilities=(merge_source.automatic_publication,)
            )
        ).items
        pending = (
            runtime.application.calibration_cohorts.status(
                CalibrationStatusQuery(
                    calibration_keys=fixture.calibration_keys,
                    fanout_scope="calibration-workers",
                )
            )
            .snapshot.statuses[0]
            .latest_success
        )
        assert pending is not None
        with pytest.raises(ValidationError, match="requires a cohort merge"):
            CalibrationPublicationReceipt(
                operation=normal.operation,
                entry=normal.entry,
                deltas=normal.deltas,
                activation=normal.activation,
                calibration_successes=(pending,),
            )
        before_cas = _publication_side_effects(runtime, fixture)
        with pytest.raises(BackendConflict):
            runtime.application.config.publish_calibration(fixture.command)
        assert _publication_side_effects(runtime, fixture) == before_cas
        assert _approval_count(runtime, fixture.baseline_run_ids) == 0
        with pytest.raises(BackendNotFound):
            runtime.application.config.get_calibration_publication_operation(
                fixture.command.operation_id
            )

    anchor_root = tmp_path / "anchor-failure"
    with (
        LocalDaemonRuntime(anchor_root, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(_daemon_client(transport)),
            suffix="anchor",
        )
        before_anchor = _publication_side_effects(runtime, fixture)
        insert = SQLiteCalibrationCohortStore.insert_success_publication_in_transaction
        calls = 0

        def fail_second_anchor(
            store: SQLiteCalibrationCohortStore,
            connection: sqlite3.Connection,
            success: CalibrationSuccessRef,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise CalibrationCohortConflict("injected final anchor failure")
            insert(store, connection, success)

        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteCalibrationCohortStore,
                "insert_success_publication_in_transaction",
                fail_second_anchor,
            )
            with pytest.raises(BackendConflict, match="final anchor failure"):
                runtime.application.config.publish_calibration(fixture.command)

        assert calls == 2
        assert _publication_side_effects(runtime, fixture) == before_anchor
        with pytest.raises(BackendNotFound):
            runtime.application.config.get_calibration_publication_operation(
                fixture.command.operation_id
            )
        retried = runtime.application.config.publish_calibration(fixture.command)
        assert retried.activation.generation == 2

    finalization_root = tmp_path / "finalization-failure"
    with (
        LocalDaemonRuntime(finalization_root, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        fixture = _prepare_calibration_merge(
            runtime,
            LabClient(_daemon_client(transport)),
            suffix="finalization",
            automatic_publication=True,
        )
        source = fixture.command.source
        assert isinstance(source, CalibrationCohortMergeRevisionSource)
        before_finalization = runtime.application.calibration_cohorts.get_publication(
            CalibrationPublicationGetQuery(cohort_id=source.cohort_id)
        ).finalization
        before_effects = _publication_side_effects(runtime, fixture)

        def fail_finalization(
            _store: SQLiteCalibrationCohortStore,
            _connection: sqlite3.Connection,
            *,
            cohort_id: str,
            policy: CalibrationPublicationPolicyRef,
            expected_revision: int,
            operation_id: str,
            at: object,
        ) -> object:
            del cohort_id, policy, expected_revision, operation_id, at
            raise CalibrationCohortConflict("injected finalization failure")

        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteCalibrationCohortStore,
                "complete_publication_in_transaction",
                fail_finalization,
            )
            with pytest.raises(BackendConflict, match="finalization failure"):
                runtime.application.config.publish_calibration(fixture.command)

        assert _publication_side_effects(runtime, fixture) == before_effects
        assert (
            runtime.application.calibration_cohorts.get_publication(
                CalibrationPublicationGetQuery(cohort_id=source.cohort_id)
            ).finalization
            == before_finalization
        )
        with pytest.raises(BackendNotFound):
            runtime.application.config.get_calibration_publication_operation(
                fixture.command.operation_id
            )
        retried = runtime.application.config.publish_calibration(fixture.command)
        assert retried.activation.generation == 2
        assert (
            runtime.application.calibration_cohorts.get_publication(
                CalibrationPublicationGetQuery(cohort_id=source.cohort_id)
            ).finalization.state
            == "published"
        )


def _compare_entity_runs(baseline: Dataset, candidate: Dataset) -> DerivedDataset:
    left, right = baseline.align_entities(candidate, "entity", join="outer")
    index = left.schema.dimensions[1].index
    assert index is not None
    before = np.ma.asarray(left["signal"].values[0])
    after = np.ma.asarray(right["signal"].values[0])
    return derived_dataset(
        pa.table(
            {
                "entity": [entity.id for entity in index.values],
                "baseline": before.tolist(),
                "candidate": after.tolist(),
                "delta": (after - before).tolist(),
            }
        )
    )


def test_cross_run_entity_comparison_freezes_sources_and_survives_restart(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        baseline_id = _complete_signal_run(
            runtime,
            submission_id="entity-baseline",
            signal=(1.0, 2.0, 3.0),
            entities=tuple(EntityRef(id=q, kind="qubit") for q in ("q0", "q1", "q2")),
        )
        candidate_id = _complete_signal_run(
            runtime,
            submission_id="entity-candidate",
            signal=(30.0, 10.0, 40.0),
            entities=tuple(EntityRef(id=q, kind="qubit") for q in ("q2", "q0", "q3")),
        )
        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            context = lab.analysis("Entity comparison", key="entity-comparison")
            baseline = context.measurements(
                lab.get_run(baseline_id), id="baseline", role="baseline"
            )
            candidate = context.measurements(
                lab.get_run(candidate_id), id="candidate", role="candidate"
            )
            with pytest.raises(ValueError, match="already bound"):
                context.measurements(lab.get_run(candidate_id), id="baseline")
            with pytest.raises(ValueError, match="ordered identities differ"):
                baseline.align_entities(candidate, "entity")
            comparison = context.trace(
                fn=_compare_entity_runs, baseline=baseline, candidate=candidate
            )
            published = context.result().dataset("comparison", comparison).save()
            expected = {
                "entity": ["q0", "q1", "q2", "q3"],
                "baseline": [1.0, 2.0, 3.0, None],
                "candidate": [10.0, None, 30.0, 40.0],
                "delta": [9.0, None, 27.0, None],
            }
            assert published.dataset("comparison").table.to_pydict() == expected
            inputs = [item.model_dump(mode="json") for item in published.inputs]
            assert {item["run_id"] for item in inputs} == {baseline_id, candidate_id}
            assert {item["content_hash"] for item in inputs} == {
                baseline.entry.content_hash,
                candidate.entry.content_hash,
            }
            assert {
                binding.target for binding in published.executions[0].input_bindings
            } == {"baseline", "candidate"}
            publication_id = published.id
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        restored = LabClient(_daemon_client(transport)).published_analysis(
            publication_id
        )
        assert restored.dataset("comparison").table.to_pydict() == expected
        assert [item.model_dump(mode="json") for item in restored.inputs] == inputs


def test_primary_run_analysis_checks_secondary_owner_and_exact_content(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        primary_id = _complete_signal_run(runtime, submission_id="primary", signal=0.8)
        secondary_id = _complete_signal_run(
            runtime, submission_id="secondary", signal=1.1
        )
        with TestClient(runtime.app()) as transport:
            lab = LabClient(_daemon_client(transport))
            primary, secondary = lab.get_run(primary_id), lab.get_run(secondary_id)
            context = primary.analysis("Comparison", key="comparison")
            context.measurements(id="primary")
            context.measurements(secondary, id="secondary")
            analysis = context.result().fact("compared", True)
            saved = analysis.save()
            assert saved.view.analysis.subject == RunAnalysisSubject(run_id=primary_id)
            first, second = analysis.inputs
            assert isinstance(first, MeasurementAnalysisInput)
            assert isinstance(second, MeasurementAnalysisInput)
            with pytest.raises(DaemonConflictError, match="primary run"):
                replace(analysis, inputs=(second,)).save()
            with pytest.raises(DaemonConflictError, match="exact run content"):
                replace(
                    analysis,
                    inputs=(first, replace(second, content_hash="sha256:wrong")),
                ).save()
            with pytest.raises(DaemonNotFoundError):
                replace(
                    analysis,
                    inputs=(first, replace(second, run_id="removed-secondary")),
                ).save()
            assert (
                primary.published_analysis(saved.id).publication_hash
                == saved.publication_hash
            )
