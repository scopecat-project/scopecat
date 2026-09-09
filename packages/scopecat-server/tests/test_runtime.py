# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import json
import sqlite3
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Thread
from typing import Literal, Never, cast
from urllib.parse import quote

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient
from scopecat.adaptive_domains import DomainProposalAttempt, ResolvedDomainFragment
from scopecat.analysis.datasets import DerivedDataset
from scopecat.application import LabBootstrap
from scopecat.config.changes import parameter_change_proposal_from_updates
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.inventory import InstrumentInventoryRekey
from scopecat.config.parameters import ReplaceParameter, replace_scalar_parameter
from scopecat.config.registry import (
    ActiveConfigRegistrySnapshot,
    ManualCandidateAcceptance,
)
from scopecat.control.models import (
    AdaptiveRegionSpec,
    DurableEvent,
    DurableEventInput,
    EventPage,
    PointCoordinateSpec,
    ResourceClaim,
    ResourceKey,
    RunDomainTargetRequirement,
    RunPlanSummary,
    RunResourceRequirement,
)
from scopecat.daemon.points import (
    AcceptedRunPointView,
    RunDomainDecisionCommand,
    RunDomainEnqueueCommand,
    RunDomainFragmentInput,
    RunDomainProposalAttemptView,
    RunDomainResolveCommand,
    RunPointCoordinateValue,
    RunPointPlanCloseCommand,
)
from scopecat.daemon.views import (
    ActiveConfigView,
    ConfigActivationPage,
    ConfigDraftPreview,
    ConfigEntryView,
    ConfigRegistryPage,
    MeasurementArrowColumn,
    MeasurementArrowQuery,
    ParameterProposalPage,
    ParameterProposalView,
    RunAnalysisPage,
    RunAnalysisView,
    RunConfigView,
    RunControlView,
    RunDatasetBytesView,
    RunDetail,
)
from scopecat.daemon.wire import (
    AnalysisArtifactOutputPayload,
    AnalysisDatasetOutputPayload,
    AnalysisFigureOutputPayload,
    AnalysisParameterProposalOutputPayload,
    AnalysisSaveCommand,
    AnalysisSaveReceipt,
    AnalysisTableOutputPayload,
    AttentionResolutionCommand,
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigDraftCommand,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    DirectConfigRevisionSource,
    ExecutorHeartbeat,
    ExecutorLease,
    ExecutorStartRequest,
    InstrumentInventoryMigrationCommand,
    InstrumentInventoryMigrationReceipt,
    ManualConfigDraftRevisionSource,
    MeasurementAnalysisInputPayload,
    MeasurementFlushCommand,
    MeasurementHeaderCommand,
    MeasurementSealCommand,
    RunAdmission,
    RunAttachmentCommand,
    RunCancellationReceipt,
    RunCoverageAdvanceCommand,
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionItem,
    RunRecoveryGroupCommitCommand,
    RunSubmission,
    TerminalRunCommitCommand,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import StorageError
from scopecat.kernel.points import PointProposalAttempt
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.measurements.recording_arrow import (
    decode_measurement_append,
    encode_measurement_append,
)
from scopecat.project_state import ProjectStateServices
from scopecat.records.analysis import (
    AnalysisDatasetViewSource,
    AnalysisField,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisFigureViewSpec,
    AnalysisTableViewSpec,
)
from scopecat.records.config import (
    ConfigProfileSnapshot,
    TcpipSocketInstrumentConnection,
    config_content_hash,
)
from scopecat.records.content import ContentEntry
from scopecat.records.execution import (
    DomainExecutionId,
    DomainExecutionReceipt,
    DomainJobCheckpoint,
    DomainJobCheckpointTransition,
    DomainJobInvocationTransition,
    DomainJobTerminalTransition,
    RecoveryGroupCompletion,
)
from scopecat.records.measurement import (
    EntityAcquisitionEvidence,
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionEvidenceCatalog,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityAcquisition,
    MeasurementEntityIndex,
    MeasurementPointDomainAxis,
    MeasurementPointDomainValuesSource,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementSegmentedArray,
    MeasurementUnavailable,
    MeasurementVariable,
    MeasurementVariableGroup,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetBatch,
    MeasurementDatasetHeader,
    MeasurementDatasetSeal,
    measurement_dataset_content_hash,
    measurement_fragment_content_hash,
    measurement_record_content_hash,
)
from scopecat.records.parameter import ScalarParameterValue
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
)
from scopecat.records.run import ConfigRegistryRunConfigSource, RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.runs.refs import dataset_content_ref, record_content_ref
from scopecat_testkit.domain import domain_execution_identity
from scopecat_testkit.server.runtime import list_test_runs

import scopecat_server.services.leases as lease_supervisor_services
from scopecat_server import BackendConflict, BackendNotFound, LocalDaemonRuntime
from scopecat_server.instruments.actors import InstrumentActorRetirement
from scopecat_server.services.admission import AdmissionService
from scopecat_server.services.leases import OwnershipLeaseSupervisor
from scopecat_server.services.point_plans import RunPointPlanService
from scopecat_server.services.samples import SampleService
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import (
    ControlPlaneConflict,
    SQLiteControlPlane,
)
from scopecat_server.storage.sqlite.execution import (
    SQLiteMeasurementDatasetRepository,
)
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository
from scopecat_server.storage.sqlite.samples import SQLiteSampleStore

_FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "core"
    / "simple_scan"
    / "config-snapshot.json"
)


def _config() -> ConfigProfileSnapshot:
    return load_config_snapshot_document(_FIXTURE)


def _direct_publish_command(
    *,
    entry_id: str,
    config: ConfigProfileSnapshot,
    actor: str,
    expected_generation: int = 0,
    note: str = "",
    operation_id: str | None = None,
) -> ConfigPublishCommand:
    return ConfigPublishCommand(
        operation_id=operation_id or f"publish:{entry_id}",
        source=DirectConfigRevisionSource(config=config),
        entry_id=entry_id,
        actor=actor,
        expected_generation=expected_generation,
        note=note,
    )


def _run_detail(runtime: LocalDaemonRuntime, run_id: str) -> RunDetail:
    return runtime.application.runs.get_run(run_id)


def _control_run(runtime: LocalDaemonRuntime, run_id: str) -> RunControlView:
    return _run_detail(runtime, run_id).control


def _snapshot(runtime: LocalDaemonRuntime, run_id: str) -> RunSnapshot:
    return _run_repository(runtime.project_root).read_snapshot(run_id)


def _run_state(
    runtime: LocalDaemonRuntime,
    run_id: str,
) -> tuple[RunSnapshot, tuple[ContentEntry, ...]]:
    repository = _run_repository(runtime.project_root)
    return (
        repository.read_snapshot(run_id),
        repository.list_contents(run_id, limit=100).items,
    )


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


def _resource_claims(project_root: Path) -> tuple[ResourceClaim, ...]:
    control = SQLiteControlPlane(
        SQLiteDatabase(project_root / ".scopecat" / "control.sqlite3")
    )
    with control.read_transaction() as connection:
        return control.list_resource_claims_in_transaction(connection)


def _run_repository(project_root: Path) -> SQLiteRunRepository:
    state = project_root / ".scopecat"
    return SQLiteRunRepository(
        SQLiteDatabase(state / "control.sqlite3"), state / "objects"
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


def _domain_only_config() -> ConfigProfileSnapshot:
    config = _config()
    [instrument] = config.instrument_registry.instruments
    configured = instrument.model_copy(update={"exclusivity_key": "rack-a/source"})
    registry = config.instrument_registry.model_copy(
        update={"instruments": [configured]}
    )
    target = config.domain_target
    assert target is not None
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "instrument_registry": registry,
                    "domain_target": target.model_copy(
                        update={"instrument_ids": ["source-0"]}
                    ),
                }
            )
        }
    )


def _rekeyed_config(
    config: ConfigProfileSnapshot,
    *,
    exclusivity_key: str = "rack-a/source",
) -> ConfigProfileSnapshot:
    [instrument] = config.instrument_registry.instruments
    registry = config.instrument_registry.model_copy(
        update={
            "instruments": [
                instrument.model_copy(update={"exclusivity_key": exclusivity_key})
            ]
        }
    )
    return config.model_copy(
        update={
            "id": "inventory-v2",
            "system": config.system.model_copy(
                update={"instrument_registry": registry}
            ),
        }
    )


def _inventory_migration_command(
    config: ConfigProfileSnapshot,
    *,
    expected_generation: int = 1,
) -> InstrumentInventoryMigrationCommand:
    [target] = config.instrument_registry.instruments
    return InstrumentInventoryMigrationCommand(
        config=config,
        entry_id="inventory-v2",
        changes=(
            InstrumentInventoryRekey(
                instrument_id=target.id,
                from_exclusivity_key="source-0",
                to_exclusivity_key=target.exclusivity_key,
            ),
        ),
        actor="operator",
        expected_generation=expected_generation,
        note="moved to rack-a",
    )


def _domain_only_submission(
    config: ConfigProfileSnapshot,
    *,
    submission_id: str,
    requirements: tuple[RunResourceRequirement, ...],
) -> RunSubmission:
    target = config.domain_target
    assert target is not None
    return RunSubmission(
        submission_id=submission_id,
        config=config,
        request=RunRequest(experiment_id="domain-only"),
        plan=RunPlanSummary(
            experiment_id="domain-only",
            experiment_kind="domain-only",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
            domain_target_requirement=RunDomainTargetRequirement(
                id=target.id,
                kind=target.kind,
                instrument_ids=tuple(target.instrument_ids),
            ),
            run_resource_requirements=requirements,
        ),
    )


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
            AnalysisTableOutputPayload(
                kind="table",
                id="fit-parameters",
                title="fit parameters",
                content=AnalysisTableViewSpec(
                    source=AnalysisDatasetViewSource(output_id="fits"),
                    columns=("bias",),
                ),
            ),
            AnalysisDatasetOutputPayload(
                kind="dataset",
                id="fits",
                title="fit data",
                content=DerivedDataset.from_arrow(
                    pa.table({"bias": [1.0, 2.0], "signal": [3.0, 4.0]}),
                    fields={"bias": AnalysisField(role="coordinate")},
                ).to_payload(),
            ),
            AnalysisFigureOutputPayload(
                kind="figure",
                id="fit-curve",
                title="fit curve",
                content=AnalysisFigureViewSpec(
                    layers=(
                        AnalysisFigureLayerSpec(
                            id="data",
                            source=AnalysisDatasetViewSource(output_id="fits"),
                            projection=AnalysisFigureProjection(
                                kind="line", x="bias", y="signal"
                            ),
                        ),
                    )
                ),
            ),
            AnalysisParameterProposalOutputPayload(
                kind="parameter_change_proposal",
                id=proposal.id,
                title=proposal.id,
                content=proposal,
            ),
            AnalysisArtifactOutputPayload(
                kind="artifact",
                id="fit-report",
                title="Fit report",
                content_base64="IyBGaXQgcmVwb3J0Cg==",
                filename="fit-report.md",
                media_type="text/markdown",
            ),
        ),
    )


def test_runtime_bootstraps_project_control_plane_and_health(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path) as runtime:
        response = TestClient(runtime.app()).get("/api/v1/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["project_id"].startswith("local:")
        assert response.json()["project_name"] == tmp_path.name
        assert response.json()["project_root"] == str(tmp_path)
        assert (tmp_path / ".scopecat" / "control.sqlite3").is_file()
        assert (tmp_path / ".scopecat" / "objects").is_dir()


@pytest.mark.parametrize("failure_point", ["reconciliation", "thread"])
def test_runtime_cleans_up_partially_started_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: Literal["reconciliation", "thread"],
) -> None:
    if failure_point == "reconciliation":

        def fail_reconciliation(_supervisor: object) -> Never:
            raise RuntimeError("startup reconciliation failed")

        monkeypatch.setattr(
            OwnershipLeaseSupervisor,
            "_reconcile_startup",
            fail_reconciliation,
        )
    else:

        class FailingThread:
            def start(self) -> Never:
                raise RuntimeError("supervisor thread failed to start")

        def build_thread(
            *,
            target: object,
            name: str,
            daemon: bool,
        ) -> FailingThread:
            del target, name, daemon
            return FailingThread()

        monkeypatch.setattr(lease_supervisor_services, "Thread", build_thread)

    with pytest.raises(RuntimeError, match=r"(reconciliation|thread) failed"):
        LocalDaemonRuntime(tmp_path)

    monkeypatch.undo()
    with LocalDaemonRuntime(tmp_path):
        pass


