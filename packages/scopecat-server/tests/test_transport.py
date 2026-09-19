from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import (
    DurableEvent,
    EventPage,
    RunPlanSummary,
)
from scopecat.daemon.views import (
    MeasurementTracePreview,
    MeasurementTracePreviewQuery,
    MeasurementTraceSeries,
    RunDetail,
)
from scopecat.daemon.wire import (
    ExecutorLease,
    ExecutorStartRequest,
    InstrumentContractCatalogRequest,
    InstrumentDriverProbeCommand,
    InstrumentDriverProbeReceipt,
    InstrumentSessionLeaseReceipt,
    RunAdmission,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
    RunSubmission,
)
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.records.config import (
    ConfigProfileSnapshot,
    InstrumentBindingSpec,
    VirtualInstrumentConnection,
    config_content_hash,
)
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.contracts import InstrumentDescription

from scopecat_server import (
    BackendConflict,
    BackendNotFound,
    DaemonHealth,
    create_app,
)
from scopecat_server.services.application import DaemonApplication
from scopecat_server.storage.sqlite.connection import SQLiteBusyError

_NOW = datetime(2026, 7, 23, 9, tzinfo=UTC)
_HASH = f"sha256:{'a' * 64}"
_REQUEST = RunRequest(experiment_id="request-1")
_CONFIG_FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "core"
    / "simple_scan"
    / "config-snapshot.json"
)


class FakeApplication:
    def __init__(self) -> None:
        self.runs = FakeRuns(
            events=(
                DurableEvent(
                    event_id=1,
                    run_id="run-1",
                    kind="run_admitted",
                    payload={"state": "accepted"},
                    occurred_at=_NOW,
                ),
                DurableEvent(
                    event_id=2,
                    run_id="run-1",
                    kind="run_state_changed",
                    payload={"state": "running"},
                    occurred_at=_NOW + timedelta(seconds=1),
                ),
            )
        )
        self.executor = FakeExecutor()
        self.instruments = FakeInstruments()
        self.last_submission: RunSubmission | None = None

    def health(self) -> DaemonHealth:
        return DaemonHealth(
            status="ok",
            project_id="test-project",
            deployment_id="test-deployment",
            project_name="test-lab",
            project_root="/projects/test-lab",
            data_root="/projects/test-lab/.scopecat",
            deployment_root="/projects/test-lab/.scopecat",
        )

    def submit_run(self, submission: RunSubmission) -> RunAdmission:
        if submission.submission_id == "duplicate":
            raise BackendConflict("submission already exists")
        self.last_submission = submission
        return _wire_admission(submission.submission_id)


class FakeRuns:
    def __init__(self, *, events: tuple[DurableEvent, ...]) -> None:
        self.events = events
        self.event_afters: list[int | None] = []
        self.trace_preview_query: tuple[str, MeasurementTracePreviewQuery] | None = None

    def get_run(self, run_id: str) -> RunDetail:
        raise BackendNotFound(f"run was not found: {run_id}")

    def measurement_trace_preview(
        self,
        run_id: str,
        query: MeasurementTracePreviewQuery,
    ) -> MeasurementTracePreview:
        self.trace_preview_query = (run_id, query)
        series = MeasurementTraceSeries(
            point_index=0,
            label="Point 0",
            x=(0.0, 1.0),
            y=(1.0, 0.5),
            source_sample_count=2,
            available_sample_count=2,
        )
        return MeasurementTracePreview(
            dimension_id="sample",
            recording_group_id=query.recording_group_id,
            coordinate_id="frequency",
            observable_id=query.observable_id or "signal",
            value_mode=query.value_mode or "magnitude",
            downsampling=query.downsampling,
            layout="overlay",
            series=(series,),
            selected_series_count=1,
            inspected_series_count=1,
            returned_series_count=1,
            source_sample_count=2,
            returned_sample_count=2,
        )

    def list_events(
        self,
        *,
        limit: int,
        after: int | None,
        run_id: str | None,
        latest: bool,
    ) -> EventPage:
        del latest
        self.event_afters.append(after)
        selected = tuple(
            event
            for event in self.events
            if event.event_id > (after or 0)
            and (run_id is None or event.run_id == run_id)
        )
        return EventPage(items=selected[:limit])


class FakeExecutor:
    def __init__(self) -> None:
        self.last_start: ExecutorStartRequest | None = None

    def start_executor(
        self,
        run_id: str,
        request: ExecutorStartRequest,
    ) -> ExecutorLease:
        if run_id != "run-1":
            raise BackendNotFound(f"run was not found: {run_id}")
        self.last_start = request
        return _executor_lease()