def test_lease_supervisor_health_recovers_after_one_failed_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with LocalDaemonRuntime(tmp_path) as runtime:
        instruments = runtime.application.instruments
        failed = Event()
        permit_success = Event()

        def fail_once_then_succeed() -> None:
            if not failed.is_set():
                failed.set()
                raise RuntimeError("temporary lease scan failure")
            assert permit_success.wait(timeout=5)

        monkeypatch.setattr(instruments, "expire_leases", fail_once_then_succeed)
        assert failed.wait(timeout=5)
        assert runtime.application.health().status == "degraded"

        permit_success.set()
        deadline = time.monotonic() + 5
        while (
            runtime.application.health().status != "ok" and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert runtime.application.health().status == "ok"


def test_lease_supervisor_releases_unflushed_live_measurements(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(
        tmp_path,
        bootstrap_config=_config(),
        lease_ttl=timedelta(seconds=1),
    ) as runtime:
        admission = runtime.application.submit_run(_submission("lost-live-data"))
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )
        header = MeasurementDatasetHeader(
            run_id=admission.run_id,
            recording_contract_fingerprint="test.recording.v1",
            dataset_schema=MeasurementDatasetSchema(
                dataset_id="raw-measurements",
                point_domain=MeasurementProductGridPointDomain(axes=[]),
                dimensions=[
                    MeasurementDimension(id="point", kind="point", size=1),
                    MeasurementDimension(id="sample", kind="trace", size=4),
                ],
                variables=[
                    MeasurementVariable(
                        id="waveform",
                        role="observable",
                        dtype="float64",
                        dims=["point", "sample"],
                    )
                ],
            ),
            expected_record_count=1,
            record_count_limit=1,
        )
        record = MeasurementRecord(
            run_id=admission.run_id,
            logical_point_id="point-0",
            point_index=0,
            coordinates={},
            observables={
                "waveform": MeasurementArray.create(
                    dtype="float64",
                    values=(0.0, 1.0, 2.0, 3.0),
                )
            },
        )
        runtime.application.executor.initialize_measurements(
            admission.run_id,
            MeasurementHeaderCommand(lease_id=lease.lease_id, header=header),
        )
        runtime.application.executor.ingest_measurements(
            admission.run_id,
            lease_id=lease.lease_id,
            content=encode_measurement_append(
                MeasurementDatasetAppend(
                    run_id=admission.run_id,
                    header_content_hash=header.content_hash,
                    acquisition_start=0,
                    records=(record,),
                ),
                header.dataset_schema,
            ),
        )

        live = runtime.application.runs.measurement_live_preview(
            admission.run_id,
            after_record_count=None,
        )
        assert live.active
        assert live.received_record_count == 1
        assert live.durable_record_count == 0

        control = _control_run(runtime, admission.run_id)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            control = _control_run(runtime, admission.run_id)
            live = runtime.application.runs.measurement_live_preview(
                admission.run_id,
                after_record_count=None,
            )
            if control.state == "attention_required" and not live.active:
                break
            time.sleep(0.01)

        assert control.state == "attention_required"
        assert not live.active
        assert live.latest is None
        assert (
            runtime.application.runs.measurement_preview(
                admission.run_id,
                limit=10,
            ).items
            == ()
        )


def test_runtime_shutdown_unblocks_an_active_lease_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LocalDaemonRuntime(tmp_path)
    instruments = runtime.application.instruments
    supervision_started = Event()
    instrument_shutdown_started = Event()
    original_shutdown = instruments.shutdown

    def wait_for_instrument_shutdown() -> None:
        supervision_started.set()
        instrument_shutdown_started.wait()

    def shutdown_instruments() -> None:
        instrument_shutdown_started.set()
        original_shutdown()

    monkeypatch.setattr(instruments, "expire_leases", wait_for_instrument_shutdown)
    monkeypatch.setattr(instruments, "shutdown", shutdown_instruments)
    assert supervision_started.wait(timeout=5)

    close_error: BaseException | None = None

    def close_runtime() -> None:
        nonlocal close_error
        try:
            runtime.close()
        except BaseException as error:
            close_error = error

    closer = Thread(target=close_runtime)
    closer.start()
    closer.join(timeout=5)
    try:
        assert not closer.is_alive()
        assert close_error is None
    finally:
        instrument_shutdown_started.set()
        closer.join(timeout=5)


def test_runtime_shutdown_bounds_a_stuck_lease_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LocalDaemonRuntime(
        tmp_path,
        instrument_shutdown_grace=timedelta(seconds=0.05),
    )
    instruments = runtime.application.instruments
    supervision_started = Event()
    release_supervision = Event()

    def block_supervision() -> None:
        supervision_started.set()
        release_supervision.wait()

    monkeypatch.setattr(instruments, "expire_leases", block_supervision)
    assert supervision_started.wait(timeout=5)

    close_error: BaseException | None = None

    def close_runtime() -> None:
        nonlocal close_error
        try:
            runtime.close()
        except BaseException as error:
            close_error = error

    closer = Thread(target=close_runtime)
    closer.start()
    closer.join(timeout=5)
    try:
        assert not closer.is_alive()
        assert isinstance(close_error, RuntimeError)
        assert str(close_error) == "ownership lease supervisor did not stop"
        with pytest.raises(RuntimeError, match="already has a running daemon"):
            LocalDaemonRuntime(tmp_path)
    finally:
        release_supervision.set()
        closer.join(timeout=5)
        runtime.close()

    with LocalDaemonRuntime(tmp_path):
        pass


def test_runtime_exclusively_owns_one_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls = 0

    def load_factory(_spec: str, _project_root: Path) -> Never:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("factory must not run before project ownership")

    monkeypatch.setattr(
        "scopecat_server.runtime.load_bootstrap_factory",
        load_factory,
    )
    with (
        LocalDaemonRuntime(tmp_path),
        pytest.raises(RuntimeError, match="already has a running daemon"),
    ):
        LocalDaemonRuntime(tmp_path, bootstrap_spec="tests.bootstrap:create")

    assert factory_calls == 0
    with LocalDaemonRuntime(tmp_path) as reopened:
        assert reopened.application.health().status == "ok"


def test_bootstrap_config_is_active_and_idempotent_across_restarts(
    tmp_path: Path,
) -> None:
    bootstrap_calls = 0

    def bootstrap_config() -> ConfigProfileSnapshot:
        nonlocal bootstrap_calls
        bootstrap_calls += 1
        if bootstrap_calls > 1:
            raise AssertionError("an initialized registry must not resolve its seed")
        return _config()

    with LocalDaemonRuntime(tmp_path, bootstrap_config=bootstrap_config) as runtime:
        first = runtime.application.config.get_active_config().activation
        first_events = _events(runtime).items

    with LocalDaemonRuntime(tmp_path, bootstrap_config=bootstrap_config) as reopened:
        second = reopened.application.config.get_active_config().activation
        second_events = _events(reopened).items

    assert first.entry_id.startswith("daemon-")
    assert second == first
    assert [event.kind for event in first_events] == [
        "config_saved",
        "config_activated",
    ]
    assert second_events == first_events
    assert bootstrap_calls == 1


def test_bootstrap_config_does_not_replace_later_activation(
    tmp_path: Path,
) -> None:
    bootstrap = _config()
    selected = bootstrap.model_copy(update={"id": "operator-selected"})

    with LocalDaemonRuntime(tmp_path, bootstrap_config=lambda: bootstrap) as runtime:
        activation = runtime.application.config.publish_config(
            _direct_publish_command(
                config=selected,
                entry_id="operator-selected",
                actor="operator",
                expected_generation=1,
            )
        )

    with LocalDaemonRuntime(tmp_path, bootstrap_config=lambda: bootstrap) as reopened:
        state = reopened.application.config.get_active_config().activation

    assert state.entry_id == "operator-selected"
    assert state == activation.activation


def test_explicit_runtime_bootstrap_overrides_project_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_bootstrap() -> ConfigProfileSnapshot:
        raise AssertionError("explicit test config must take precedence")

    def bootstrap_factory(_root: Path) -> LabBootstrap:
        return LabBootstrap(bootstrap_config=unavailable_bootstrap)

    def load_factory(
        _spec: str,
        _project_root: Path,
    ) -> object:
        return bootstrap_factory

    monkeypatch.setattr(
        "scopecat_server.runtime.load_bootstrap_factory",
        load_factory,
    )
    explicit = _config().model_copy(update={"id": "explicit-test-bootstrap"})
    with LocalDaemonRuntime(
        tmp_path,
        bootstrap_spec="tests.bootstrap:create",
        bootstrap_config=explicit,
    ) as runtime:
        state = runtime.application.config.get_active_config().activation

    assert state.entry_content_hash == config_content_hash(explicit)


def test_config_registry_http_workflow_persists_and_publishes_events(
    tmp_path: Path,
) -> None:
    baseline = _config().model_copy(update={"id": "baseline"})
    updated = baseline.model_copy(update={"id": "updated"})
    operation_id = "activation/baseline?attempt=1"
    with LocalDaemonRuntime(tmp_path) as runtime:
        client = TestClient(runtime.app())

        empty = ConfigRegistryPage.model_validate(
            client.get("/api/v1/config-registry").json()
        )
        missing = client.get("/api/v1/config-registry/active")
        baseline_publish = client.post(
            "/api/v1/config-registry/publish-operations",
            json=_direct_publish_command(
                entry_id="baseline",
                config=baseline,
                actor="notebook",
            ).model_dump(mode="json"),
        )
        updated_publish = client.post(
            "/api/v1/config-registry/publish-operations",
            json=_direct_publish_command(
                entry_id="updated",
                config=updated,
                actor="notebook",
                expected_generation=1,
            ).model_dump(mode="json"),
        )
        activation_command = ConfigEntryActivationCommand(
            operation_id=operation_id,
            entry_id="baseline",
            actor="operator",
            expected_generation=2,
            note="restore baseline",
        )
        current_activation = client.post(
            "/api/v1/config-registry/activation-operations",
            json=activation_command.model_dump(mode="json"),
        )
        repeated_activation = client.post(
            "/api/v1/config-registry/activation-operations",
            json=activation_command.model_dump(mode="json"),
        )
        operation_lookup = client.get(
            "/api/v1/config-registry/activation-operations/"
            f"{quote(operation_id, safe='')}"
        )
        operation_conflict = client.post(
            "/api/v1/config-registry/activation-operations",
            json=activation_command.model_copy(
                update={"note": "different intent"}
            ).model_dump(mode="json"),
        )
        noop_activation = client.post(
            "/api/v1/config-registry/activation-operations",
            json=ConfigEntryActivationCommand(
                operation_id="activation:baseline-noop",
                entry_id="baseline",
                actor="operator",
                expected_generation=3,
            ).model_dump(mode="json"),
        )
        stale_activation = client.post(
            "/api/v1/config-registry/activation-operations",
            json=ConfigEntryActivationCommand(
                operation_id="activation:stale",
                entry_id="baseline",
                actor="stale-notebook",
                expected_generation=1,
            ).model_dump(mode="json"),
        )
        stale_operation_lookup = client.get(
            "/api/v1/config-registry/activation-operations/activation%3Astale"
        )
        restore_updated_command = ConfigEntryActivationCommand(
            operation_id="activation:restore-updated",
            entry_id="updated",
            actor="operator",
            expected_generation=3,
        )
        restore_updated_response = client.post(
            "/api/v1/config-registry/activation-operations",
            json=restore_updated_command.model_dump(mode="json"),
        )
        late_replay = client.post(
            "/api/v1/config-registry/activation-operations",
            json=activation_command.model_dump(mode="json"),
        )
        noop_operation_lookup = client.get(
            "/api/v1/config-registry/activation-operations/activation%3Abaseline-noop"
        )
        missing_operation = client.get(
            "/api/v1/config-registry/activation-operations/missing"
        )

        registry = ConfigRegistryPage.model_validate(
            client.get("/api/v1/config-registry").json()
        )
        activation_history = ConfigActivationPage.model_validate(
            client.get("/api/v1/config-registry/activations").json()
        )
        active = ActiveConfigView.model_validate(
            client.get("/api/v1/config-registry/active").json()
        )
        events = _events(runtime).items

        assert empty == ConfigRegistryPage()
        assert missing.status_code == 404
        assert baseline_publish.status_code == 200
        assert updated_publish.status_code == 200
        first_receipt = ConfigPublishReceipt.model_validate(baseline_publish.json())
        second_receipt = ConfigPublishReceipt.model_validate(updated_publish.json())
        assert first_receipt.entry.id == "baseline"
        assert first_receipt.activation.generation == 1
        assert second_receipt.activation.generation == 2
        activation_receipt = ConfigActivationReceipt.model_validate(
            current_activation.json()
        )
        assert activation_receipt.operation.operation_id == operation_id
        assert activation_receipt.activation.generation == 3
        assert activation_receipt.activation.entry_id == "baseline"
        assert (
            ConfigActivationReceipt.model_validate(repeated_activation.json())
            == activation_receipt
        )
        assert (
            ConfigActivationReceipt.model_validate(operation_lookup.json())
            == activation_receipt
        )
        noop_receipt = ConfigActivationReceipt.model_validate(noop_activation.json())
        assert noop_receipt.operation.activation_generation == 3
        assert noop_receipt.activation == activation_receipt.activation
        assert (
            ConfigActivationReceipt.model_validate(noop_operation_lookup.json())
            == noop_receipt
        )
        assert operation_conflict.status_code == 409
        assert stale_activation.status_code == 409
        assert stale_operation_lookup.status_code == 404
        assert missing_operation.status_code == 404
        assert (
            ConfigActivationReceipt.model_validate(late_replay.json())
            == activation_receipt
        )
        restored_updated = ConfigActivationReceipt.model_validate(
            restore_updated_response.json()
        )
        assert restored_updated.activation.action == "activation"
        assert restored_updated.activation.generation == 4
        assert restored_updated.activation.entry_id == "updated"
        assert [entry.id for entry in registry.entries] == ["updated", "baseline"]
        assert registry.activation is not None
        assert [record.action for record in activation_history.items] == [
            "activation",
            "activation",
            "activation",
            "activation",
        ]
        assert active.entry.id == "updated"
        assert active.config == updated
        assert [(event.kind, event.payload, event.run_id) for event in events] == [
            ("config_saved", {"entry_id": "baseline"}, None),
            (
                "config_activated",
                {"entry_id": "baseline", "generation": 1},
                None,
            ),
            ("config_saved", {"entry_id": "updated"}, None),
            (
                "config_activated",
                {"entry_id": "updated", "generation": 2},
                None,
            ),
            (
                "config_activated",
                {"entry_id": "baseline", "generation": 3},
                None,
            ),
            (
                "config_activated",
                {"entry_id": "updated", "generation": 4},
                None,
            ),
        ]

    with LocalDaemonRuntime(tmp_path) as reopened:
        active = reopened.application.config.get_active_config()
        operation = reopened.application.config.get_config_activation_operation(
            operation_id
        )
        events = _events(reopened).items

        assert active.entry.id == "updated"
        assert active.config == updated
        assert operation == activation_receipt
        assert events[-1].kind == "config_activated"


def test_config_publish_operation_replays_exact_receipt_across_head_changes(
    tmp_path: Path,
) -> None:
    baseline = _config().model_copy(update={"id": "publish-baseline"})
    updated = baseline.model_copy(update={"id": "publish-updated"})
    later = baseline.model_copy(update={"id": "publish-later"})
    operation_id = "publish/updated?attempt=1"
    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        client = TestClient(runtime.app())
        initial = runtime.application.config.get_active_config()
        command = ConfigPublishCommand(
            operation_id=operation_id,
            source=DirectConfigRevisionSource(config=updated),
            entry_id=updated.id,
            actor="operator",
            expected_generation=initial.activation.generation,
        )

        first = client.post(
            "/api/v1/config-registry/publish-operations",
            json=command.model_dump(mode="json"),
        )
        replay = client.post(
            "/api/v1/config-registry/publish-operations",
            json=command.model_dump(mode="json"),
        )
        lookup = client.get(
            f"/api/v1/config-registry/publish-operations/{quote(operation_id, safe='')}"
        )
        changed_intent = client.post(
            "/api/v1/config-registry/publish-operations",
            json=command.model_copy(update={"note": "different"}).model_dump(
                mode="json"
            ),
        )
        cross_kind = client.post(
            "/api/v1/config-registry/activation-operations",
            json=ConfigEntryActivationCommand(
                operation_id=operation_id,
                entry_id=initial.entry.id,
                actor="operator",
                expected_generation=2,
            ).model_dump(mode="json"),
        )
        later_receipt = runtime.application.config.publish_config(
            _direct_publish_command(
                operation_id="publish:later",
                entry_id=later.id,
                config=later,
                actor="operator",
                expected_generation=2,
            )
        )
        late_replay = client.post(
            "/api/v1/config-registry/publish-operations",
            json=command.model_dump(mode="json"),
        )
        noop_command = _direct_publish_command(
            operation_id="publish:later-noop",
            entry_id=later.id,
            config=later,
            actor="operator",
            expected_generation=3,
        )
        noop_response = client.post(
            "/api/v1/config-registry/publish-operations",
            json=noop_command.model_dump(mode="json"),
        )
        noop_lookup = client.get(
            "/api/v1/config-registry/publish-operations/publish%3Alater-noop"
        )
        stale_command = _direct_publish_command(
            operation_id="publish:stale",
            entry_id="publish-stale",
            config=baseline.model_copy(update={"id": "publish-stale"}),
            actor="operator",
            expected_generation=1,
        )
        stale = client.post(
            "/api/v1/config-registry/publish-operations",
            json=stale_command.model_dump(mode="json"),
        )
        stale_lookup = client.get(
            "/api/v1/config-registry/publish-operations/publish%3Astale"
        )

        assert first.status_code == 200
        original_receipt = ConfigPublishReceipt.model_validate(first.json())
        assert original_receipt.operation.operation_id == operation_id
        assert original_receipt.activation.generation == 2
        assert ConfigPublishReceipt.model_validate(replay.json()) == original_receipt
        assert ConfigPublishReceipt.model_validate(lookup.json()) == original_receipt
        assert changed_intent.status_code == 409
        assert cross_kind.status_code == 409
        assert later_receipt.activation.generation == 3
        assert (
            ConfigPublishReceipt.model_validate(late_replay.json()) == original_receipt
        )
        noop_receipt = ConfigPublishReceipt.model_validate(noop_response.json())
        assert noop_receipt.operation.activation_generation == 3
        assert noop_receipt.activation == later_receipt.activation
        assert ConfigPublishReceipt.model_validate(noop_lookup.json()) == noop_receipt
        assert stale.status_code == 409
        assert stale_lookup.status_code == 404
        assert [
            item.generation
            for item in runtime.application.config.get_config_activation_history().items
        ] == [3, 2, 1]
        assert "publish-stale" not in {
            item.id for item in runtime.application.config.get_config_registry().entries
        }
        assert [event.kind for event in _events(runtime).items] == [
            "config_saved",
            "config_activated",
            "config_saved",
            "config_activated",
            "config_saved",
            "config_activated",
        ]

    with LocalDaemonRuntime(tmp_path) as reopened:
        assert (
            reopened.application.config.get_config_publish_operation(operation_id)
            == original_receipt
        )


def test_inventory_migration_http_workflow_activates_only_when_drained(
    tmp_path: Path,
) -> None:
    baseline = _config()
    target = _rekeyed_config(baseline)
    command = _inventory_migration_command(target)
    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        client = TestClient(runtime.app())
        initial = runtime.application.config.get_active_config()

        response = client.post(
            "/api/v1/config-registry/instrument-inventory-migrations",
            json=command.model_dump(mode="json"),
        )

        assert response.status_code == 200
        receipt = InstrumentInventoryMigrationReceipt.model_validate(response.json())
        assert receipt.entry.id == command.entry_id
        assert receipt.activation.action == "inventory_migration"
        assert receipt.activation.generation == 2
        assert receipt.changes == command.changes
        assert runtime.application.config.get_active_config().config == target
        assert [
            (event.kind, event.payload) for event in _events(runtime).items[-2:]
        ] == [
            ("config_saved", {"entry_id": command.entry_id}),
            (
                "instrument_inventory_migrated",
                {
                    "entry_id": command.entry_id,
                    "generation": 2,
                    "change_count": 1,
                },
            ),
        ]

        restore = client.post(
            "/api/v1/config-registry/activation-operations",
            json=ConfigEntryActivationCommand(
                operation_id="restore-before-inventory-migration",
                entry_id=initial.entry.id,
                actor="operator",
                expected_generation=2,
            ).model_dump(mode="json"),
        )
        assert restore.status_code == 409
        assert runtime.application.config.get_active_config().config == target


def test_inventory_migration_reports_queued_run_as_a_blocker(
    tmp_path: Path,
) -> None:
    baseline = _config()
    target = _rekeyed_config(baseline)
    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        queued = runtime.application.submit_run(_submission("queued-blocker"))
        client = TestClient(runtime.app())

        response = client.post(
            "/api/v1/config-registry/instrument-inventory-migrations",
            json=_inventory_migration_command(target).model_dump(mode="json"),
        )

        assert response.status_code == 409
        assert queued.run_id in response.json()["detail"]
        assert "queued" in response.json()["detail"]
        assert runtime.application.config.get_active_config().config == baseline
        assert "inventory-v2" not in [
            entry.id
            for entry in runtime.application.config.get_config_registry().entries
        ]
        assert "instrument_inventory_migrated" not in {
            event.kind for event in _events(runtime).items
        }


def test_inventory_migration_final_check_catches_post_preflight_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _config()
    target = _rekeyed_config(baseline)
    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        service = runtime.application.config
        require_drained = service._require_inventory_migration_drained
        queued_run_ids: list[str] = []

        def admit_after_preflight(exclusivity_keys: tuple[str, ...]) -> None:
            require_drained(exclusivity_keys)
            queued = runtime.application.submit_run(
                _submission("post-preflight-blocker")
            )
            queued_run_ids.append(queued.run_id)

        monkeypatch.setattr(
            service,
            "_require_inventory_migration_drained",
            admit_after_preflight,
        )

        with pytest.raises(BackendConflict) as caught:
            service.migrate_instrument_inventory(_inventory_migration_command(target))

        assert len(queued_run_ids) == 1
        assert queued_run_ids[0] in str(caught.value)
        assert (
            runtime.application.executor._control.get_run(queued_run_ids[0]).state
            == "queued"
        )
        assert service.get_active_config().config == baseline
        assert "inventory-v2" not in {
            entry.id for entry in service.get_config_registry().entries
        }
        assert "instrument_inventory_migrated" not in {
            event.kind for event in _events(runtime).items
        }


def test_inventory_migration_stale_generation_does_not_begin_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _config()
    current = baseline.model_copy(update={"id": "generation-2"})
    target = _rekeyed_config(current)
    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        service = runtime.application.config
        service.publish_config(
            _direct_publish_command(
                entry_id=current.id,
                config=current,
                actor="operator",
                expected_generation=1,
            )
        )
        begin_calls = 0

        def unexpected_retirement(_keys: tuple[str, ...]) -> Never:
            nonlocal begin_calls
            begin_calls += 1
            pytest.fail("stale migration must not begin actor retirement")

        monkeypatch.setattr(
            service._actors,
            "begin_retirement",
            unexpected_retirement,
        )

        with pytest.raises(BackendConflict, match="active state changed"):
            service.migrate_instrument_inventory(
                _inventory_migration_command(target, expected_generation=1)
            )

        assert begin_calls == 0
        assert service.get_active_config().config == current
        assert "inventory-v2" not in {
            entry.id for entry in service.get_config_registry().entries
        }


def test_inventory_migration_serializes_competing_config_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _config()
    target = _rekeyed_config(baseline)
    ordinary = baseline.model_copy(update={"id": "ordinary-v2"})
    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        service = runtime.application.config
        require_drained = service._require_inventory_migration_drained
        migration_paused = Event()
        release_migration = Event()
        publish_started = Event()
        publish_finished = Event()

        def pause_after_preflight(exclusivity_keys: tuple[str, ...]) -> None:
            require_drained(exclusivity_keys)
            migration_paused.set()
            assert release_migration.wait(timeout=2)

        def publish_competing_revision() -> ConfigPublishReceipt:
            publish_started.set()
            try:
                return service.publish_config(
                    _direct_publish_command(
                        entry_id=ordinary.id,
                        config=ordinary,
                        actor="operator",
                        expected_generation=1,
                    )
                )
            finally:
                publish_finished.set()

        monkeypatch.setattr(
            service,
            "_require_inventory_migration_drained",
            pause_after_preflight,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            migration = pool.submit(
                service.migrate_instrument_inventory,
                _inventory_migration_command(target),
            )
            assert migration_paused.wait(timeout=2)
            publish = pool.submit(publish_competing_revision)
            assert publish_started.wait(timeout=2)
            try:
                assert not publish_finished.wait(timeout=0.1)
            finally:
                release_migration.set()

            migration_receipt = migration.result(timeout=2)
            with pytest.raises(BackendConflict, match="active state changed"):
                publish.result(timeout=2)

        assert migration_receipt.activation.action == "inventory_migration"
        assert service.get_active_config().config == target
        assert "ordinary-v2" not in {
            entry.id for entry in service.get_config_registry().entries
        }


def test_inventory_migration_release_before_commit_fences_old_session_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _config()
    target = _rekeyed_config(baseline)
    release_seen = Event()
    claim_started = Event()
    release_gate = InstrumentActorRetirement.release_gate

    def release_then_allow_claim(self: InstrumentActorRetirement) -> None:
        release_gate(self)
        if release_seen.is_set():
            return
        release_seen.set()
        assert claim_started.wait(timeout=2)

    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        control = runtime.application.executor._control
        active = runtime.application.config.get_active_config()

        def claim_from_old_snapshot() -> None:
            assert release_seen.wait(timeout=2)
            claim_started.set()
            control.open_instrument_session(
                operation_id="old-snapshot-open",
                actor="operator",
                config_entry_id=active.entry.id,
                config_content_hash=active.entry.content_hash,
                instrument_ids=("source-0",),
                exclusivity_keys=("source-0",),
                expected_config_generation=active.activation.generation,
                ttl=timedelta(seconds=30),
            )

        monkeypatch.setattr(
            InstrumentActorRetirement,
            "release_gate",
            release_then_allow_claim,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            claim = pool.submit(claim_from_old_snapshot)
            receipt = runtime.application.config.migrate_instrument_inventory(
                _inventory_migration_command(target)
            )
            with pytest.raises(
                ControlPlaneConflict,
                match="active configuration changed",
            ):
                claim.result(timeout=2)

        assert receipt.activation.action == "inventory_migration"
        assert control.list_instrument_sessions() == ()
        assert _resource_claims(tmp_path) == ()


def test_inventory_migration_rolls_back_and_releases_its_gate_when_event_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _config()
    target = _rekeyed_config(baseline)
    command = _inventory_migration_command(target)
    append_event = SQLiteControlPlane.append_event_in_transaction

    def fail_migration_event(
        control: SQLiteControlPlane,
        connection: sqlite3.Connection,
        event: DurableEventInput,
    ) -> DurableEvent:
        if event.kind == "instrument_inventory_migrated":
            raise RuntimeError("migration event publication failed")
        return append_event(control, connection, event)

    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteControlPlane,
                "append_event_in_transaction",
                fail_migration_event,
            )
            with pytest.raises(
                RuntimeError,
                match="migration event publication failed",
            ):
                runtime.application.config.migrate_instrument_inventory(command)

        assert runtime.application.config.get_active_config().config == baseline
        assert [
            entry.id
            for entry in runtime.application.config.get_config_registry().entries
        ] == [runtime.application.config.get_active_config().entry.id]
        assert "instrument_inventory_migrated" not in {
            event.kind for event in _events(runtime).items
        }

        receipt = runtime.application.config.migrate_instrument_inventory(command)
        assert receipt.activation.action == "inventory_migration"
        assert runtime.application.config.get_active_config().config == target


def test_config_publish_rolls_back_registry_and_event_when_event_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _direct_publish_command(
        entry_id="baseline",
        config=_config(),
        actor="notebook",
    )
    append_event = SQLiteControlPlane.append_event_in_transaction

    def fail_activation_event(
        control: SQLiteControlPlane,
        connection: sqlite3.Connection,
        event: DurableEventInput,
    ) -> DurableEvent:
        if event.kind == "config_activated":
            raise RuntimeError("event publication failed")
        return append_event(control, connection, event)

    with LocalDaemonRuntime(tmp_path) as runtime:
        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteControlPlane,
                "append_event_in_transaction",
                fail_activation_event,
            )
            with pytest.raises(RuntimeError, match="event publication failed"):
                runtime.application.config.publish_config(command)

        assert runtime.application.config.get_config_registry() == ConfigRegistryPage()
        assert _events(runtime).items == ()

        receipt = runtime.application.config.publish_config(command)

        assert receipt.activation.generation == 1
        assert [
            entry.id
            for entry in runtime.application.config.get_config_registry().entries
        ] == ["baseline"]
        assert [event.kind for event in _events(runtime).items] == [
            "config_saved",
            "config_activated",
        ]


def test_config_activation_rolls_back_when_operation_commit_fails(
    tmp_path: Path,
) -> None:
    baseline = _config().model_copy(update={"id": "operation-baseline"})
    current = baseline.model_copy(update={"id": "operation-current"})
    operation_id = "activation:rollback"
    with LocalDaemonRuntime(tmp_path, bootstrap_config=baseline) as runtime:
        service = runtime.application.config
        baseline_entry = service.get_active_config().entry
        service.publish_config(
            _direct_publish_command(
                entry_id=current.id,
                config=current,
                actor="notebook",
                expected_generation=1,
            )
        )
        command = ConfigEntryActivationCommand(
            operation_id=operation_id,
            entry_id=baseline_entry.id,
            actor="operator",
            expected_generation=2,
        )
        history_before = service.get_config_activation_history().items
        events_before = _events(runtime).items
        database = runtime.state_dir / "control.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_config_activation_operation
                BEFORE INSERT ON config_operations
                BEGIN
                    SELECT RAISE(ABORT, 'injected operation failure');
                END
                """
            )

        with pytest.raises(StorageError):
            service.activate_config_entry(command)

        active_after_failure = service.get_active_config()
        assert active_after_failure.entry.id == current.id
        assert active_after_failure.activation.generation == 2
        assert service.get_config_activation_history().items == history_before
        assert _events(runtime).items == events_before
        with pytest.raises(BackendNotFound):
            service.get_config_activation_operation(operation_id)

        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER reject_config_activation_operation")

        receipt = service.activate_config_entry(command)

        assert receipt.activation.generation == 3
        assert receipt.activation.entry_id == baseline_entry.id
        assert service.get_config_activation_operation(operation_id) == receipt
        assert len(_events(runtime).items) == len(events_before) + 1


def test_config_draft_http_workflow_previews_and_atomically_sets_default(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        active = ActiveConfigView.model_validate(
            client.get("/api/v1/config-registry/active").json()
        )
        draft = ConfigDraftCommand(
            base_entry_id=active.entry.id,
            base_content_hash=active.entry.content_hash,
            base_generation=active.activation.generation,
            candidate_id="manual-tuning",
            updates=(
                ReplaceParameter(
                    value=ScalarParameterValue(
                        id="drive_frequency",
                        value=Quantity(value=5.1, unit="GHz"),
                    )
                ),
            ),
        )

        preview_response = client.post(
            "/api/v1/config-registry/drafts/preview",
            json=draft.model_dump(mode="json"),
        )
        preview = ConfigDraftPreview.model_validate(preview_response.json())
        assert preview.result_content_hash is not None
        default_response = client.post(
            "/api/v1/config-registry/publish-operations",
            json=ConfigPublishCommand(
                operation_id="publish:manual-tuning",
                source=ManualConfigDraftRevisionSource(
                    draft=draft,
                    expected_result_content_hash=preview.result_content_hash,
                ),
                entry_id="manual-tuning",
                actor="operator",
                expected_generation=active.activation.generation,
            ).model_dump(mode="json"),
        )
        default = ConfigPublishReceipt.model_validate(default_response.json())

        assert preview_response.status_code == 200
        assert preview.valid
        assert default_response.status_code == 200
        assert default.entry.content_hash == preview.result_content_hash
        assert default.activation.entry_id == "manual-tuning"
        assert default.activation.generation == active.activation.generation + 1
        # A later revision makes the original draft base stale. Restoration must
        # select the already accepted immutable entry, not republish the draft.
        later = runtime.application.config.publish_config(
            _direct_publish_command(
                config=_config().model_copy(update={"id": "later"}),
                entry_id="later",
                actor="operator",
                expected_generation=2,
            )
        )
        detail = ConfigEntryView.model_validate(
            client.get("/api/v1/config-registry/entries/manual-tuning").json()
        )
        assert detail.latest_activation == default.activation
        command = ConfigEntryActivationCommand(
            operation_id="restore-manual-tuning",
            entry_id=default.entry.id,
            actor="operator",
            expected_generation=later.activation.generation,
            note="return to reviewed parameters",
        )
        restored_response = client.post(
            "/api/v1/config-registry/activation-operations",
            json=command.model_dump(mode="json"),
        )
        assert restored_response.status_code == 200
        restored = ConfigActivationReceipt.model_validate(restored_response.json())
        assert restored.activation.generation == 4
        assert restored.activation.restored_from_generation == 2
        assert restored.activation.entry_content_hash == default.entry.content_hash
        assert (
            runtime.application.config.get_config_entry(default.entry.id).entry
            == default.entry
        )
        replay = client.post(
            "/api/v1/config-registry/activation-operations",
            json=command.model_dump(mode="json"),
        )
        assert ConfigActivationReceipt.model_validate(replay.json()) == restored
        stale = client.post(
            "/api/v1/config-registry/activation-operations",
            json=command.model_copy(
                update={"operation_id": "stale-restore"}
            ).model_dump(mode="json"),
        )
        assert stale.status_code == 409
        assert [
            item.generation
            for item in runtime.application.config.get_config_activation_history().items
        ] == [4, 3, 2, 1]

    with LocalDaemonRuntime(tmp_path) as reopened:
        assert (
            reopened.application.config.get_config_activation_operation(
                "restore-manual-tuning"
            )
            == restored
        )
        assert (
            reopened.application.config.get_config_entry(
                "manual-tuning"
            ).latest_activation
            == restored.activation
        )
        active = reopened.application.config.get_active_config()
        parameter = active.config.parameter_snapshot.get("drive_frequency")

        assert active.entry.id == "manual-tuning"
        assert isinstance(parameter, ScalarParameterValue)
        assert parameter.value == Quantity(value=5.1, unit="GHz")


def test_admission_is_durably_idempotent(tmp_path: Path) -> None:
    submission = _submission()
    state = tmp_path / ".scopecat"
    database = state / "control.sqlite3"
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        admission_services: list[AdmissionService] = []
        for _ in range(2):
            sqlite = SQLiteDatabase(database)
            runs = SQLiteRunRepository(sqlite, state / "objects")
            registry = SQLiteConfigRegistryStore(sqlite, runs=runs)
            control = SQLiteControlPlane(sqlite)
            sample_store = SQLiteSampleStore(sqlite, control=control)
            admission_services.append(
                AdmissionService(
                    control=control,
                    runs=runs,
                    services=ProjectStateServices(
                        runs=runs,
                        config_registry=registry.read_unit_of_work,
                    ),
                    point_plans=RunPointPlanService(control=control, runs=runs),
                    samples=SampleService(
                        sample_store, ImmutableObjectStore(state / "objects")
                    ),
                    sample_store=sample_store,
                )
            )
        services = tuple(admission_services)
        barrier = Barrier(len(services))

        def submit(service: AdmissionService) -> RunAdmission:
            barrier.wait()
            return service.submit_run(submission)

        with ThreadPoolExecutor(max_workers=len(services)) as pool:
            admissions = tuple(pool.map(submit, services))

        retry = client.post(
            "/api/v1/runs",
            json=submission.model_dump(mode="json"),
        )
        changed = client.post(
            "/api/v1/runs",
            json=submission.model_copy(
                update={"request": RunRequest(metadata={"changed": True})}
            ).model_dump(mode="json"),
        )

        assert admissions[0] == admissions[1]
        assert retry.status_code == 201
        assert RunAdmission.model_validate(retry.json()) == admissions[0]
        assert changed.status_code == 409
        run_id = admissions[0].run_id
        published_run_ids = [
            manifest.run_id for manifest in list_test_runs(_run_repository(tmp_path))
        ]
        assert published_run_ids == [run_id]
        assert _snapshot(runtime, run_id).outcome is None
        listed = client.get("/api/v1/runs").json()
        assert len(listed["items"]) == 1
        assert listed["items"][0]["control"]["state"] == "queued"
        assert listed["items"][0]["snapshot"]["run_id"] == run_id
        assert "manifest" not in listed["items"][0]
        detail = client.get(f"/api/v1/runs/{run_id}").json()
        assert detail["snapshot"]["run_id"] == run_id
        assert "contents" not in detail["snapshot"]
        contents = client.get(f"/api/v1/runs/{run_id}/contents")
        missing_content = client.get(f"/api/v1/runs/{run_id}/contents/artifact/missing")
        assert contents.status_code == 200
        assert contents.json() == {
            "run_id": run_id,
            "items": [],
            "next_cursor": None,
        }
        assert missing_content.status_code == 404
        assert [
            event.kind
            for event in _events(runtime, run_id=run_id).items
            if event.kind == "run_admitted"
        ] == ["run_admitted"]

    with LocalDaemonRuntime(tmp_path) as reopened:
        persisted = reopened.application.submit_run(submission)
        assert persisted.run_id == run_id


@pytest.mark.parametrize(
    "instrument_update",
    [
        {"exclusivity_key": "alternate-source"},
        {"driver_id": "alternate.driver"},
        {
            "connection": TcpipSocketInstrumentConnection(
                host="127.0.0.1",
                port=5025,
            )
        },
    ],
)
def test_client_planned_admission_rejects_instrument_inventory_changes(
    tmp_path: Path,
    instrument_update: dict[str, object],
) -> None:
    authoritative = _config()
    [instrument] = authoritative.instrument_registry.instruments
    registry = authoritative.instrument_registry.model_copy(
        update={"instruments": [instrument.model_copy(update=instrument_update)]}
    )
    submitted = authoritative.model_copy(
        update={
            "system": authoritative.system.model_copy(
                update={"instrument_registry": registry}
            )
        }
    )
    submission = _submission("changed-inventory").model_copy(
        update={"config": submitted}
    )

    with LocalDaemonRuntime(
        tmp_path,
        bootstrap_config=authoritative,
    ) as runtime:
        with pytest.raises(BackendConflict, match="instrument inventory differs"):
            runtime.application.submit_run(submission)

        assert (
            runtime.application.runs.list_runs(
                limit=10,
                before=None,
                state=None,
            ).items
            == ()
        )


def test_config_publish_rejects_rekey_with_a_queued_run(tmp_path: Path) -> None:
    config = _config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        queued = runtime.application.submit_run(_submission("queued-before-rekey"))
        active = runtime.application.config.get_active_config()
        [instrument] = config.instrument_registry.instruments
        rekeyed_registry = config.instrument_registry.model_copy(
            update={
                "instruments": [
                    instrument.model_copy(
                        update={"exclusivity_key": "alternate-source"}
                    )
                ]
            }
        )
        rekeyed = config.model_copy(
            update={
                "id": "rekeyed",
                "system": config.system.model_copy(
                    update={"instrument_registry": rekeyed_registry}
                ),
            }
        )

        with pytest.raises(
            BackendConflict,
            match="cannot change its exclusivity key",
        ):
            runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id="publish:rekeyed",
                    source=DirectConfigRevisionSource(config=rekeyed),
                    entry_id="rekeyed",
                    actor="operator",
                    expected_generation=active.activation.generation,
                )
            )

        current = runtime.application.config.get_active_config()
        assert current.activation == active.activation
        assert (
            runtime.application.executor._control.get_run(queued.run_id).state
            == "queued"
        )


def test_admission_fences_an_activation_after_active_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        admission = runtime.application._admission
        resolve_active = admission._resolve_active_config

        def resolve_then_activate() -> ActiveConfigRegistrySnapshot:
            resolved = resolve_active()
            runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id="publish:activated-during-submit",
                    source=DirectConfigRevisionSource(
                        config=config.model_copy(
                            update={"id": "activated-during-submit"}
                        )
                    ),
                    entry_id="activated-during-submit",
                    actor="operator",
                    expected_generation=resolved.activation.generation,
                )
            )
            return resolved

        monkeypatch.setattr(
            admission,
            "_resolve_active_config",
            resolve_then_activate,
        )

        with pytest.raises(BackendConflict, match="active configuration changed"):
            runtime.application.submit_run(_submission("activation-race"))

        assert (
            runtime.application.runs.list_runs(
                limit=10,
                before=None,
                state=None,
            ).items
            == ()
        )
        assert list_test_runs(_run_repository(tmp_path)) == []


def test_registry_admission_replays_but_uses_current_inventory_for_new_runs(
    tmp_path: Path,
) -> None:
    config = _config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        active = runtime.application.config.get_active_config()
        source = ConfigRegistryRunConfigSource(
            selector="active",
            entry_id=active.entry.id,
            config_ref=active.entry.config_ref,
            content_hash=active.entry.content_hash,
            registry_generation=active.activation.generation,
        )
        submission = _submission("registry-source").model_copy(
            update={"config_source": source}
        )
        admitted = runtime.application.submit_run(submission)

        [instrument] = config.instrument_registry.instruments
        changed_registry = config.instrument_registry.model_copy(
            update={
                "instruments": [
                    instrument.model_copy(update={"driver_id": "alternate.driver"})
                ]
            }
        )
        changed = config.model_copy(
            update={
                "id": "changed-inventory",
                "system": config.system.model_copy(
                    update={"instrument_registry": changed_registry}
                ),
            }
        )
        runtime.application.config.publish_config(
            ConfigPublishCommand(
                operation_id="publish:changed-inventory",
                source=DirectConfigRevisionSource(config=changed),
                entry_id="changed-inventory",
                actor="operator",
                expected_generation=active.activation.generation,
            )
        )

        assert runtime.application.submit_run(submission) == admitted
        with pytest.raises(BackendConflict, match="instrument inventory differs"):
            runtime.application.submit_run(
                submission.model_copy(
                    update={"submission_id": "historical-registry-source"}
                )
            )
        current = runtime.application.submit_run(
            _submission("current-active-inventory").model_copy(
                update={"config": changed}
            )
        )
        control = runtime.application.executor._control.get_run(current.run_id)
        assert control.admission.resource_claims == (
            ResourceKey(kind="instrument", id="source-0"),
        )

        with pytest.raises(BackendConflict, match="does not match its registry entry"):
            runtime.application.submit_run(
                submission.model_copy(
                    update={
                        "submission_id": "forged-registry-source",
                        "config_source": source.model_copy(
                            update={"config_ref": "forged-config-ref"}
                        ),
                    }
                )
            )


def test_authority_failure_replays_a_concurrently_admitted_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    submission = _submission("concurrent-authority-change")
    state = tmp_path / ".scopecat"
    database = state / "control.sqlite3"
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        sqlite = SQLiteDatabase(database)
        runs = SQLiteRunRepository(sqlite, state / "objects")
        registry = SQLiteConfigRegistryStore(sqlite, runs=runs)
        control = SQLiteControlPlane(sqlite)
        sample_store = SQLiteSampleStore(sqlite, control=control)
        racing = AdmissionService(
            control=control,
            runs=runs,
            services=ProjectStateServices(
                runs=runs,
                config_registry=registry.read_unit_of_work,
            ),
            point_plans=RunPointPlanService(control=control, runs=runs),
            samples=SampleService(
                sample_store, ImmutableObjectStore(state / "objects")
            ),
            sample_store=sample_store,
        )
        resolve_active = racing._resolve_active_config
        admitted: RunAdmission | None = None

        def resolve_after_competing_admission() -> ActiveConfigRegistrySnapshot:
            nonlocal admitted
            admitted = runtime.application.submit_run(submission)
            active = runtime.application.config.get_active_config()
            [instrument] = config.instrument_registry.instruments
            changed_registry = config.instrument_registry.model_copy(
                update={
                    "instruments": [
                        instrument.model_copy(update={"driver_id": "alternate.driver"})
                    ]
                }
            )
            runtime.application.config.publish_config(
                ConfigPublishCommand(
                    operation_id="publish:concurrent-inventory-change",
                    source=DirectConfigRevisionSource(
                        config=config.model_copy(
                            update={
                                "id": "concurrent-inventory-change",
                                "system": config.system.model_copy(
                                    update={"instrument_registry": changed_registry}
                                ),
                            }
                        )
                    ),
                    entry_id="concurrent-inventory-change",
                    actor="operator",
                    expected_generation=active.activation.generation,
                )
            )
            return resolve_active()

        monkeypatch.setattr(
            racing,
            "_resolve_active_config",
            resolve_after_competing_admission,
        )

        replayed = racing.submit_run(submission)

        assert admitted is not None
        assert replayed == admitted


def test_admission_canonicalizes_domain_only_instrument_claims(
    tmp_path: Path,
) -> None:
    config = _domain_only_config()
    target = config.domain_target
    assert target is not None
    logical_requirements = (RunResourceRequirement(id="source-0", kind="instrument"),)
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        admitted = runtime.application.submit_run(
            _domain_only_submission(
                config,
                submission_id="domain-canonical",
                requirements=logical_requirements,
            )
        )
        control = runtime.application.executor._control.get_run(admitted.run_id)
        public = runtime.application.runs.get_run(admitted.run_id)

    assert control.admission.plan.run_resource_requirements == logical_requirements
    assert control.admission.resource_claims == (
        ResourceKey(id="rack-a/source", kind="instrument"),
    )
    assert tuple(item.resource.id for item in public.resources) == ("source-0",)
    public_control = public.control.model_dump(mode="json")
    assert set(public_control["admission"]) == {
        "run_id",
        "run_contract_fingerprint",
        "plan",
        "display_name",
        "tags",
        "description",
        "admitted_at",
    }
    assert set(public_control["admission"]["plan"]) == {
        "experiment_id",
        "experiment_kind",
        "point_plan_fingerprint",
        "measurement_contract_fingerprint",
        "point_count",
        "initial_point_count",
        "point_limit",
        "adaptive_coordinate_ids",
        "adaptive_scope",
        "per_region_point_limit",
        "adaptive_region_count",
        "adaptive_regions",
        "adaptive_regions_truncated",
        "coordinates",
        "sampled_points",
        "sampled_points_truncated",
        "record_ids",
        "run_resource_requirements",
    }
    assert "rack-a/source" not in str(public_control)


def test_admission_rejects_invalid_domain_only_requirements(
    tmp_path: Path,
) -> None:
    config = _domain_only_config()
    target = config.domain_target
    assert target is not None
    requirements = (
        RunResourceRequirement(id="source-0", kind="instrument"),
        RunResourceRequirement(id="rack-a/source", kind="instrument"),
    )
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        with pytest.raises(BackendConflict, match="unknown instruments"):
            runtime.application.submit_run(
                _domain_only_submission(
                    config,
                    submission_id="domain-invalid-instrument",
                    requirements=requirements,
                )
            )

        assert (
            runtime.application.runs.list_runs(
                limit=10,
                before=None,
                state=None,
            ).items
            == ()
        )


@pytest.mark.parametrize(
    "target_update",
    [
        {"id": "tests.forged-target"},
        {"kind": "tests.forged-domain"},
        {"instrument_ids": []},
    ],
)
def test_admission_rejects_domain_requirement_outside_active_authority(
    tmp_path: Path,
    target_update: dict[str, object],
) -> None:
    config = _domain_only_config()
    target = config.domain_target
    assert target is not None
    submitted = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"domain_target": target.model_copy(update=target_update)}
            )
        }
    )
    submitted_target = submitted.domain_target
    assert submitted_target is not None
    requirements = (RunResourceRequirement(id="source-0", kind="instrument"),)
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime,
        pytest.raises(
            BackendConflict,
            match="differs from the active configuration",
        ),
    ):
        runtime.application.submit_run(
            _domain_only_submission(
                submitted,
                submission_id="domain-invalid-authority",
                requirements=requirements,
            )
        )


def test_post_run_analysis_policy_acceptance_and_candidate_activation_closed_loop(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        admission = runtime.application.submit_run(_submission("post-run-loop"))
        proposal = _analysis_proposal(admission.run_id)
        analysis_command = _analysis_command(proposal)
        analysis_url = f"/api/v1/runs/{admission.run_id}/analyses"
        first_save = client.post(
            analysis_url,
            json=analysis_command.model_dump(mode="json"),
        )
        retry_save = client.post(
            analysis_url,
            json=analysis_command.model_dump(mode="json"),
        )
        analyses = client.get(analysis_url)
        analysis_detail = client.get(f"{analysis_url}/{analysis_command.analysis_key}")
        analysis_record = client.get(
            f"/api/v1/runs/{admission.run_id}/records/"
            f"analysis-{analysis_command.analysis_key}-r1/json",
            params={"expected_kind": "analysis"},
        )
        dataset_bytes = RunDatasetBytesView.model_validate(
            client.get(
                f"/api/v1/runs/{admission.run_id}/datasets/analysis-fit-r1-fits/bytes",
                params={"expected_kind": "analysis_dataset"},
            ).json()
        )
        analysis_artifact = client.get(
            f"/api/v1/runs/{admission.run_id}/artifacts/"
            "analysis-fit-r1-fit-report/text",
            params={"expected_kind": "analysis_artifact"},
        )
        attachment_command = RunAttachmentCommand(
            key="notebook-notes",
            text="operator notes",
            filename="notes.md",
            media_type="text/markdown",
        )
        attachment = client.post(
            f"/api/v1/runs/{admission.run_id}/attachments",
            json=attachment_command.model_dump(mode="json"),
        )
        attachment_text = client.get(
            f"/api/v1/runs/{admission.run_id}/artifacts/notebook-notes/text",
            params={"expected_kind": "attachment"},
        )
        config = RunConfigView.model_validate(
            client.get(f"/api/v1/runs/{admission.run_id}/config").json()
        )
        proposals = ParameterProposalPage.model_validate(
            client.get(f"/api/v1/runs/{admission.run_id}/parameter-proposals").json()
        )
        exact_proposal = ParameterProposalView.model_validate(
            client.get(
                f"/api/v1/runs/{admission.run_id}/parameter-proposals/{proposal.id}"
            ).json()
        )
        activated = client.post(
            "/api/v1/config-registry/publish-operations",
            json=ConfigPublishCommand(
                operation_id="publish:candidate-fit",
                source=CandidateConfigRevisionSource(
                    run_id=admission.run_id,
                    proposal_id=proposal.id,
                    acceptance=ManualCandidateAcceptance(),
                ),
                entry_id="candidate-fit",
                actor="nightly-calibration",
                expected_generation=1,
                note="fit evidence reviewed",
            ).model_dump(mode="json"),
        )
        approved_proposals = ParameterProposalPage.model_validate(
            client.get(f"/api/v1/runs/{admission.run_id}/parameter-proposals").json()
        )

        saved = AnalysisSaveReceipt.model_validate(first_save.json())
        retry = AnalysisSaveReceipt.model_validate(retry_save.json())
        activation = ConfigPublishReceipt.model_validate(activated.json())
        approval = approved_proposals.items[0].approval
        assert approval is not None
        events = _events(runtime, run_id=admission.run_id).items

        assert first_save.status_code == 201
        assert retry == saved
        assert exact_proposal.proposal == proposal
        assert analyses.json()["items"][0]["key"] == "fit"
        assert analysis_detail.json()["entry"]["id"] == "analysis-fit-r1"
        assert analysis_record.json()["content"]["title"] == "fit"
        persisted_outputs = analysis_record.json()["content"]["outputs"]
        assert persisted_outputs[0]["content"]["preview"] == {
            "columns": [{"id": "bias", "label": None, "unit": None}],
            "rows": [{"cells": [1.0]}, {"cells": [2.0]}],
        }
        assert persisted_outputs[0]["content"]["total_rows"] == 2
        assert not persisted_outputs[0]["content"]["truncated"]
        assert persisted_outputs[1]["content"]["dataset_id"] == "analysis-fit-r1-fits"
        restored_dataset = DerivedDataset.from_arrow_ipc(
            dataset_bytes.content_bytes(),
            schema=DerivedDataset.from_payload(
                cast(
                    "AnalysisDatasetOutputPayload", analysis_command.outputs[1]
                ).content
            ).schema,
        )
        assert restored_dataset.table.to_pylist() == [
            {"bias": 1.0, "signal": 3.0},
            {"bias": 2.0, "signal": 4.0},
        ]
        assert persisted_outputs[2]["content"]["layers"][0]["preview"]["series"][0] == {
            "id": "signal",
            "label": "signal",
            "x": [1.0, 2.0],
            "y": [3.0, 4.0],
            "y_lower": None,
            "y_upper": None,
        }
        assert persisted_outputs[2]["content"]["total_points"] == 2
        assert not persisted_outputs[2]["content"]["truncated"]
        assert persisted_outputs[3]["content"]["proposal_id"] == proposal.id
        assert persisted_outputs[4]["content"]["artifact_id"] == (
            "analysis-fit-r1-fit-report"
        )
        assert analysis_artifact.json()["content"] == "# Fit report\n"
        assert attachment.json()["filename"] == "notes.md"
        assert attachment_text.json()["content"] == "operator notes\n"
        assert config.config == _config()
        assert proposals.items[0].proposal == proposal
        assert proposals.items[0].approval is None
        assert approval.actor == "nightly-calibration"
        assert approved_proposals.items[0].approval == approval
        assert activation.entry.id == "candidate-fit"
        assert activation.activation.generation == 2
        assert [
            event.kind
            for event in events
            if event.kind
            in {
                "analysis_saved",
                "parameter_proposal_approved",
                "config_activated",
            }
        ] == [
            "analysis_saved",
            "parameter_proposal_approved",
            "config_activated",
        ]


def test_run_analysis_history_is_paged_and_logical_keys_resolve_latest(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        admission = runtime.application.submit_run(_submission("analysis-history"))
        analysis_url = f"/api/v1/runs/{admission.run_id}/analyses"
        for revision in range(1, 4):
            command = AnalysisSaveCommand(
                title=f"History revision {revision}",
                analysis_key="history",
                outputs=(
                    AnalysisArtifactOutputPayload(
                        kind="artifact",
                        id="note",
                        title="Revision note",
                        content_base64=b64encode(
                            f"revision {revision}".encode()
                        ).decode(),
                        filename="note.txt",
                        media_type="text/plain",
                    ),
                ),
            )
            response = client.post(
                analysis_url,
                json=command.model_dump(mode="json"),
            )
            assert response.status_code == 201

        head = RunAnalysisPage.model_validate(
            client.get(analysis_url, params={"limit": 2}).json()
        )
        tail = RunAnalysisPage.model_validate(
            client.get(
                analysis_url,
                params={"limit": 2, "before": head.next_cursor},
            ).json()
        )
        latest = RunAnalysisView.model_validate(
            client.get(f"{analysis_url}/history").json()
        )
        exact = RunAnalysisView.model_validate(
            client.get(f"{analysis_url}/analysis-history-r1").json()
        )

        assert [item.entry.id for item in head.items] == [
            "analysis-history-r3",
            "analysis-history-r2",
        ]
        assert head.next_cursor is not None
        assert [item.entry.id for item in tail.items] == ["analysis-history-r1"]
        assert tail.next_cursor is None
        assert latest.entry.id == "analysis-history-r3"
        assert latest.analysis.revision == 3
        assert exact.analysis.revision == 1


def test_run_analysis_rejects_missing_measurement_input_content(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        admission = runtime.application.submit_run(
            _submission("analysis-missing-measurement-input")
        )
        command = AnalysisSaveCommand(
            title="Forged input",
            analysis_key="forged-input",
            inputs=(
                MeasurementAnalysisInputPayload(
                    id="measurement",
                    run_id=admission.run_id,
                    target="missing-measurements",
                    content_hash="sha256:missing",
                    codec="scopecat.measurement-dataset.v12",
                    role="data",
                ),
            ),
        )

        response = client.post(
            f"/api/v1/runs/{admission.run_id}/analyses",
            json=command.model_dump(mode="json"),
        )

        assert response.status_code == 409
        assert "analysis_input_content_mismatch" in response.json()["detail"]


def test_run_analysis_allocates_distinct_revisions_for_concurrent_saves(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        admission = runtime.application.submit_run(
            _submission("concurrent-run-analysis")
        )
        commands = tuple(
            AnalysisSaveCommand(
                title=f"Concurrent analysis {ordinal}",
                analysis_key="concurrent-analysis",
            )
            for ordinal in range(2)
        )
        ready = Barrier(len(commands))

        def save(command: AnalysisSaveCommand) -> AnalysisSaveReceipt:
            ready.wait()
            response = client.post(
                f"/api/v1/runs/{admission.run_id}/analyses",
                json=command.model_dump(mode="json"),
            )
            assert response.status_code == 201
            return AnalysisSaveReceipt.model_validate(response.json())

        with ThreadPoolExecutor(max_workers=len(commands)) as executor:
            saved = tuple(executor.map(save, commands))

        assert {item.record.id for item in saved} == {
            "analysis-concurrent-analysis-r1",
            "analysis-concurrent-analysis-r2",
        }
        page = runtime.application.runs.list_run_analyses(admission.run_id)
        assert len(page.items) == 2


def test_analysis_publication_rolls_back_refs_index_and_event_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(_submission("analysis-atomic"))
        proposal = _analysis_proposal(admission.run_id)
        command = _analysis_command(proposal)
        before = _run_state(runtime, admission.run_id)
        append_event = SQLiteControlPlane.append_event_in_transaction

        def fail_analysis_event(
            control: SQLiteControlPlane,
            connection: sqlite3.Connection,
            event: DurableEventInput,
        ) -> DurableEvent:
            if event.kind == "analysis_saved":
                raise RuntimeError("analysis event publication failed")
            return append_event(control, connection, event)

        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteControlPlane,
                "append_event_in_transaction",
                fail_analysis_event,
            )
            with pytest.raises(
                RuntimeError,
                match="analysis event publication failed",
            ):
                runtime.application.runs.save_run_analysis(
                    admission.run_id,
                    command,
                )

        repository = _run_repository(tmp_path)
        assert _run_state(runtime, admission.run_id) == before
        assert runtime.application.runs.list_run_analyses(admission.run_id).items == ()
        assert (
            runtime.application.runs.list_parameter_proposals(admission.run_id).items
            == ()
        )
        assert not repository.exists(
            admission.run_id,
            record_content_ref(record_id="analysis-fit-r1", kind="analysis"),
        )
        assert not repository.exists(
            admission.run_id,
            dataset_content_ref(
                dataset_id="analysis-fit-r1-fits",
                kind="analysis_dataset",
            ),
        )
        assert [
            event.kind
            for event in _events(runtime, run_id=admission.run_id).items
            if event.kind == "analysis_saved"
        ] == []

        saved = runtime.application.runs.save_run_analysis(
            admission.run_id,
            command,
        )

        assert saved.record.id == "analysis-fit-r1"
        assert (
            len(runtime.application.runs.list_run_analyses(admission.run_id).items) == 1
        )
        assert [
            event.kind
            for event in _events(runtime, run_id=admission.run_id).items
            if event.kind == "analysis_saved"
        ] == ["analysis_saved"]


def test_candidate_publish_rolls_back_approval_with_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(_submission("decision-atomic"))
        proposal = _analysis_proposal(admission.run_id)
        runtime.application.runs.save_run_analysis(
            admission.run_id,
            _analysis_command(proposal),
        )
        command = ConfigPublishCommand(
            operation_id="publish:candidate-atomic",
            source=CandidateConfigRevisionSource(
                run_id=admission.run_id,
                proposal_id=proposal.id,
                acceptance=ManualCandidateAcceptance(),
            ),
            entry_id="candidate-atomic",
            actor="nightly-calibration",
            expected_generation=1,
        )
        before = _run_state(runtime, admission.run_id)
        events_before = _events(runtime, run_id=admission.run_id).items
        append_event = SQLiteControlPlane.append_event_in_transaction

        def fail_decision_event(
            control: SQLiteControlPlane,
            connection: sqlite3.Connection,
            event: DurableEventInput,
        ) -> DurableEvent:
            if event.kind == "parameter_proposal_approved":
                raise RuntimeError("approval event publication failed")
            return append_event(control, connection, event)

        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteControlPlane,
                "append_event_in_transaction",
                fail_decision_event,
            )
            with pytest.raises(
                RuntimeError,
                match="approval event publication failed",
            ):
                runtime.application.config.publish_config(command)

        proposals = runtime.application.runs.list_parameter_proposals(admission.run_id)
        assert _run_state(runtime, admission.run_id) == before
        assert proposals.items[0].approval is None
        assert [
            entry.id
            for entry in runtime.application.config.get_config_registry().entries
            if entry.id == "candidate-atomic"
        ] == []
        assert [
            event.kind
            for event in _events(runtime, run_id=admission.run_id).items
            if event.kind == "parameter_proposal_approved"
        ] == []
        assert _events(runtime, run_id=admission.run_id).items == events_before
        with pytest.raises(BackendNotFound):
            runtime.application.config.get_config_publish_operation(
                command.operation_id
            )

        database = runtime.state_dir / "control.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_config_publish_operation
                BEFORE INSERT ON config_operations
                WHEN NEW.kind = 'publish_revision'
                BEGIN
                    SELECT RAISE(ABORT, 'injected publish operation failure');
                END
                """
            )

        with pytest.raises(StorageError):
            runtime.application.config.publish_config(command)

        proposals = runtime.application.runs.list_parameter_proposals(admission.run_id)
        assert _run_state(runtime, admission.run_id) == before
        assert proposals.items[0].approval is None
        assert _events(runtime, run_id=admission.run_id).items == events_before
        assert "candidate-atomic" not in {
            entry.id
            for entry in runtime.application.config.get_config_registry().entries
        }
        with pytest.raises(BackendNotFound):
            runtime.application.config.get_config_publish_operation(
                command.operation_id
            )

        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER reject_config_publish_operation")

        receipt = runtime.application.config.publish_config(command)
        proposals = runtime.application.runs.list_parameter_proposals(admission.run_id)

        assert proposals.items[0].approval is not None
        assert receipt.entry.id == "candidate-atomic"
        assert (
            runtime.application.config.get_config_publish_operation(
                command.operation_id
            )
            == receipt
        )
        assert [
            event.kind
            for event in _events(runtime, run_id=admission.run_id).items
            if event.kind == "parameter_proposal_approved"
        ] == ["parameter_proposal_approved"]


def test_executor_start_is_atomic_idempotent_and_quiet_when_resources_busy(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        first = runtime.application.submit_run(_submission("executor-first"))
        request = ExecutorStartRequest(
            executor_id="notebook-1",
        )

        started = runtime.application.executor.start_executor(first.run_id, request)
        retry = runtime.application.executor.start_executor(first.run_id, request)
        [segment] = runtime.application.executor.execution_segments(first.run_id).items
        events_before_heartbeat = _events(runtime, run_id=first.run_id).items
        renewed = runtime.application.executor.heartbeat_executor(
            first.run_id,
            ExecutorHeartbeat(
                lease_id=started.lease_id,
            ),
        )

        assert retry == started
        assert segment.segment_id == started.segment_id
        assert segment.ordinal == 0
        assert segment.start_point_count == 0
        assert segment.result is None
        assert renewed.expires_at > started.expires_at
        assert _events(runtime, run_id=first.run_id).items == events_before_heartbeat
        assert (
            len(
                [
                    event
                    for event in _events(runtime, run_id=first.run_id).items
                    if event.kind == "executor_lease_granted"
                ]
            )
            == 1
        )

        waiting = runtime.application.submit_run(_submission("executor-waiting"))
        with pytest.raises(BackendConflict, match="resources are busy"):
            runtime.application.executor.start_executor(
                waiting.run_id,
                ExecutorStartRequest(
                    executor_id="notebook-2",
                ),
            )

        assert _control_run(runtime, waiting.run_id).state == "queued"
        assert _snapshot(runtime, waiting.run_id).outcome is None
        assert [
            event.kind for event in _events(runtime, run_id=waiting.run_id).items
        ] == ["run_admitted"]


def test_queued_run_reports_owner_and_cancellation_does_not_touch_it(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        executor = runtime.application.executor
        owner = runtime.application.submit_run(_submission("visible-owner"))
        lease = executor.start_executor(
            owner.run_id, ExecutorStartRequest(executor_id="owner")
        )
        waiting = runtime.application.submit_run(_submission("visible-waiter"))
        resource = runtime.application.runs.get_run(waiting.run_id).resources[0]
        assert resource.status == "blocked"
        assert resource.blocked_by is not None
        assert resource.blocked_by.owner_kind == "run"
        assert resource.blocked_by.owner_id == owner.run_id
        assert resource.blocked_by.status == "active"
        assert resource.expires_at is None
        executor.cancel_run(waiting.run_id)
        closed = runtime.application.runs.get_run(waiting.run_id).resources[0]
        assert closed.status == "released" and closed.blocked_by is None
        assert (
            executor.start_executor(
                owner.run_id, ExecutorStartRequest(executor_id="owner")
            )
            == lease
        )
        assert _control_run(runtime, owner.run_id).cancellation_requested_at is None

        next_waiter = runtime.application.submit_run(_submission("next-waiter"))
        executor.commit_terminal(
            owner.run_id,
            TerminalRunCommitCommand(
                lease_id=lease.lease_id,
                outcome=RunOutcome(
                    run_id=owner.run_id,
                    result="failed",
                    certainty="known",
                    finished_at=datetime.now(tz=UTC),
                    problems=(
                        problem(
                            "test_owner_finished",
                            "owner finished",
                            phase=ProblemPhase.EXECUTION,
                        ),
                    ),
                ),
            ),
        )
        available = runtime.application.runs.get_run(next_waiter.run_id).resources[0]
        assert available.status == "required" and available.blocked_by is None
        executor.start_executor(
            next_waiter.run_id, ExecutorStartRequest(executor_id="next")
        )
        acquired = runtime.application.runs.get_run(next_waiter.run_id).resources[0]
        assert acquired.status == "active" and acquired.blocked_by is None


def test_queued_run_reports_quarantined_owner_after_restart(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        owner = runtime.application.submit_run(_submission("restart-owner"))
        runtime.application.executor.start_executor(
            owner.run_id, ExecutorStartRequest(executor_id="owner")
        )
        waiting = runtime.application.submit_run(_submission("restart-waiter"))
    with LocalDaemonRuntime(tmp_path) as reopened:
        detail = reopened.application.runs.get_run(waiting.run_id)
        assert detail.control.state == "queued"
        blocker = detail.resources[0].blocked_by
        assert detail.resources[0].status == "blocked"
        assert blocker is not None and blocker.owner_id == owner.run_id
        assert blocker.status == "quarantined"
        assert _control_run(reopened, owner.run_id).state == "attention_required"


def test_queued_run_reports_interactive_session_blocker(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        active = runtime.application.config.get_active_config()
        session = runtime.application.executor._control.open_instrument_session(
            operation_id="visible-session",
            actor="operator",
            config_entry_id=active.entry.id,
            config_content_hash=active.entry.content_hash,
            instrument_ids=("source-0",),
            exclusivity_keys=("source-0",),
            expected_config_generation=active.activation.generation,
            ttl=timedelta(seconds=30),
        )
        waiting = runtime.application.submit_run(_submission("session-waiter"))
        resource = runtime.application.runs.get_run(waiting.run_id).resources[0]
        assert resource.status == "blocked"
        assert resource.blocked_by is not None
        assert resource.blocked_by.owner_kind == "instrument_session"
        assert resource.blocked_by.owner_id == session.session_id


def test_resource_rejection_closes_only_the_unstarted_contender(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        executor = runtime.application.executor
        owner = runtime.application.submit_run(_submission("busy-owner"))
        owner_request = ExecutorStartRequest(
            executor_id="owner", on_resource_busy="fail"
        )
        lease = executor.start_executor(owner.run_id, owner_request)
        contender = runtime.application.submit_run(_submission("busy-contender"))

        with pytest.raises(BackendConflict, match="resources are busy"):
            executor.start_executor(
                contender.run_id,
                ExecutorStartRequest(
                    executor_id="contender",
                    on_resource_busy="fail",
                ),
            )

        assert _control_run(runtime, contender.run_id).state == "closed"
        outcome = _snapshot(runtime, contender.run_id).outcome
        assert outcome is not None and outcome.result == "failed"
        assert outcome.certainty == "known"
        assert outcome.problems[0].code == "run_resources_busy"
        assert executor.execution_segments(contender.run_id).items == ()
        assert executor.run_coverage(contender.run_id).completed_point_count == 0
        assert executor.start_executor(owner.run_id, owner_request) == lease
        assert _control_run(runtime, owner.run_id).cancellation_requested_at is None

        # A different executor cannot turn an already-owned run into a failure.
        with pytest.raises(BackendConflict, match="different executor intent"):
            executor.start_executor(
                owner.run_id,
                ExecutorStartRequest(
                    executor_id="intruder",
                    on_resource_busy="fail",
                ),
            )
        assert _snapshot(runtime, owner.run_id).outcome is None
        assert executor.start_executor(owner.run_id, owner_request) == lease

        # Retrying admission preserves the exact rejected run and its outcome.
        retry = runtime.application.submit_run(_submission("busy-contender"))
        assert retry.run_id == contender.run_id
        assert retry.snapshot.outcome == outcome


def test_resource_rejection_rolls_back_terminal_state_if_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_close(
        self: SQLiteControlPlane,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        at: datetime,
    ) -> Never:
        raise RuntimeError("injected close failure")

    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        executor = runtime.application.executor
        owner = runtime.application.submit_run(_submission("rollback-owner"))
        executor.start_executor(owner.run_id, ExecutorStartRequest(executor_id="owner"))
        contender = runtime.application.submit_run(_submission("rollback-contender"))
        request = ExecutorStartRequest(executor_id="contender", on_resource_busy="fail")
        events_before = _events(runtime, run_id=contender.run_id).items
        with monkeypatch.context() as patch:
            patch.setattr(
                SQLiteControlPlane, "reject_queued_run_in_transaction", refuse_close
            )
            with pytest.raises(RuntimeError, match="injected close failure"):
                executor.start_executor(contender.run_id, request)
        assert _control_run(runtime, contender.run_id).state == "queued"
        assert _snapshot(runtime, contender.run_id).outcome is None
        assert _events(runtime, run_id=contender.run_id).items == events_before
        with pytest.raises(BackendConflict, match="resources are busy"):
            executor.start_executor(contender.run_id, request)
        assert _control_run(runtime, contender.run_id).state == "closed"


def test_run_coverage_is_contiguous_durable_and_retryable(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(
            _submission("coverage-prefix", point_count=3)
        )
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )
        executor = runtime.application.executor

        initial = executor.run_coverage(admission.run_id)
        first = executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )
        retry = executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )

        with pytest.raises(BackendConflict, match="conflicts with durable run state"):
            executor.advance_run_coverage(
                admission.run_id,
                RunCoverageAdvanceCommand(
                    lease_id=lease.lease_id,
                    start_index=2,
                    point_count=1,
                ),
            )

        completed = executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=1,
                point_count=2,
            ),
        )
        historical_retry = executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )

        assert initial.completed_point_count == 0
        assert first.completed_point_count == 1
        assert retry == first
        assert completed.completed_point_count == 3
        assert historical_retry == completed