class FakeInstruments:
    def __init__(self) -> None:
        self.last_run_provision: tuple[str, RunInstrumentProvisionCommand] | None = None
        self.last_contract_config: ConfigProfileSnapshot | None = None
        self.last_driver_probe: InstrumentDriverProbeCommand | None = None
        self.last_renewed_session_id: str | None = None
        self.renewal_unavailable = False

    def resolve_instrument_contracts(
        self,
        config: ConfigProfileSnapshot,
    ) -> InstrumentContractCatalog:
        self.last_contract_config = config
        return InstrumentContractCatalog(
            config_content_hash=config_content_hash(config),
            provider_id=None,
        )

    def driver_catalog(self) -> DriverCatalog:
        return DriverCatalog(provider_id="tests.fake")

    def probe_driver(
        self,
        command: InstrumentDriverProbeCommand,
    ) -> InstrumentDriverProbeReceipt:
        self.last_driver_probe = command
        return InstrumentDriverProbeReceipt(
            status="connected",
            description=InstrumentDescription(
                instrument_id=command.binding.id,
                implementation_id=command.binding.driver_id,
                implementation_version="v1",
            ),
        )

    def provision_run(
        self,
        run_id: str,
        command: RunInstrumentProvisionCommand,
    ) -> RunInstrumentProvisionReceipt:
        self.last_run_provision = (run_id, command)
        return RunInstrumentProvisionReceipt(
            run_id=run_id,
            operation_id=command.operation_id,
            status="ready",
        )

    def renew_session(self, session_id: str) -> InstrumentSessionLeaseReceipt:
        if self.renewal_unavailable:
            raise SQLiteBusyError("project database writer is busy")
        self.last_renewed_session_id = session_id
        return InstrumentSessionLeaseReceipt(
            session_id=session_id,
            renewed_at=_NOW,
            expires_at=_NOW + timedelta(minutes=1),
        )


def _create_test_app(
    backend: FakeApplication,
    *,
    static_dir: Path | None = None,
    request_shutdown: Callable[[str], bool] | None = None,
) -> FastAPI:
    # Each test supplies only the service methods reachable through its route.
    return create_app(
        cast("DaemonApplication", cast("object", backend)),
        static_dir=static_dir,
        request_shutdown=request_shutdown,
    )


def test_shutdown_route_requires_the_private_daemon_token() -> None:
    requested_tokens: list[str] = []

    def request_shutdown(token: str) -> bool:
        requested_tokens.append(token)
        return token == "valid-token"  # noqa: S105 - fixture credential

    client = TestClient(
        _create_test_app(FakeApplication(), request_shutdown=request_shutdown)
    )

    missing = client.post("/api/v1/shutdown")
    rejected = client.post(
        "/api/v1/shutdown",
        headers={"X-Scopecat-Shutdown-Token": "invalid-token"},
    )
    accepted = client.post(
        "/api/v1/shutdown",
        headers={"X-Scopecat-Shutdown-Token": "valid-token"},
    )

    assert missing.status_code == 422
    assert rejected.status_code == 403
    assert accepted.status_code == 202
    assert requested_tokens == ["invalid-token", "valid-token"]


def test_run_submission_and_backend_error_mapping() -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))

    response = client.post("/api/v1/runs", json=_submission("submission-1"))
    conflict = client.post("/api/v1/runs", json=_submission("duplicate"))
    missing = client.get("/api/v1/runs/missing")
    invalid = client.post(
        "/api/v1/runs",
        json={**_submission("invalid"), "unexpected": True},
    )

    assert response.status_code == 201
    assert response.json()["snapshot"]["run_id"] == "run-1"
    assert isinstance(backend.last_submission, RunSubmission)
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "submission already exists"}
    assert missing.status_code == 404
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    "selection",
    [{"entity_indices": [0, 2]}, {"entities": [{"kind": "qubit", "id": "q2"}]}],
)
def test_trace_preview_route_forwards_typed_query_and_validates_selection(
    selection: dict[str, object],
) -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))
    payload = {
        "recording_group_id": "readout",
        "observable_id": "signal",
        "coordinate_id": "frequency",
        "fixed_axis_indices": {"bias": 1},
        **selection,
        "max_series": 4,
        "max_samples": 128,
        "value_mode": "real",
        "downsampling": "minmax",
    }

    response = client.post(
        "/api/v1/runs/run-1/measurements/traces/query",
        json=payload,
    )
    invalid = client.post(
        "/api/v1/runs/run-1/measurements/traces/query",
        json={},
    )
    invalid_value_mode = client.post(
        "/api/v1/runs/run-1/measurements/traces/query",
        json={"observable_id": "signal", "value_mode": "raw"},
    )
    duplicate_entities = client.post(
        "/api/v1/runs/run-1/measurements/traces/query",
        json={"observable_id": "signal", "entity_indices": [0, 0]},
    )

    assert response.status_code == 200
    assert response.json()["series"][0]["x"] == [0.0, 1.0]
    assert invalid.status_code == 422
    assert invalid_value_mode.status_code == 422
    assert duplicate_entities.status_code == 422
    assert backend.runs.trace_preview_query == (
        "run-1",
        MeasurementTracePreviewQuery.model_validate(payload),
    )


def test_event_replay_and_sse_resume_from_durable_event_id() -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))

    replay = client.get("/api/v1/events", params={"after": 1, "run_id": "run-1"})
    stream = client.get(
        "/api/v1/events/stream",
        params={"follow": "false"},
        headers={"Last-Event-ID": "1"},
    )
    reconnect = client.get(
        "/api/v1/events/stream",
        params={"after": 0, "follow": "false"},
        headers={"Last-Event-ID": "2"},
    )

    assert [item["event_id"] for item in replay.json()["items"]] == [2]
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "id: 2\nevent: project\ndata: " in stream.text
    assert '"event_id":2' in stream.text
    assert reconnect.text == ""
    assert backend.runs.event_afters[-1] == 2