def test_recovery_groups_are_sparse_idempotent_and_survive_restart(
    tmp_path: Path,
) -> None:
    run_id: str
    schedule_fingerprint = "schedule-v1"
    first = RecoveryGroupCompletion(
        schedule_fingerprint=schedule_fingerprint,
        group_id="comparison:delay-0",
        point_indices=(0, 2),
        output_kind="unrecorded",
    )
    second = RecoveryGroupCompletion(
        schedule_fingerprint=schedule_fingerprint,
        group_id="comparison:delay-1",
        point_indices=(1, 3),
        output_kind="unrecorded",
    )
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(
            _submission("sparse-recovery-groups", point_count=4)
        )
        run_id = admission.run_id
        lease = runtime.application.executor.start_executor(
            run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )
        command = RunRecoveryGroupCommitCommand(
            lease_id=lease.lease_id,
            groups=(first,),
        )

        committed = runtime.application.executor.commit_recovery_groups(
            run_id,
            command,
        )
        retry = runtime.application.executor.commit_recovery_groups(run_id, command)
        with pytest.raises(BackendConflict, match="conflicts with durable run state"):
            runtime.application.executor.commit_recovery_groups(
                run_id,
                RunRecoveryGroupCommitCommand(
                    lease_id=lease.lease_id,
                    groups=(
                        RecoveryGroupCompletion(
                            schedule_fingerprint="different-schedule",
                            group_id="different",
                            point_indices=(1,),
                            output_kind="unrecorded",
                        ),
                    ),
                ),
            )
        runtime.application.executor.commit_recovery_groups(
            run_id,
            RunRecoveryGroupCommitCommand(
                lease_id=lease.lease_id,
                groups=(second,),
            ),
        )

        assert retry == committed
        assert (
            runtime.application.executor.run_coverage(run_id).completed_point_count == 0
        )
        latest = runtime.application.executor.recovery_groups(run_id, limit=1)
        assert tuple(item.completion for item in latest.items) == (second,)
        assert latest.next_cursor is not None
        earlier = runtime.application.executor.recovery_groups(
            run_id,
            limit=1,
            before=latest.next_cursor,
        )
        assert tuple(item.completion for item in earlier.items) == (first,)

        with pytest.raises(BackendConflict, match="conflicts with durable run state"):
            runtime.application.executor.commit_recovery_groups(
                run_id,
                RunRecoveryGroupCommitCommand(
                    lease_id=lease.lease_id,
                    groups=(
                        RecoveryGroupCompletion(
                            schedule_fingerprint=schedule_fingerprint,
                            group_id="overlap",
                            point_indices=(2,),
                            output_kind="unrecorded",
                        ),
                    ),
                ),
            )

    with LocalDaemonRuntime(tmp_path) as restarted:
        restored = restarted.application.executor.recovery_groups(run_id)
        assert tuple(item.completion for item in restored.items) == (first, second)


def test_measurement_recovery_group_requires_published_matching_records(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(
            _submission("measurement-recovery-group", point_count=2)
        )
        run_id = admission.run_id
        lease = runtime.application.executor.start_executor(
            run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )
        header = MeasurementDatasetHeader(
            run_id=run_id,
            recording_contract_fingerprint="test.recording.v1",
            dataset_schema=MeasurementDatasetSchema(
                dataset_id="raw-measurements",
                point_domain=MeasurementProductGridPointDomain(
                    axes=[
                        MeasurementPointDomainAxis(
                            id="point",
                            size=2,
                            source=MeasurementPointDomainValuesSource(
                                values=[
                                    MeasurementScalar.create(
                                        dtype="int64",
                                        value=point_index,
                                    )
                                    for point_index in range(2)
                                ]
                            ),
                        )
                    ]
                ),
                dimensions=[MeasurementDimension(id="point", kind="point", size=2)],
                variables=[
                    MeasurementVariable(
                        id="signal",
                        role="observable",
                        dtype="float64",
                        dims=["point"],
                    )
                ],
            ),
            expected_record_count=2,
            record_count_limit=2,
        )
        records = tuple(
            MeasurementRecord(
                run_id=run_id,
                logical_point_id=f"point-{point_index}",
                point_index=point_index,
                coordinates={},
                observables={
                    "signal": MeasurementScalar.create(
                        dtype="float64",
                        value=float(point_index),
                    )
                },
            )
            for point_index in range(2)
        )
        completion = RecoveryGroupCompletion(
            schedule_fingerprint="schedule-v1",
            group_id="comparison",
            point_indices=(1, 0),
            output_kind="measurement",
            record_content_hashes=tuple(
                measurement_record_content_hash(records[point_index])
                for point_index in (1, 0)
            ),
        )
        runtime.application.executor.initialize_measurements(
            run_id,
            MeasurementHeaderCommand(lease_id=lease.lease_id, header=header),
        )
        runtime.application.executor.ingest_measurements(
            run_id,
            lease_id=lease.lease_id,
            content=encode_measurement_append(
                MeasurementDatasetAppend(
                    run_id=run_id,
                    header_content_hash=header.content_hash,
                    acquisition_start=0,
                    records=records,
                ),
                header.dataset_schema,
            ),
        )

        with pytest.raises(BackendConflict, match="conflicts with durable run state"):
            runtime.application.executor.commit_recovery_groups(
                run_id,
                RunRecoveryGroupCommitCommand(
                    lease_id=lease.lease_id,
                    groups=(completion,),
                ),
            )

        runtime.application.executor.flush_measurements(
            run_id,
            MeasurementFlushCommand(lease_id=lease.lease_id),
        )
        with pytest.raises(BackendConflict, match="conflicts with durable run state"):
            runtime.application.executor.commit_recovery_groups(
                run_id,
                RunRecoveryGroupCommitCommand(
                    lease_id=lease.lease_id,
                    groups=(
                        completion.model_copy(
                            update={
                                "record_content_hashes": (
                                    "wrong-record-1",
                                    "wrong-record-0",
                                )
                            }
                        ),
                    ),
                ),
            )
        committed = runtime.application.executor.commit_recovery_groups(
            run_id,
            RunRecoveryGroupCommitCommand(
                lease_id=lease.lease_id,
                groups=(completion,),
            ),
        )

        assert tuple(item.completion for item in committed.items) == (completion,)


def test_domain_job_transitions_are_fenced_retryable_and_survive_restart(
    tmp_path: Path,
) -> None:
    run_id: str
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(
            _submission("domain-job-transitions", point_count=3)
        )
        run_id = admission.run_id
        lease = runtime.application.executor.start_executor(
            run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )
        executor = runtime.application.executor
        intent, execution_id = domain_execution_identity(
            run_id=run_id,
            logical_compute_node_id="domain.batch-0",
            invocation_id="invocation-1",
            target_intent={"provider_profile": "fast-readout"},
        )
        first_checkpoint = DomainJobCheckpoint(
            execution_key=execution_id.execution_key,
            job_id="provider-job",
            revision=1,
            resume_token={"cursor": "poll-1"},
            progress={"status": "submitted"},
        )
        first_item = RunDomainJobTransitionItem(
            logical_compute_node_id="domain.batch-0",
            point_ordinals=(0, 1),
            transition=DomainJobCheckpointTransition(checkpoint=first_checkpoint),
        )

        def commit(item: RunDomainJobTransitionItem):
            receipt = executor.commit_domain_job_transitions(
                run_id,
                RunDomainJobTransitionBatchCommand(
                    lease_id=lease.lease_id,
                    items=(item,),
                ),
            )
            return receipt.items[0]

        with pytest.raises(BackendConflict, match="durable run state"):
            commit(first_item)
        invocation_item = first_item.model_copy(
            update={
                "transition": DomainJobInvocationTransition(
                    execution_id=execution_id,
                    intent=intent,
                )
            }
        )
        with pytest.raises(BackendConflict, match="durable run state"):
            executor.commit_domain_job_transitions(
                run_id,
                RunDomainJobTransitionBatchCommand(
                    lease_id=lease.lease_id,
                    items=(
                        invocation_item,
                        first_item.model_copy(update={"point_ordinals": (1,)}),
                    ),
                ),
            )
        initial_command = RunDomainJobTransitionBatchCommand(
            lease_id=lease.lease_id,
            items=(invocation_item, first_item),
        )
        initial = executor.commit_domain_job_transitions(run_id, initial_command)
        assert (
            executor.commit_domain_job_transitions(run_id, initial_command) == initial
        )
        invocation, first = initial.items
        retry = commit(first_item)
        second_checkpoint = first_checkpoint.model_copy(
            update={
                "revision": 2,
                "resume_token": {"cursor": "poll-2"},
                "progress": {"status": "results_ready"},
            }
        )
        second = commit(
            first_item.model_copy(
                update={
                    "transition": DomainJobCheckpointTransition(
                        checkpoint=second_checkpoint
                    )
                }
            )
        )

        assert retry == first
        assert first.sequence > invocation.sequence
        assert second.sequence > first.sequence
        assert [
            item.transition.kind
            for item in executor.domain_job_transitions(run_id).items
        ] == ["invocation", "checkpoint", "checkpoint"]
        with pytest.raises(BackendConflict, match="durable run state"):
            commit(
                first_item.model_copy(
                    update={
                        "transition": DomainJobCheckpointTransition(
                            checkpoint=second_checkpoint.model_copy(
                                update={"revision": 3, "job_id": "another-job"}
                            )
                        )
                    }
                ),
            )
        terminal_item = first_item.model_copy(
            update={
                "transition": DomainJobTerminalTransition(
                    receipt=DomainExecutionReceipt(
                        execution_key=first_checkpoint.execution_key,
                        status="completed",
                        result_fingerprint="results-v1",
                        result_count=2,
                    )
                )
            }
        )
        terminal = commit(terminal_item)
        terminal_retry = commit(terminal_item)
        assert terminal_retry == terminal
        with pytest.raises(BackendConflict, match="durable run state"):
            commit(
                first_item.model_copy(
                    update={
                        "transition": DomainJobCheckpointTransition(
                            checkpoint=second_checkpoint.model_copy(
                                update={"revision": 3}
                            )
                        )
                    }
                ),
            )

    with LocalDaemonRuntime(tmp_path) as restarted:
        page = restarted.application.executor.domain_job_transitions(run_id)
        assert [item.transition.kind for item in page.items] == [
            "invocation",
            "checkpoint",
            "checkpoint",
            "terminal",
        ]
        pending = page.items[-2].transition
        assert isinstance(pending, DomainJobCheckpointTransition)
        assert pending.checkpoint.resume_token == {"cursor": "poll-2"}
        terminal_transition = page.items[-1].transition
        assert isinstance(terminal_transition, DomainJobTerminalTransition)
        assert terminal_transition.receipt.status == "completed"
        [state] = restarted.application.executor.domain_jobs(run_id).items
        assert state.state == "terminal"
        assert state.transition_count == 4
        assert state.invocation.intent.target_intent == {
            "provider_profile": "fast-readout"
        }
        assert state.latest_transition == terminal_transition


def test_domain_job_invocation_without_outcome_survives_restart(
    tmp_path: Path,
) -> None:
    run_id: str
    execution_id: DomainExecutionId
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(
            _submission("domain-job-invocation", point_count=1)
        )
        run_id = admission.run_id
        lease = runtime.application.executor.start_executor(
            run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )
        intent, execution_id = domain_execution_identity(
            run_id=run_id,
            logical_compute_node_id="domain.batch-0",
            invocation_id="invocation-1",
        )
        [committed] = runtime.application.executor.commit_domain_job_transitions(
            run_id,
            RunDomainJobTransitionBatchCommand(
                lease_id=lease.lease_id,
                items=(
                    RunDomainJobTransitionItem(
                        logical_compute_node_id=execution_id.logical_compute_node_id,
                        point_ordinals=(0,),
                        transition=DomainJobInvocationTransition(
                            execution_id=execution_id,
                            intent=intent,
                        ),
                    ),
                ),
            ),
        ).items
        assert committed.transition.kind == "invocation"

    with LocalDaemonRuntime(tmp_path) as restarted:
        page = restarted.application.executor.domain_job_transitions(run_id)
        [persisted] = page.items
        assert isinstance(persisted.transition, DomainJobInvocationTransition)
        assert persisted.transition.execution_id == execution_id
        [state] = restarted.application.executor.domain_jobs(run_id).items
        assert state.state == "invocation_unknown"
        assert state.transition_count == 1
        assert state.invocation.intent == intent
        assert state.latest_transition == persisted.transition


def test_open_point_plan_can_succeed_below_its_limit_and_exposes_coverage(
    tmp_path: Path,
) -> None:
    submission = _submission("adaptive-coverage").model_copy(
        update={
            "plan": RunPlanSummary(
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
                        unit="GHz",
                        sampled_values=(Quantity(5.0, "GHz"),),
                    ),
                ),
                sampled_points=({"frequency": Quantity(5.0, "GHz")},),
                run_resource_requirements=(
                    RunResourceRequirement(id="source-0", kind="instrument"),
                ),
            )
        }
    )
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(submission)
        initialized = runtime.application.point_plans.read(admission.run_id)
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="notebook-adaptive"),
        )
        runtime.application.executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )
        terminal = TerminalRunCommitCommand(
            lease_id=lease.lease_id,
            outcome=RunOutcome(
                run_id=admission.run_id,
                result="succeeded",
                certainty="known",
            ),
        )
        with pytest.raises(BackendConflict, match="closed durable point plan"):
            runtime.application.executor.commit_terminal(
                admission.run_id,
                terminal,
            )
        closed = runtime.application.executor.close_run_point_plan(
            admission.run_id,
            RunPointPlanCloseCommand(
                lease_id=lease.lease_id,
                operation_id="close-adaptive-coverage",
                based_on_completed_point_count=1,
                reason="optimizer converged",
            ),
        )
        manifest = runtime.application.executor.commit_terminal(
            admission.run_id,
            terminal,
        )
        view = runtime.application.runs.get_run(admission.run_id)

    assert not initialized.plan_closed
    assert closed.plan_closed
    assert manifest.outcome is not None
    assert manifest.outcome.result == "succeeded"
    assert view.control.completed_point_count == 1
    assert view.control.point_plan == closed
    assert view.control.admission.plan.point_count is None
    assert view.control.admission.plan.initial_point_count == 1
    assert view.control.admission.plan.point_limit == 3