def test_executor_path_is_the_start_request_run_identity() -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))

    response = client.post(
        "/api/v1/runs/other/executor/start",
        json=ExecutorStartRequest(
            executor_id="notebook-1",
        ).model_dump(mode="json"),
    )

    assert response.status_code == 404
    assert backend.executor.last_start is None


def test_run_instrument_provision_route_preserves_fencing_command() -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))
    command = RunInstrumentProvisionCommand(
        lease_id="lease-1",
        operation_id="lifecycle.provide-instruments",
    )

    response = client.post(
        "/api/v1/runs/run-1/instruments/provision",
        json=command.model_dump(mode="json"),
    )

    assert response.status_code == 200
    assert RunInstrumentProvisionReceipt.model_validate(response.json()).status == (
        "ready"
    )
    assert backend.instruments.last_run_provision == ("run-1", command)


def test_instrument_contract_route_resolves_the_exact_config_body() -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))
    command = InstrumentContractCatalogRequest(config=_config())

    response = client.post(
        "/api/v1/instrument-contracts/resolve",
        json=command.model_dump(mode="json"),
    )

    assert response.status_code == 200
    catalog = InstrumentContractCatalog.model_validate(response.json())
    assert catalog.config_content_hash == config_content_hash(command.config)
    assert backend.instruments.last_contract_config == command.config


def test_driver_catalog_route_returns_project_backend_catalog() -> None:
    client = TestClient(_create_test_app(FakeApplication()))

    response = client.get("/api/v1/instrument-drivers")

    assert response.status_code == 200
    assert DriverCatalog.model_validate_json(response.content) == DriverCatalog(
        provider_id="tests.fake"
    )


def test_driver_probe_route_forwards_the_candidate_binding() -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))
    command = InstrumentDriverProbeCommand(
        binding=InstrumentBindingSpec(
            id="source-0",
            driver_id="tests.signal",
            connection=VirtualInstrumentConnection(),
        )
    )

    response = client.post(
        "/api/v1/instrument-drivers/probe",
        json=command.model_dump(mode="json"),
    )

    assert response.status_code == 200
    receipt = InstrumentDriverProbeReceipt.model_validate_json(response.content)
    assert receipt.status == "connected"
    assert backend.instruments.last_driver_probe == command


def test_instrument_session_heartbeat_renews_the_lease() -> None:
    backend = FakeApplication()
    client = TestClient(_create_test_app(backend))

    response = client.post("/api/v1/instrument-sessions/session-1/heartbeat")

    assert response.status_code == 200
    receipt = InstrumentSessionLeaseReceipt.model_validate(response.json())
    assert receipt.session_id == "session-1"
    assert backend.instruments.last_renewed_session_id == "session-1"


def test_temporary_backend_unavailability_is_retryable() -> None:
    backend = FakeApplication()
    backend.instruments.renewal_unavailable = True
    client = TestClient(_create_test_app(backend))

    response = client.post("/api/v1/instrument-sessions/session-1/heartbeat")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {"detail": "project database writer is busy"}


def test_static_dist_serves_files_and_spa_without_shadowing_api(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text("<main>Scopecat GUI</main>")
    (tmp_path / "app.js").write_text("window.SCOPECAT = true")
    client = TestClient(
        _create_test_app(FakeApplication(), static_dir=tmp_path),
    )

    assert client.get("/").text == "<main>Scopecat GUI</main>"
    assert client.get("/runs/run-1").text == "<main>Scopecat GUI</main>"
    assert client.get("/app.js").text == "window.SCOPECAT = true"
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/unknown").status_code == 404


def _config() -> ConfigProfileSnapshot:
    return load_config_snapshot_document(_CONFIG_FIXTURE)


def _wire_admission(submission_id: str) -> RunAdmission:
    return RunAdmission(
        submission_id=submission_id,
        snapshot=_accepted_manifest(),
    )


def _submission(submission_id: str) -> dict[str, object]:
    return RunSubmission(
        scientific_binding=bind_scientific_evidence(
            catalog_id="test", config=_config(), samples=(), sample_revisions={}
        ),
        submission_id=submission_id,
        config=_config(),
        request=_REQUEST,
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
        ),
    ).model_dump(mode="json")


def _executor_lease() -> ExecutorLease:
    return ExecutorLease(
        lease_id="lease-1",
        segment_id="segment-1",
        run_id="run-1",
        executor_id="notebook-1",
        issued_at=_NOW,
        expires_at=_NOW + timedelta(seconds=30),
        heartbeat_interval_seconds=10,
    )


def _accepted_manifest() -> RunSnapshot:
    return RunSnapshot(
        scientific_binding=ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash=_HASH,
            setup_content_hash="sha256:" + "0" * 64,
        ),
        run_id="run-1",
        created_at=_NOW,
        config_content_hash=_HASH,
    )