def test_run_point_resolution_preserves_raw_input_and_makes_snap_explicit(
    tmp_path: Path,
) -> None:
    submission = _submission("adaptive-resolution").model_copy(
        update={
            "plan": RunPlanSummary(
                experiment_id="scratch",
                experiment_kind="scratch",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=None,
                initial_point_count=2,
                point_limit=4,
                adaptive_coordinate_ids=("frequency",),
                adaptive_scope="per_region",
                adaptive_region_count=1,
                adaptive_regions=(
                    AdaptiveRegionSpec(
                        id="region-0",
                        coordinates={},
                        initial_point_count=2,
                    ),
                ),
                coordinates=(
                    PointCoordinateSpec(
                        id="frequency",
                        kind="quantity",
                        unit="GHz",
                        minimum=4.0,
                        maximum=6.0,
                        sampled_values=(
                            Quantity(5.0, "GHz"),
                            Quantity(5.2, "GHz"),
                        ),
                    ),
                ),
                sampled_points=(
                    {"frequency": Quantity(5.0, "GHz")},
                    {"frequency": Quantity(5.2, "GHz")},
                ),
                run_resource_requirements=(
                    RunResourceRequirement(id="source-0", kind="instrument"),
                ),
            )
        }
    )
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(submission)
        runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="adaptive-resolution-test"),
        )
        snap = runtime.application.point_plans.resolve(
            admission.run_id,
            RunDomainResolveCommand(
                coordinate_mode="snap",
                region_scope="current",
                fragment=RunDomainFragmentInput.from_fragment(
                    ResolvedDomainFragment.points(
                        ({"frequency": Quantity(5.16, "GHz")},)
                    )
                ),
            ),
        )
        free = runtime.application.point_plans.resolve(
            admission.run_id,
            RunDomainResolveCommand(
                coordinate_mode="free",
                region_scope="current",
                fragment=RunDomainFragmentInput.from_fragment(
                    ResolvedDomainFragment.points(
                        ({"frequency": Quantity(4.5, "GHz")},)
                    )
                ),
            ),
        )
        queued = runtime.application.point_plans.enqueue(
            admission.run_id,
            RunDomainEnqueueCommand(
                request_id="operator-snap",
                coordinate_mode="snap",
                region_scope="current",
                fragment=RunDomainFragmentInput.from_fragment(
                    ResolvedDomainFragment.points(
                        ({"frequency": Quantity(5.16, "GHz")},)
                    )
                ),
            ),
        )

        with pytest.raises(BackendConflict, match="at least"):
            runtime.application.point_plans.resolve(
                admission.run_id,
                RunDomainResolveCommand(
                    coordinate_mode="free",
                    region_scope="current",
                    fragment=RunDomainFragmentInput.from_fragment(
                        ResolvedDomainFragment.points(
                            ({"frequency": Quantity(3.5, "GHz")},)
                        )
                    ),
                ),
            )

    assert tuple(snap.requested_fragment.fragment().rows()) == (
        {"frequency": Quantity(5.16, "GHz")},
    )
    assert tuple(snap.fragment.fragment().rows()) == (
        {"frequency": Quantity(5.2, "GHz")},
    )
    assert tuple(free.fragment.fragment().rows()) == (
        {"frequency": Quantity(4.5, "GHz")},
    )
    assert queued.request.coordinate_mode == "snap"
    assert queued.request.requested_fragment == snap.requested_fragment
    assert queued.request.fragment == snap.fragment


def test_selected_region_resolution_defers_to_executor_when_region_sample_is_truncated(
    tmp_path: Path,
) -> None:
    submission = _submission("truncated-adaptive-regions").model_copy(
        update={
            "plan": RunPlanSummary(
                experiment_id="scratch",
                experiment_kind="scratch",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=None,
                initial_point_count=0,
                point_limit=4,
                adaptive_coordinate_ids=("frequency",),
                adaptive_scope="per_region",
                adaptive_region_count=300,
                adaptive_regions=(
                    AdaptiveRegionSpec(
                        id="region-0",
                        coordinates={},
                        initial_point_count=0,
                    ),
                ),
                adaptive_regions_truncated=True,
                coordinates=(
                    PointCoordinateSpec(
                        id="frequency",
                        kind="quantity",
                        unit="GHz",
                        sampled_values=(Quantity(5.0, "GHz"),),
                    ),
                ),
                run_resource_requirements=(
                    RunResourceRequirement(id="source-0", kind="instrument"),
                ),
            )
        }
    )
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(submission)
        resolved = runtime.application.point_plans.resolve(
            admission.run_id,
            RunDomainResolveCommand(
                coordinate_mode="free",
                region_scope="selected",
                region_ids=("region-299",),
                fragment=RunDomainFragmentInput.from_fragment(
                    ResolvedDomainFragment.points(
                        ({"frequency": Quantity(5.1, "GHz")},)
                    )
                ),
            ),
        )

    assert resolved.region_ids == ("region-299",)
    assert resolved.region_count == 1


def test_adaptive_domain_ledger_survives_runtime_restart(tmp_path: Path) -> None:
    submission = _submission("adaptive-ledger").model_copy(
        update={
            "plan": RunPlanSummary(
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
                        id="region-0",
                        coordinates={},
                        initial_point_count=1,
                    ),
                ),
                coordinates=(
                    PointCoordinateSpec(
                        id="frequency",
                        kind="quantity",
                        unit="GHz",
                        sampled_values=(Quantity(5.0, "GHz"),),
                    ),
                ),
                sampled_points=({"frequency": Quantity(5.0, "GHz")},),
                run_resource_requirements=(
                    RunResourceRequirement(id="source-0", kind="instrument"),
                ),
            )
        }
    )
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(submission)
        initialized = runtime.application.point_plans.read(admission.run_id)
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="adaptive-ledger-test"),
        )
        runtime.application.executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )
        fragment = ResolvedDomainFragment.points(({"frequency": Quantity(5.2, "GHz")},))
        queued = runtime.application.point_plans.enqueue(
            admission.run_id,
            RunDomainEnqueueCommand(
                request_id="queue-1",
                coordinate_mode="free",
                region_scope="current",
                fragment=RunDomainFragmentInput.from_fragment(fragment),
            ),
        )
        proposal = DomainProposalAttempt(
            fragment,
            region_ids=("region-0",),
            source="operator",
        )
        [row] = fragment.rows()
        candidate = PointProposalAttempt(
            row,
            source="operator",
            region_id="region-0",
            domain_proposal_fingerprint=proposal.proposal_fingerprint,
        )
        decision = runtime.application.executor.append_run_domain_decision(
            admission.run_id,
            RunDomainDecisionCommand(
                lease_id=lease.lease_id,
                operation_id="decision-1",
                operator_request_id=queued.request.request_id,
                proposal=RunDomainProposalAttemptView.from_proposal(proposal),
                accepted_points=(
                    AcceptedRunPointView(
                        point_index=1,
                        coordinates=cast("dict[str, RunPointCoordinateValue]", row),
                        proposal_fingerprint=candidate.proposal_fingerprint,
                        source="operator",
                        region_id="region-0",
                        domain_proposal_fingerprint=proposal.proposal_fingerprint,
                    ),
                ),
                outcome="accepted",
            ),
        )
        rejected = runtime.application.executor.append_run_domain_decision(
            admission.run_id,
            RunDomainDecisionCommand(
                lease_id=lease.lease_id,
                operation_id="decision-2",
                proposal=RunDomainProposalAttemptView.from_proposal(
                    DomainProposalAttempt(
                        fragment,
                        region_ids=("region-0",),
                        source="optimizer",
                    )
                ),
                outcome="rejected",
                reason="proposal used stale observations",
            ),
        )
        runtime.application.executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=1,
                point_count=1,
            ),
        )
        closed = runtime.application.executor.close_run_point_plan(
            admission.run_id,
            RunPointPlanCloseCommand(
                lease_id=lease.lease_id,
                operation_id="close",
                based_on_completed_point_count=2,
                reason="optimizer converged",
            ),
        )

        assert initialized.accepted_point_count == 1
        assert decision.accepted_point_start == 1
        assert decision.accepted_point_count == 1
        assert closed.accepted_point_count == 2
        assert closed.plan_closed

    with LocalDaemonRuntime(tmp_path) as restarted:
        restored = restarted.application.point_plans.read(admission.run_id)
        restored_queue = restarted.application.point_plans.queue(admission.run_id)
        latest_decisions = restarted.application.point_plans.decisions(
            admission.run_id,
            limit=1,
        )
        older_decisions = restarted.application.point_plans.decisions(
            admission.run_id,
            limit=1,
            before=latest_decisions.next_cursor,
        )

    assert restored == closed
    assert restored_queue.items[0].status == "accepted"
    assert restored_queue.items[0].accepted_point_start == 1
    assert latest_decisions.items == (rejected,)
    assert latest_decisions.next_cursor == 1
    assert older_decisions.items == (decision,)
    assert older_decisions.next_cursor is None


def test_closed_point_plan_cannot_succeed_before_full_coverage(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(
            _submission("incomplete-success", point_count=2)
        )
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="notebook-incomplete"),
        )

        with pytest.raises(BackendConflict, match="coverage"):
            runtime.application.executor.commit_terminal(
                admission.run_id,
                TerminalRunCommitCommand(
                    lease_id=lease.lease_id,
                    outcome=RunOutcome(
                        run_id=admission.run_id,
                        result="succeeded",
                        certainty="known",
                    ),
                ),
            )


def test_failed_adaptive_run_abandons_pending_operator_domains(tmp_path: Path) -> None:
    submission = _submission("failed-adaptive-queue").model_copy(
        update={
            "plan": RunPlanSummary(
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
                        unit="GHz",
                        sampled_values=(Quantity(5.0, "GHz"),),
                    ),
                ),
                sampled_points=({"frequency": Quantity(5.0, "GHz")},),
                run_resource_requirements=(
                    RunResourceRequirement(id="source-0", kind="instrument"),
                ),
            )
        }
    )
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(submission)
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="failed-adaptive-test"),
        )
        runtime.application.point_plans.enqueue(
            admission.run_id,
            RunDomainEnqueueCommand(
                request_id="pending-at-failure",
                coordinate_mode="free",
                region_scope="current",
                fragment=RunDomainFragmentInput.from_fragment(
                    ResolvedDomainFragment.points(
                        ({"frequency": Quantity(5.2, "GHz")},)
                    )
                ),
            ),
        )
        manifest = runtime.application.executor.commit_terminal(
            admission.run_id,
            TerminalRunCommitCommand(
                lease_id=lease.lease_id,
                outcome=RunOutcome(
                    run_id=admission.run_id,
                    result="failed",
                    certainty="known",
                    problems=(
                        problem(
                            "test.adaptive_failure",
                            "adaptive execution failed",
                            phase=ProblemPhase.EXECUTION,
                        ),
                    ),
                ),
            ),
        )
        point_plan = runtime.application.point_plans.read(admission.run_id)
        queue = runtime.application.point_plans.queue(admission.run_id)

    assert manifest.outcome is not None
    assert manifest.outcome.result == "failed"
    assert point_plan.plan_closed
    assert point_plan.stop_reason == "run failed"
    assert queue.items[0].status == "cancelled"
    assert queue.items[0].reason == "point plan abandoned: run failed"


def test_queued_run_cancellation_is_immediate_durable_and_idempotent(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        admission = runtime.application.submit_run(_submission("cancel-queued"))

        response = client.post(f"/api/v1/runs/{admission.run_id}/cancel")
        retry = client.post(f"/api/v1/runs/{admission.run_id}/cancel")
        missing = client.post("/api/v1/runs/missing-run/cancel")

        assert response.status_code == 200
        receipt = RunCancellationReceipt.model_validate(response.json())
        assert RunCancellationReceipt.model_validate(retry.json()) == receipt
        assert receipt.status == "cancelled"
        assert receipt.outcome is not None
        assert receipt.outcome.result == "cancelled"
        assert receipt.outcome.certainty == "known"
        assert receipt.outcome.problems[0].code == "run_cancelled_before_execution"
        assert missing.status_code == 404
        control = _control_run(runtime, admission.run_id)
        assert control.state == "closed"
        assert control.cancellation_requested_at == receipt.cancellation_requested_at
        assert _snapshot(runtime, admission.run_id).outcome == receipt.outcome
        assert _resource_claims(tmp_path) == ()
        assert [
            event.kind
            for event in _events(runtime, run_id=admission.run_id).items
            if event.kind == "run_cancellation_requested"
        ] == ["run_cancellation_requested"]
        with pytest.raises(BackendConflict, match="not ready to start"):
            runtime.application.executor.start_executor(
                admission.run_id,
                ExecutorStartRequest(executor_id="late-executor"),
            )


def test_leased_run_cancellation_reaches_heartbeat_and_preserves_terminal_history(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        admission = runtime.application.submit_run(_submission("cancel-leased"))
        assert not runtime.application.executor.run_cancellation(
            admission.run_id
        ).requested
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )

        requested = runtime.application.cancel_run(admission.run_id)
        assert runtime.application.executor.run_cancellation(admission.run_id).requested
        retry = runtime.application.cancel_run(admission.run_id)
        heartbeat = runtime.application.executor.heartbeat_executor(
            admission.run_id,
            ExecutorHeartbeat(lease_id=lease.lease_id),
        )

        assert requested == retry
        assert requested.status == "cancel_requested"
        assert heartbeat.cancellation_requested_at == (
            requested.cancellation_requested_at
        )
        assert (
            _control_run(runtime, admission.run_id).cancellation_requested_at
            == requested.cancellation_requested_at
        )
        assert _snapshot(runtime, admission.run_id).outcome is None

        outcome = RunOutcome(
            run_id=admission.run_id,
            result="cancelled",
            certainty="known",
            problems=(
                problem(
                    "run_cancellation_requested",
                    "run stopped at a safe checkpoint after cancellation was requested",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        )
        terminal = runtime.application.executor.commit_terminal(
            admission.run_id,
            TerminalRunCommitCommand(
                lease_id=lease.lease_id,
                outcome=outcome,
            ),
        )
        completed = runtime.application.cancel_run(admission.run_id)

        assert terminal.outcome == outcome
        assert completed.status == "cancelled"
        assert completed.outcome == outcome
        assert completed.cancellation_requested_at == (
            requested.cancellation_requested_at
        )
        assert _control_run(runtime, admission.run_id).state == "closed"
        assert _resource_claims(tmp_path) == ()
        [segment] = runtime.application.executor.execution_segments(
            admission.run_id
        ).items
        assert segment.segment_id == lease.segment_id
        assert segment.result == "cancelled"
        assert segment.certainty == "known"
        assert segment.end_point_count == 0

        racing = runtime.application.submit_run(_submission("cancel-terminal-race"))
        racing_lease = runtime.application.executor.start_executor(
            racing.run_id,
            ExecutorStartRequest(executor_id="notebook-race"),
        )
        racing_request = runtime.application.cancel_run(racing.run_id)
        success_intent = TerminalRunCommitCommand(
            lease_id=racing_lease.lease_id,
            outcome=RunOutcome(
                run_id=racing.run_id,
                result="succeeded",
                certainty="known",
            ),
        )

        raced_terminal = runtime.application.executor.commit_terminal(
            racing.run_id,
            success_intent,
        )
        raced_retry = runtime.application.executor.commit_terminal(
            racing.run_id,
            success_intent,
        )

        assert racing_request.status == "cancel_requested"
        assert raced_retry == raced_terminal
        assert raced_terminal.outcome is not None
        assert raced_terminal.outcome.result == "cancelled"
        assert raced_terminal.outcome.certainty == "known"
        assert raced_terminal.outcome.problems[0].code == ("run_cancellation_requested")

        failing = runtime.application.submit_run(_submission("cancel-after-failure"))
        failing_lease = runtime.application.executor.start_executor(
            failing.run_id,
            ExecutorStartRequest(executor_id="notebook-failure"),
        )
        runtime.application.cancel_run(failing.run_id)
        failed_outcome = RunOutcome(
            run_id=failing.run_id,
            result="failed",
            certainty="known",
            problems=(
                problem(
                    "run_failed_before_cancellation_checkpoint",
                    "run failed before it could honor cancellation",
                    phase=ProblemPhase.EXECUTION,
                ),
            ),
        )

        failed_terminal = runtime.application.executor.commit_terminal(
            failing.run_id,
            TerminalRunCommitCommand(
                lease_id=failing_lease.lease_id,
                outcome=failed_outcome,
            ),
        )

        assert failed_terminal.outcome == failed_outcome

        succeeded = runtime.application.submit_run(_submission("already-succeeded"))
        succeeded_lease = runtime.application.executor.start_executor(
            succeeded.run_id,
            ExecutorStartRequest(executor_id="notebook-2"),
        )
        succeeded_outcome = RunOutcome(
            run_id=succeeded.run_id,
            result="succeeded",
            certainty="known",
        )
        runtime.application.executor.advance_run_coverage(
            succeeded.run_id,
            RunCoverageAdvanceCommand(
                lease_id=succeeded_lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )
        runtime.application.executor.commit_terminal(
            succeeded.run_id,
            TerminalRunCommitCommand(
                lease_id=succeeded_lease.lease_id,
                outcome=succeeded_outcome,
            ),
        )

        not_accepted = runtime.application.cancel_run(succeeded.run_id)

        assert not_accepted.status == "not_accepted"
        assert not_accepted.cancellation_requested_at is None
        assert not_accepted.outcome == succeeded_outcome


def test_effect_is_fenced_and_terminal_updates_control(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        client = TestClient(runtime.app())
        admission_response = client.post(
            "/api/v1/runs",
            json=_submission(point_count=4).model_dump(mode="json"),
        )
        run_id = RunAdmission.model_validate(admission_response.json()).run_id
        accepted = _snapshot(runtime, run_id)
        lease_response = client.post(
            f"/api/v1/runs/{run_id}/executor/start",
            json=ExecutorStartRequest(
                executor_id="notebook-1",
            ).model_dump(mode="json"),
        )
        lease = ExecutorLease.model_validate(lease_response.json())
        measurement_records = tuple(
            MeasurementRecord(
                run_id=run_id,
                logical_point_id=f"point-{point_index}",
                point_index=point_index,
                coordinates={
                    "frequency": MeasurementArray.create(
                        dtype="float64",
                        unit="Hz",
                        values=tuple(float(index) for index in range(5)),
                    )
                },
                observables={
                    "signal": MeasurementScalar.create(
                        dtype="float64",
                        value=point_index + 1.25,
                        unit="ratio",
                    ),
                    "trace": (
                        MeasurementUnavailable.create(
                            reason="overload",
                            dtype="complex128",
                            unit="ratio",
                            shape=(5,),
                            metadata={},
                        )
                        if point_index == 0
                        else MeasurementArray.create(
                            dtype="complex128",
                            unit="ratio",
                            values=tuple(
                                complex(point_index + 1.0, index) for index in range(5)
                            ),
                        )
                    ),
                },
            )
            for point_index in range(4)
        )
        measurement_header = MeasurementDatasetHeader(
            run_id=run_id,
            recording_contract_fingerprint="test.recording.v1",
            dataset_schema=MeasurementDatasetSchema(
                dataset_id="raw-measurements",
                point_domain=MeasurementProductGridPointDomain(
                    axes=[
                        MeasurementPointDomainAxis(
                            id="x",
                            size=2,
                            source=MeasurementPointDomainValuesSource(
                                values=[
                                    MeasurementScalar.create(dtype="int64", value=value)
                                    for value in (10, 20)
                                ]
                            ),
                        ),
                        MeasurementPointDomainAxis(
                            id="bias",
                            size=2,
                            source=MeasurementPointDomainValuesSource(
                                values=[
                                    MeasurementScalar.create(dtype="int64", value=value)
                                    for value in (0, 1)
                                ]
                            ),
                        ),
                    ]
                ),
                dimensions=[
                    MeasurementDimension(id="point", kind="point", size=4),
                    MeasurementDimension(id="sample", kind="frequency", size=5),
                ],
                variables=[
                    MeasurementVariable(
                        id="frequency",
                        role="coordinate",
                        dtype="float64",
                        unit="Hz",
                        dims=["point", "sample"],
                        recording_group_id="readout",
                    ),
                    MeasurementVariable(
                        id="signal",
                        role="observable",
                        dtype="float64",
                        unit="ratio",
                        dims=["point"],
                    ),
                    MeasurementVariable(
                        id="trace",
                        role="observable",
                        dtype="complex128",
                        unit="ratio",
                        dims=["point", "sample"],
                        recording_group_id="readout",
                    ),
                ],
                variable_groups=[MeasurementVariableGroup(id="readout")],
                primary_coordinates=["frequency"],
                primary_observables=["signal", "trace"],
            ),
            expected_record_count=4,
            record_count_limit=4,
        )
        measurement_batch = MeasurementDatasetBatch(
            run_id=run_id,
            header_content_hash=measurement_header.content_hash,
            records=measurement_records,
        )
        missing_schema_slice = client.post(
            f"/api/v1/runs/{run_id}/measurements/query",
            json={"fixed_axis_indices": {"bias": 1}},
        )
        missing_schema_trace = client.post(
            f"/api/v1/runs/{run_id}/measurements/traces/query",
            json={"observable_id": "trace"},
        )
        header_response = client.post(
            f"/api/v1/runs/{run_id}/measurements/header",
            json=MeasurementHeaderCommand(
                lease_id=lease.lease_id,
                header=measurement_header,
            ).model_dump(mode="json"),
        )
        measurement_response = client.post(
            f"/api/v1/runs/{run_id}/measurements/ingest",
            content=encode_measurement_append(
                MeasurementDatasetAppend(
                    run_id=measurement_batch.run_id,
                    header_content_hash=measurement_batch.header_content_hash,
                    acquisition_start=0,
                    records=measurement_batch.records,
                ),
                measurement_header.dataset_schema,
            ),
            headers={
                "Content-Type": "application/vnd.apache.arrow.file",
                "X-Scopecat-Lease-ID": lease.lease_id,
            },
        )
        pending_preview = client.get(f"/api/v1/runs/{run_id}/measurements/preview")
        live_preview = client.get(f"/api/v1/runs/{run_id}/measurements/live")
        pending_coverage = client.get(f"/api/v1/runs/{run_id}/coverage")
        premature_coverage = client.post(
            f"/api/v1/runs/{run_id}/coverage/advance",
            json=RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=4,
            ).model_dump(mode="json"),
        )
        assert premature_coverage.status_code == 409
        assert (
            client.get(f"/api/v1/runs/{run_id}/coverage").json()[
                "completed_point_count"
            ]
            == 0
        )
        flush_response = client.post(
            f"/api/v1/runs/{run_id}/measurements/flush",
            json=MeasurementFlushCommand(
                lease_id=lease.lease_id,
            ).model_dump(mode="json"),
        )
        recovery_response = client.post(
            f"/api/v1/runs/{run_id}/recovery-groups",
            json=RunRecoveryGroupCommitCommand(
                lease_id=lease.lease_id,
                groups=(
                    RecoveryGroupCompletion(
                        schedule_fingerprint="test-runtime-schedule-v1",
                        group_id="all-points",
                        point_indices=tuple(range(4)),
                        output_kind="measurement",
                        record_content_hashes=tuple(
                            measurement_record_content_hash(record)
                            for record in measurement_records
                        ),
                    ),
                ),
            ).model_dump(mode="json"),
        )
        coverage_response = client.post(
            f"/api/v1/runs/{run_id}/coverage/advance",
            json=RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=4,
            ).model_dump(mode="json"),
        )
        durable_coverage = client.get(f"/api/v1/runs/{run_id}/coverage")
        detail = client.get(f"/api/v1/runs/{run_id}")
        measurement_preview = client.get(f"/api/v1/runs/{run_id}/measurements/preview")
        measurement_arrow = client.post(
            f"/api/v1/runs/{run_id}/measurements/arrow",
            json={
                "columns": [
                    {"name": "sample_hz", "variable_id": "frequency"},
                    {"name": "response", "variable_id": "trace"},
                ],
                "limit": 2,
                "diagnostics": "reason",
                "layout": "observations",
            },
        )
        measurement_slice = client.post(
            f"/api/v1/runs/{run_id}/measurements/query",
            json={
                "fixed_axis_indices": {"bias": 1},
                "limit": 1,
                "include_schema": True,
            },
        )
        next_measurement_slice = client.post(
            f"/api/v1/runs/{run_id}/measurements/query",
            json={
                "fixed_axis_indices": {"bias": 1},
                "offset": 1,
                "limit": 1,
            },
        )
        invalid_measurement_slice = client.post(
            f"/api/v1/runs/{run_id}/measurements/query",
            json={"fixed_axis_indices": {"missing": 0}},
        )
        trace_preview = client.post(
            f"/api/v1/runs/{run_id}/measurements/traces/query",
            json={
                "observable_id": "trace",
                "coordinate_id": "frequency",
                "fixed_axis_indices": {"bias": 1},
                "max_series": 2,
                "max_samples": 4,
                "value_mode": "imag",
            },
        )
        truncated_trace_preview = client.post(
            f"/api/v1/runs/{run_id}/measurements/traces/query",
            json={
                "observable_id": "trace",
                "coordinate_id": "frequency",
                "max_series": 1,
                "max_samples": 2,
            },
        )
        exhausted_trace_preview = client.post(
            f"/api/v1/runs/{run_id}/measurements/traces/query",
            json={
                "observable_id": "trace",
                "coordinate_id": "frequency",
                "fixed_axis_indices": {"x": 0},
                "max_series": 2,
                "max_samples": 4,
            },
        )
        invalid_trace_preview = client.post(
            f"/api/v1/runs/{run_id}/measurements/traces/query",
            json={"recording_group_id": "missing"},
        )
        assert header_response.status_code == 200
        assert measurement_response.status_code == 200
        assert measurement_response.json()["received_record_count"] == 4
        assert measurement_response.json()["durable_record_count"] == 0
        assert pending_preview.json()["items"] == []
        live_append = decode_measurement_append(
            live_preview.content,
            measurement_header.dataset_schema,
        )
        assert live_append.records[0].point_index == 3
        assert live_preview.headers["x-scopecat-measurement-active"] == "true"
        assert live_preview.headers["x-scopecat-received-record-count"] == "4"
        assert live_preview.headers["x-scopecat-durable-record-count"] == "0"
        assert pending_coverage.json()["completed_point_count"] == 0
        assert flush_response.status_code == 200
        assert flush_response.json()["durable_record_count"] == 4
        assert recovery_response.status_code == 200
        assert coverage_response.status_code == 200
        assert durable_coverage.json()["completed_point_count"] == 4
        assert detail.json()["control"]["state"] == "leased"
        assert detail.json()["snapshot"]["outcome"] is None
        assert detail.json()["resources"][0]["status"] == "active"
        assert measurement_preview.json()["items"][0]["point_index"] == 0
        assert (
            measurement_preview.json()["dataset_schema"]["dataset_id"]
            == "raw-measurements"
        )
        assert measurement_preview.json()["truncated"] is False
        assert measurement_arrow.status_code == 200
        assert measurement_arrow.headers["x-scopecat-next-offset"] == "2"
        assert measurement_arrow.headers["x-scopecat-snapshot-size"] == "4"
        arrow_table = pa.ipc.open_stream(measurement_arrow.content).read_all()
        assert arrow_table.schema.names == [
            "point_index",
            "logical_point_id",
            "sample_index",
            "sample_hz",
            "sample_hz__unavailable_reason",
            "response",
            "response__unavailable_reason",
        ]
        assert arrow_table.num_rows == 10
        assert arrow_table["sample_index"].to_pylist() == [0, 1, 2, 3, 4] * 2
        assert arrow_table["sample_hz"].to_pylist() == [0.0, 1.0, 2.0, 3.0, 4.0] * 2
        assert (
            arrow_table["response__unavailable_reason"].to_pylist()
            == [
                "overload",
            ]
            * 5
            + [None] * 5
        )
        assert measurement_slice.status_code == 200
        assert missing_schema_slice.status_code == 409
        assert missing_schema_trace.status_code == 409
        assert measurement_slice.json()["items"][0]["point_index"] == 1
        assert measurement_slice.json()["selected_point_count"] == 2
        assert measurement_slice.json()["offset"] == 0
        assert measurement_slice.json()["window_point_count"] == 1
        assert measurement_slice.json()["next_offset"] == 1
        assert measurement_slice.json()["previous_offset"] is None
        assert measurement_slice.json()["truncated"]
        assert measurement_slice.json()["dataset_schema"]["dataset_id"] == (
            "raw-measurements"
        )
        assert next_measurement_slice.status_code == 200
        assert next_measurement_slice.json()["items"][0]["point_index"] == 3
        assert next_measurement_slice.json()["offset"] == 1
        assert next_measurement_slice.json()["next_offset"] is None
        assert next_measurement_slice.json()["previous_offset"] == 0
        assert invalid_measurement_slice.status_code == 409
        assert trace_preview.status_code == 200
        assert trace_preview.json()["coordinate_id"] == "frequency"
        assert trace_preview.json()["selected_series_count"] == 2
        assert trace_preview.json()["returned_series_count"] == 2
        assert not trace_preview.json()["truncated_series"]
        assert trace_preview.json()["source_sample_count"] == 10
        assert trace_preview.json()["returned_sample_count"] == 4
        assert trace_preview.json()["samples_reduced"]
        assert trace_preview.json()["series"][0] == {
            "point_index": 1,
            "logical_point_id": "point-1",
            "label": "X 10 · Bias 1",
            "x": [0.0, 4.0],
            "y": [0.0, 4.0],
            "source_sample_count": 5,
            "available_sample_count": 5,
            "unavailable_reasons": [],
            "entity_index": None,
            "entity": None,
            "evidence": None,
        }
        assert invalid_trace_preview.status_code == 409
        assert truncated_trace_preview.status_code == 200
        assert truncated_trace_preview.json()["selected_series_count"] == 4
        assert truncated_trace_preview.json()["returned_series_count"] == 0
        assert truncated_trace_preview.json()["truncated_series"]
        assert truncated_trace_preview.json()["failures"][0]["point_index"] == 0
        assert truncated_trace_preview.json()["failures"][0]["reasons"] == ["overload"]
        assert exhausted_trace_preview.status_code == 200
        assert exhausted_trace_preview.json()["selected_series_count"] == 2
        assert exhausted_trace_preview.json()["returned_series_count"] == 1
        assert not exhausted_trace_preview.json()["truncated_series"]
        assert exhausted_trace_preview.json()["series"][0]["point_index"] == 1
        outcome = RunOutcome(
            run_id=run_id,
            result="succeeded",
            certainty="known",
            finished_at=datetime.now(tz=UTC),
        )
        terminal = accepted.model_copy(
            update={
                "outcome": outcome,
            }
        )
        terminal_command = TerminalRunCommitCommand(
            lease_id=lease.lease_id,
            outcome=outcome,
        )
        terminal_run_mismatch = client.post(
            f"/api/v1/runs/{run_id}/terminal",
            json=terminal_command.model_copy(
                update={"outcome": outcome.model_copy(update={"run_id": "another-run"})}
            ).model_dump(mode="json"),
        )
        completed = client.post(
            f"/api/v1/runs/{run_id}/terminal",
            json=terminal_command.model_dump(mode="json"),
        )
        terminal_retry = client.post(
            f"/api/v1/runs/{run_id}/terminal",
            json=terminal_command.model_dump(mode="json"),
        )
        terminal_conflict = client.post(
            f"/api/v1/runs/{run_id}/terminal",
            json=terminal_command.model_copy(
                update={
                    "outcome": outcome.model_copy(
                        update={"finished_at": datetime(2026, 7, 23, 10, tzinfo=UTC)}
                    )
                }
            ).model_dump(mode="json"),
        )

        assert terminal_run_mismatch.status_code == 422
        assert terminal_run_mismatch.json() == {
            "detail": "path run_id must match request body"
        }
        assert completed.status_code == 200
        assert completed.json()["outcome"]["result"] == "succeeded"
        assert terminal_retry.json() == completed.json()
        assert terminal_conflict.status_code == 409
        control_run = _control_run(runtime, run_id)
        assert control_run.state == "closed"
        assert _snapshot(runtime, run_id) == terminal
        assert _resource_claims(tmp_path) == ()
        terminal_detail = client.get(f"/api/v1/runs/{run_id}").json()
        assert terminal_detail["resources"][0]["status"] == "released"


def test_effect_and_terminal_publication_roll_back_with_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        submission = _submission()
        admission = runtime.application.submit_run(submission)
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(
                executor_id="notebook-1",
            ),
        )
        measurement = MeasurementRecord(
            run_id=admission.run_id,
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
        header = MeasurementDatasetHeader(
            run_id=admission.run_id,
            recording_contract_fingerprint="test.recording.v1",
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
        batch = MeasurementDatasetBatch(
            run_id=admission.run_id,
            header_content_hash=header.content_hash,
            records=(measurement,),
        )
        runtime.application.executor.initialize_measurements(
            admission.run_id,
            MeasurementHeaderCommand(
                lease_id=lease.lease_id,
                header=header,
            ),
        )
        runtime.application.executor.ingest_measurements(
            admission.run_id,
            lease_id=lease.lease_id,
            content=encode_measurement_append(
                MeasurementDatasetAppend(
                    run_id=batch.run_id,
                    header_content_hash=batch.header_content_hash,
                    acquisition_start=0,
                    records=batch.records,
                ),
                header.dataset_schema,
            ),
        )

        with monkeypatch.context() as patch:

            def fail_event(*_args: object, **_kwargs: object) -> Never:
                raise RuntimeError("event publication failed")

            patch.setattr(
                SQLiteControlPlane,
                "append_event_in_transaction",
                fail_event,
            )
            with pytest.raises(RuntimeError, match="event publication failed"):
                runtime.application.executor.flush_measurements(
                    admission.run_id,
                    MeasurementFlushCommand(
                        lease_id=lease.lease_id,
                    ),
                )

        rolled_back, _, _ = runtime.application.runs.measurement_arrow(
            admission.run_id,
            MeasurementArrowQuery(
                columns=(MeasurementArrowColumn(name="signal", variable_id="signal"),)
            ),
        )
        assert rolled_back.num_rows == 0
        runtime.application.executor.flush_measurements(
            admission.run_id,
            MeasurementFlushCommand(lease_id=lease.lease_id),
        )
        runtime.application.executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )

        outcome = RunOutcome(
            run_id=admission.run_id,
            result="succeeded",
            certainty="known",
        )
        with monkeypatch.context() as patch:

            def fail_close(*_args: object, **_kwargs: object) -> Never:
                raise RuntimeError("control close failed")

            patch.setattr(
                SQLiteControlPlane,
                "close_run_in_transaction",
                fail_close,
            )
            with pytest.raises(RuntimeError, match="control close failed"):
                runtime.application.executor.commit_terminal(
                    admission.run_id,
                    TerminalRunCommitCommand(
                        lease_id=lease.lease_id,
                        outcome=outcome,
                    ),
                )

        assert _snapshot(runtime, admission.run_id).outcome is None
        assert _control_run(runtime, admission.run_id).state == "leased"


def test_restart_quarantines_executor_until_operator_reconciles(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:
        submission = _submission("operator-recovery").model_copy(
            update={
                "plan": RunPlanSummary(
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
                            unit="GHz",
                            sampled_values=(Quantity(5.0, "GHz"),),
                        ),
                    ),
                    sampled_points=({"frequency": Quantity(5.0, "GHz")},),
                    run_resource_requirements=(
                        RunResourceRequirement(id="source-0", kind="instrument"),
                    ),
                )
            }
        )
        admission = runtime.application.submit_run(submission)
        lease = runtime.application.executor.start_executor(
            admission.run_id,
            ExecutorStartRequest(
                executor_id="notebook-1",
            ),
        )
        runtime.application.executor.advance_run_coverage(
            admission.run_id,
            RunCoverageAdvanceCommand(
                lease_id=lease.lease_id,
                start_index=0,
                point_count=1,
            ),
        )
        runtime.application.point_plans.enqueue(
            admission.run_id,
            RunDomainEnqueueCommand(
                request_id="queue-before-restart",
                coordinate_mode="free",
                region_scope="current",
                fragment=RunDomainFragmentInput.from_fragment(
                    ResolvedDomainFragment.points(
                        ({"frequency": Quantity(5.2, "GHz")},)
                    )
                ),
            ),
        )
        run_id = admission.run_id

    with LocalDaemonRuntime(tmp_path) as reopened:
        attention = _control_run(reopened, run_id)
        assert attention.state == "attention_required"
        assert attention.attention_reason == "daemon_restarted"
        assert _resource_claims(tmp_path)[0].status == "quarantined"
        coverage = reopened.application.executor.run_coverage(run_id)
        assert coverage.completed_point_count == 1
        interrupted_plan = reopened.application.point_plans.read(run_id)
        interrupted_queue = reopened.application.point_plans.queue(run_id)
        assert not interrupted_plan.plan_closed
        assert interrupted_queue.items[0].status == "pending"
        with pytest.raises(BackendConflict, match="attention_required"):
            reopened.application.executor.start_executor(
                run_id,
                ExecutorStartRequest(executor_id="notebook-2"),
            )

        with pytest.raises(BackendConflict, match="contract"):
            reopened.application.resolve_attention(
                run_id,
                AttentionResolutionCommand.continue_run(
                    run_contract_fingerprint="0" * 64,
                ),
            )
        continued = reopened.application.resolve_attention(
            run_id,
            AttentionResolutionCommand.continue_run(
                run_contract_fingerprint=submission.intent_content_hash,
            ),
        )
        assert continued.disposition == "continue"
        assert continued.state == "queued"
        assert continued.released_resource_count == 1
        assert _snapshot(reopened, run_id).outcome is None
        continuing_lease = reopened.application.executor.start_executor(
            run_id,
            ExecutorStartRequest(executor_id="notebook-2"),
        )
        segments = reopened.application.executor.execution_segments(run_id).items
        assert [segment.ordinal for segment in segments] == [1, 0]
        assert segments[0].segment_id == continuing_lease.segment_id
        assert segments[0].start_point_count == 1
        assert segments[0].result is None
        assert segments[1].result == "interrupted"
        assert segments[1].reason == "daemon_restarted"
        reopened.application.executor._control.mark_executor_unknown(
            run_id,
            token=continuing_lease.lease_id,
            reason="continuation_reconciled_for_close",
        )

        resolved = reopened.application.resolve_attention(
            run_id,
            AttentionResolutionCommand.close_run(),
        )
        assert resolved.disposition == "close"
        assert resolved.state == "closed"
        assert resolved.released_resource_count == 1
        control = _control_run(reopened, run_id)
        assert control.state == "closed"
        snapshot = _snapshot(reopened, run_id)
        assert snapshot.outcome is not None
        assert snapshot.outcome.certainty == "indeterminate"
        assert snapshot.outcome.problems[0].code == "daemon.executor_loss_reconciled"
        abandoned_plan = reopened.application.point_plans.read(run_id)
        abandoned_queue = reopened.application.point_plans.queue(run_id)
        assert abandoned_plan.plan_closed
        assert abandoned_plan.stop_reason == "executor loss reconciled"
        assert abandoned_queue.items[0].status == "cancelled"
        assert abandoned_queue.items[0].reason == (
            "point plan abandoned: executor loss reconciled"
        )
        assert _resource_claims(tmp_path) == ()
        with pytest.raises(BackendConflict, match="not ready to start"):
            reopened.application.executor.start_executor(
                run_id,
                ExecutorStartRequest(executor_id="notebook-2"),
            )


@pytest.mark.parametrize(
    "segmented,restart", [(False, False), (True, False), (True, True)]
)
def test_continuation_appends_measurements_in_a_new_segment_fragment(
    tmp_path: Path,
    segmented: bool,
    restart: bool,
) -> None:
    with ExitStack() as stack:
        runtime = stack.enter_context(
            LocalDaemonRuntime(tmp_path, bootstrap_config=_config())
        )
        submission = _submission("measurement-fragments", point_count=2)
        admission = runtime.application.submit_run(submission)
        run_id = admission.run_id
        first_lease = runtime.application.executor.start_executor(
            run_id,
            ExecutorStartRequest(executor_id="notebook-1"),
        )
        header = MeasurementDatasetHeader(
            run_id=run_id,
            recording_contract_fingerprint="test.recording.v1",
            dataset_schema=MeasurementDatasetSchema(
                dataset_id="raw-measurements",
                point_domain=MeasurementProductGridPointDomain(
                    axes=[
                        MeasurementPointDomainAxis(
                            id="point",
                            size=2,
                            source=MeasurementPointDomainValuesSource(
                                values=[
                                    MeasurementScalar.create(
                                        dtype="int64",
                                        value=point_index,
                                    )
                                    for point_index in range(2)
                                ]
                            ),
                        )
                    ]
                ),
                dimensions=[
                    MeasurementDimension(id="point", kind="point", size=2),
                    *(
                        [
                            MeasurementDimension(
                                id="entity",
                                kind="entity",
                                size=2,
                                index=MeasurementEntityIndex(
                                    values=(
                                        EntityRef(id="q0", kind="qubit"),
                                        EntityRef(id="q1", kind="qubit"),
                                    )
                                ),
                            ),
                            MeasurementDimension(id="sample", kind="sample", size=None),
                        ]
                        if segmented
                        else []
                    ),
                ],
                variables=[
                    MeasurementVariable(
                        id="signal",
                        role="observable",
                        dtype="float64",
                        unit="ratio",
                        dims=["point", "entity", "sample"] if segmented else ["point"],
                    )
                ],
            ),
            expected_record_count=2,
            record_count_limit=2,
        )

        def record(point_index: int) -> MeasurementRecord:
            if segmented:
                length = 3 + point_index
                value = MeasurementSegmentedArray.create(
                    dtype="float64",
                    unit="ratio",
                    segments=(
                        MeasurementArray.create(
                            values=[1.0, 999.0, *range(2, length)],
                            unit="ratio",
                            availability=MeasurementArrayAvailability.create(
                                valid=[True, False, *([True] * (length - 2))],
                                reason="overload",
                            ),
                        ),
                        MeasurementUnavailable.create(
                            reason="missing",
                            dtype="float64",
                            unit="ratio",
                            shape=(None,),
                            metadata={"entity": "q1", "details": [{"attempt": 1}]},
                        )
                        if point_index == 0
                        else MeasurementArray.create(values=[8.0, 9.0], unit="ratio"),
                    ),
                )
                evidence = EntityAcquisitionEvidence(
                    dimension_id="entity",
                    acquisition=MeasurementEntityAcquisition(),
                    values=tuple(
                        InstrumentAcquisitionEvidence(
                            command_id=f"read-{point_index}",
                            instrument_id="simulator",
                            interface_id="test.ragged/v1",
                            acquisition_id=f"a-{point_index}",
                            result_id=f"q{i}-p{point_index}",
                            started_at=datetime(2026, 9, 6, tzinfo=UTC),
                            completed_at=datetime(2026, 9, 6, tzinfo=UTC),
                        )
                        for i in range(2)
                    ),
                )
                return MeasurementRecord(
                    run_id=run_id,
                    logical_point_id=f"point-{point_index}",
                    point_index=point_index,
                    coordinates={},
                    observables={"signal": value},
                    acquisition_evidence=MeasurementAcquisitionEvidenceCatalog.create(
                        {"signal": evidence}
                    ),
                )
            return MeasurementRecord(
                run_id=run_id,
                logical_point_id=f"point-{point_index}",
                point_index=point_index,
                coordinates={},
                observables={
                    "signal": MeasurementScalar.create(
                        dtype="float64",
                        value=point_index + 1,
                        unit="ratio",
                    )
                },
            )

        def append(lease: ExecutorLease, point_index: int) -> None:
            measurement = MeasurementDatasetAppend(
                run_id=run_id,
                header_content_hash=header.content_hash,
                acquisition_start=point_index,
                records=(record(point_index),),
            )
            runtime.application.executor.ingest_measurements(
                run_id,
                lease_id=lease.lease_id,
                content=encode_measurement_append(
                    measurement,
                    header.dataset_schema,
                ),
            )
            runtime.application.executor.flush_measurements(
                run_id,
                MeasurementFlushCommand(lease_id=lease.lease_id),
            )

        runtime.application.executor.initialize_measurements(
            run_id,
            MeasurementHeaderCommand(
                lease_id=first_lease.lease_id,
                header=header,
            ),
        )
        append(first_lease, 0)
        if restart:
            runtime.close()
            runtime = stack.enter_context(LocalDaemonRuntime(tmp_path))
        else:
            runtime.application.executor._control.mark_executor_unknown(
                run_id, token=first_lease.lease_id, reason="executor_disconnected"
            )
        runtime.application.resolve_attention(
            run_id,
            AttentionResolutionCommand.continue_run(
                run_contract_fingerprint=submission.intent_content_hash,
            ),
        )
        second_lease = runtime.application.executor.start_executor(
            run_id,
            ExecutorStartRequest(executor_id="notebook-2"),
        )
        runtime.application.executor.initialize_measurements(
            run_id,
            MeasurementHeaderCommand(
                lease_id=second_lease.lease_id,
                header=header,
            ),
        )
        live = runtime.application.runs.measurement_live_preview(
            run_id,
            after_record_count=None,
        )
        assert live.received_record_count == 1
        assert live.durable_record_count == 1
        with pytest.raises(BackendConflict):
            append(first_lease, 1)
        append(second_lease, 1)
        first_append = MeasurementDatasetAppend(
            run_id=run_id,
            header_content_hash=header.content_hash,
            acquisition_start=0,
            records=(record(0),),
        )
        second_append = MeasurementDatasetAppend(
            run_id=run_id,
            header_content_hash=header.content_hash,
            acquisition_start=1,
            records=(record(1),),
        )
        seal_receipt = runtime.application.executor.seal_measurements(
            run_id,
            MeasurementSealCommand(
                lease_id=second_lease.lease_id,
                seal=MeasurementDatasetSeal(
                    run_id=run_id,
                    header_content_hash=header.content_hash,
                    record_count=2,
                    fragment_record_count=1,
                    fragment_content_hash=measurement_fragment_content_hash(
                        header_content_hash=header.content_hash,
                        record_content_hashes=second_append.record_content_hashes,
                    ),
                ),
            ),
        )
        expected_dataset_content_hash = measurement_dataset_content_hash(
            header_content_hash=header.content_hash,
            record_content_hashes=(
                *first_append.record_content_hashes,
                *second_append.record_content_hashes,
            ),
        )
        assert seal_receipt.dataset_content_hash == expected_dataset_content_hash

        fragments = SQLiteMeasurementDatasetRepository(
            _run_repository(tmp_path),
            run_id=run_id,
        ).measurement_fragments()
        assert [fragment.segment_id for fragment in fragments] == [
            first_lease.segment_id,
            second_lease.segment_id,
        ]
        assert [fragment.acquisition_start for fragment in fragments] == [0, 1]
        assert [fragment.record_count for fragment in fragments] == [1, 1]
        assert fragments[0].fragment_content_hash == (
            measurement_fragment_content_hash(
                header_content_hash=header.content_hash,
                record_content_hashes=first_append.record_content_hashes,
            )
        )
        assert fragments[0].dataset_content_hash is None
        assert fragments[1].fragment_content_hash == (
            measurement_fragment_content_hash(
                header_content_hash=header.content_hash,
                record_content_hashes=second_append.record_content_hashes,
            )
        )
        assert fragments[1].dataset_content_hash == expected_dataset_content_hash
        table, _, _ = runtime.application.runs.measurement_arrow(
            run_id,
            MeasurementArrowQuery(
                columns=(MeasurementArrowColumn(name="signal", variable_id="signal"),),
                diagnostics="full",
            ),
        )
        assert table.column("point_index").to_pylist() == [0, 1]
        if not segmented:
            assert table.column("signal").to_pylist() == [1.0, 2.0]
        else:
            assert table.column("signal").to_pylist() == [
                [[1.0, None, 2.0], None],
                [[1.0, None, 2.0, 3.0], [8.0, 9.0]],
            ]
            diagnostics = json.loads(
                table.column("signal__unavailable_metadata")[0].as_py()
            )
            assert diagnostics["segments"] == [
                {
                    "index": 1,
                    "reason": "missing",
                    "metadata": {"entity": "q1", "details": [{"attempt": 1}]},
                }
            ]
            with TestClient(runtime.app()) as client:
                response = client.post(
                    f"/api/v1/runs/{run_id}/measurements/traces/query",
                    json={
                        "observable_id": "signal",
                        "entities": [
                            {"id": "q1", "kind": "qubit"},
                            {"id": "q0", "kind": "qubit"},
                        ],
                        "max_series": 4,
                        "max_samples": 32,
                    },
                )
                assert response.status_code == 200
                preview = response.json()
                assert [
                    item["evidence"]["result_id"] for item in preview["series"]
                ] == ["q0-p0", "q1-p1", "q0-p1"]
                assert [item["y"] for item in preview["series"]] == [
                    [1.0, 2.0],
                    [8.0, 9.0],
                    [1.0, 2.0, 3.0],
                ]
                [failure] = preview["failures"]
                assert failure["evidence"]["result_id"] == "q1-p0"
                assert failure["reasons"] == ["missing"]


@pytest.mark.parametrize("durable", [False, True])
def test_measurement_acknowledgment_loss_and_replay_boundaries(
    tmp_path: Path, durable: bool
) -> None:
    with ExitStack() as stack:
        runtime = stack.enter_context(
            LocalDaemonRuntime(tmp_path, bootstrap_config=_config())
        )
        submission = _submission("ack-boundary", point_count=2)
        run_id = runtime.application.submit_run(submission).run_id
        executor = runtime.application.executor
        lease = executor.start_executor(
            run_id, ExecutorStartRequest(executor_id="before")
        )
        header = MeasurementDatasetHeader(
            run_id=run_id,
            recording_contract_fingerprint="ack-boundary.v1",
            expected_record_count=2,
            record_count_limit=2,
            dataset_schema=MeasurementDatasetSchema(
                dataset_id="raw-measurements",
                point_domain=MeasurementProductGridPointDomain(
                    axes=(
                        MeasurementPointDomainAxis(
                            id="point",
                            size=2,
                            source=MeasurementPointDomainValuesSource(
                                values=tuple(
                                    MeasurementScalar.create(dtype="int64", value=i)
                                    for i in range(2)
                                )
                            ),
                        ),
                    )
                ),
                dimensions=(MeasurementDimension(id="point", kind="point", size=2),),
                variables=(
                    MeasurementVariable(
                        id="signal", role="observable", dtype="float64", dims=("point",)
                    ),
                ),
            ),
        )
        executor.initialize_measurements(
            run_id, MeasurementHeaderCommand(lease_id=lease.lease_id, header=header)
        )

        def content(index: int) -> bytes:
            append = MeasurementDatasetAppend(
                run_id=run_id,
                header_content_hash=header.content_hash,
                acquisition_start=index,
                records=(
                    MeasurementRecord(
                        run_id=run_id,
                        point_index=index,
                        logical_point_id=f"point-{index}",
                        coordinates={},
                        observables={
                            "signal": MeasurementScalar.create(
                                dtype="float64", value=index + 1
                            )
                        },
                    ),
                ),
            )
            return encode_measurement_append(append, header.dataset_schema)

        first = content(0)
        receipt = executor.ingest_measurements(
            run_id, lease_id=lease.lease_id, content=first
        )
        assert receipt.received_record_count == 1
        assert receipt.durable_record_count == 0
        # Simulate a lost acknowledgment by retransmitting exactly the same bytes.
        with pytest.raises(BackendConflict, match="acquisition-log"):
            executor.ingest_measurements(run_id, lease_id=lease.lease_id, content=first)
        if durable:
            flushed = executor.flush_measurements(
                run_id, MeasurementFlushCommand(lease_id=lease.lease_id)
            )
            assert flushed.durable_record_count == 1
            retried = executor.flush_measurements(
                run_id, MeasurementFlushCommand(lease_id=lease.lease_id)
            )
            assert retried.durable_record_count == 1
            assert retried.durable_receipts == ()
            with pytest.raises(BackendConflict, match="acquisition-log"):
                executor.ingest_measurements(
                    run_id, lease_id=lease.lease_id, content=first
                )
        assert executor.run_coverage(run_id).completed_point_count == 0
        runtime.close()
        runtime = stack.enter_context(LocalDaemonRuntime(tmp_path))
        runtime.application.resolve_attention(
            run_id,
            AttentionResolutionCommand.continue_run(
                run_contract_fingerprint=submission.intent_content_hash
            ),
        )
        executor = runtime.application.executor
        resumed = executor.start_executor(
            run_id, ExecutorStartRequest(executor_id="after")
        )
        executor.initialize_measurements(
            run_id, MeasurementHeaderCommand(lease_id=resumed.lease_id, header=header)
        )
        preview = runtime.application.runs.measurement_live_preview(
            run_id, after_record_count=None
        )
        assert preview.received_record_count == int(durable)
        assert preview.durable_record_count == int(durable)
        assert executor.run_coverage(run_id).completed_point_count == 0
        with pytest.raises(BackendConflict):
            executor.ingest_measurements(run_id, lease_id=lease.lease_id, content=first)
        if durable:
            with pytest.raises(BackendConflict, match="acquisition-log"):
                executor.ingest_measurements(
                    run_id, lease_id=resumed.lease_id, content=first
                )
        else:
            replayed = executor.ingest_measurements(
                run_id, lease_id=resumed.lease_id, content=first
            )
            assert replayed.received_record_count == 1
        executor.ingest_measurements(
            run_id, lease_id=resumed.lease_id, content=content(1)
        )
        final = executor.flush_measurements(
            run_id, MeasurementFlushCommand(lease_id=resumed.lease_id)
        )
        assert final.durable_record_count == 2
        assert executor.run_coverage(run_id).completed_point_count == 0
        coverage = executor.advance_run_coverage(
            run_id,
            RunCoverageAdvanceCommand(
                lease_id=resumed.lease_id, start_index=0, point_count=2
            ),
        )
        assert coverage.completed_point_count == 2
        hashes = tuple(
            decode_measurement_append(
                content(i), header.dataset_schema
            ).record_content_hashes[0]
            for i in range(2)
        )
        sealed = executor.seal_measurements(
            run_id,
            MeasurementSealCommand(
                lease_id=resumed.lease_id,
                seal=MeasurementDatasetSeal(
                    run_id=run_id,
                    header_content_hash=header.content_hash,
                    record_count=2,
                    fragment_record_count=2 - int(durable),
                    fragment_content_hash=measurement_fragment_content_hash(
                        header_content_hash=header.content_hash,
                        record_content_hashes=hashes[int(durable) :],
                    ),
                ),
            ),
        )
        assert sealed.dataset_content_hash == measurement_dataset_content_hash(
            header_content_hash=header.content_hash, record_content_hashes=hashes
        )
        table, _, _ = runtime.application.runs.measurement_arrow(
            run_id,
            MeasurementArrowQuery(
                columns=(MeasurementArrowColumn(name="signal", variable_id="signal"),)
            ),
        )
        assert table.column("point_index").to_pylist() == [0, 1]
        assert table.column("signal").to_pylist() == [1.0, 2.0]


@pytest.mark.parametrize("order", [tuple(range(8)), tuple(reversed(range(8)))])
def test_entity_selected_arrow_http_preserves_run_identity_and_page_watermark(
    tmp_path: Path,
    order: tuple[int, ...],
) -> None:
    import json

    from scopecat_testkit.entity_reads import wide_entity_measurements

    with LocalDaemonRuntime(
        tmp_path / str(order[0]), bootstrap_config=_config()
    ) as runtime:
        client = TestClient(runtime.app())
        admission = runtime.application.submit_run(
            _submission("selected", point_count=2)
        )
        run_id = admission.run_id
        lease = runtime.application.executor.start_executor(
            run_id, ExecutorStartRequest(executor_id="reader-fixture")
        )
        raw = wide_entity_measurements(run_id=run_id, entity_order=order)
        header = MeasurementDatasetHeader(
            run_id=run_id,
            recording_contract_fingerprint="test.entity-selection.v1",
            dataset_schema=raw.dataset_schema,
            expected_record_count=2,
            record_count_limit=2,
        )
        runtime.application.executor.initialize_measurements(
            run_id, MeasurementHeaderCommand(lease_id=lease.lease_id, header=header)
        )

        def append(point: int) -> None:
            runtime.application.executor.ingest_measurements(
                run_id,
                lease_id=lease.lease_id,
                content=encode_measurement_append(
                    MeasurementDatasetAppend(
                        run_id=run_id,
                        header_content_hash=header.content_hash,
                        acquisition_start=point,
                        records=(raw.records[point],),
                    ),
                    header.dataset_schema,
                ),
            )
            runtime.application.executor.flush_measurements(
                run_id, MeasurementFlushCommand(lease_id=lease.lease_id)
            )
            runtime.application.executor.commit_recovery_groups(
                run_id,
                RunRecoveryGroupCommitCommand(
                    lease_id=lease.lease_id,
                    groups=(
                        RecoveryGroupCompletion(
                            schedule_fingerprint="test-runtime-schedule-v1",
                            group_id=f"point-{point}",
                            point_indices=(point,),
                            output_kind="measurement",
                            record_content_hashes=(
                                measurement_record_content_hash(raw.records[point]),
                            ),
                        ),
                    ),
                ),
            )
            runtime.application.executor.advance_run_coverage(
                run_id,
                RunCoverageAdvanceCommand(
                    lease_id=lease.lease_id,
                    start_index=point,
                    point_count=1,
                ),
            )

        append(0)
        query = {
            "columns": [{"name": "signal", "variable_id": "signal"}],
            "entity_selection": {
                "dimension_id": "entity",
                "entities": [
                    {"kind": "qubit", "id": "q7"},
                    {
                        "kind": "qubit",
                        "id": "absent",
                        "metadata": {"label": "requested"},
                    },
                    {"kind": "qubit", "id": "q0"},
                ],
            },
            "diagnostics": "full",
            "limit": 1,
        }
        response = client.post(f"/api/v1/runs/{run_id}/measurements/arrow", json=query)
        assert response.status_code == 200, response.text
        table = pa.ipc.open_stream(response.content).read_all()
        metadata = table.schema.metadata
        assert metadata is not None
        assert metadata[b"scopecat.run_id"] == run_id.encode()
        assert (
            metadata[b"scopecat.config_content_hash"]
            == _snapshot(runtime, run_id).config_content_hash.encode()
        )
        assert metadata[b"scopecat.snapshot_size"] == b"1"
        selected_schema = json.loads(metadata[b"scopecat.schema"])
        axis = next(
            item for item in selected_schema["dimensions"] if item["id"] == "entity"
        )
        assert [item["id"] for item in axis["index"]["values"]] == [
            "q7",
            "absent",
            "q0",
        ]
        assert axis["index"]["values"][0]["metadata"]["label"] == f"{run_id}:Q7"
        assert axis["index"]["values"][1]["metadata"] == {"label": "requested"}
        append(1)
        pinned = client.post(
            f"/api/v1/runs/{run_id}/measurements/arrow",
            json={
                **query,
                "offset": 1,
                "snapshot_size": 1,
            },
        )
        assert pinned.status_code == 200, pinned.text
        assert pa.ipc.open_stream(pinned.content).read_all().num_rows == 0
        fresh = client.post(
            f"/api/v1/runs/{run_id}/measurements/arrow", json={**query, "offset": 1}
        )
        assert fresh.status_code == 200, fresh.text
        assert fresh.headers["x-scopecat-snapshot-size"] == "2"
        assert pa.ipc.open_stream(fresh.content).read_all().num_rows == 1

        trace = client.post(
            f"/api/v1/runs/{run_id}/measurements/traces/query",
            json={
                "observable_id": "signal",
                "coordinate_id": "time",
                "entities": [
                    {"kind": "qubit", "id": "q7"},
                    {"kind": "qubit", "id": "absent"},
                    {"kind": "qubit", "id": "q0"},
                ],
            },
        )
        assert trace.status_code == 200, trace.text
        preview = trace.json()
        assert [item["entity"]["id"] for item in preview["series"]] == [
            "q7",
            "q0",
            "q7",
            "q0",
        ]
        assert [item["entity_index"] for item in preview["series"]] == [
            order.index(7),
            order.index(0),
        ] * 2
        assert [item["entity"]["id"] for item in preview["failures"]] == [
            "absent",
            "absent",
        ]
        assert all(item["entity_index"] is None for item in preview["failures"])
        assert all(item["evidence"] is None for item in preview["failures"])
        assert preview["series"][0]["evidence"]["command_id"] == f"{run_id}-q7"
