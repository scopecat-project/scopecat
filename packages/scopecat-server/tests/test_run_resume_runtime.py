# pyright: reportUnknownArgumentType=false

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest
import scopecat.daemon.execution as daemon_execution_module
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient
from scopecat.kernel.errors import RunFinalizationFailed
from scopecat.measurements.datasets import RAW_MEASUREMENTS_DATASET_ID
from scopecat.sdk.instruments import (
    DriverCatalog,
    InstrumentBackend,
    InstrumentConnectionContext,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)
from scopecat_testkit.signal_instruments import (
    TestSignalInstrument,
    TestSignalInstrumentProvider,
)
from scopecat_testkit.workflow_fixtures import load_config, load_invocation

from scopecat_server import LocalDaemonRuntime
from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.services.active_measurements import ActiveMeasurementStore
from scopecat_server.storage.sqlite.execution import (
    SQLiteMeasurementDatasetRepository,
)


class _TrackingSignalProvider:
    provider_id = TestSignalInstrumentProvider.provider_id

    def __init__(self) -> None:
        self._delegate = TestSignalInstrumentProvider()
        self.drivers: list[TestSignalInstrument] = []

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        return self._delegate.describe(context)

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> TestSignalInstrument:
        driver = self._delegate.connect(context)
        self.drivers.append(driver)
        return driver


def test_static_run_resumes_end_to_end_after_daemon_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        daemon_execution_module,
        "_MEASUREMENT_TRANSPORT_RECORD_LIMIT",
        1,
    )
    provider = _TrackingSignalProvider()
    run_id: str | None = None
    disconnected = False

    with (
        _runtime(tmp_path, provider) as first_runtime,
        TestClient(first_runtime.app()) as transport,
    ):
        first_runtime.application.executor._active_measurements = (
            ActiveMeasurementStore(record_limit=1)
        )

        def send(request: httpx2.Request) -> httpx2.Response:
            nonlocal disconnected, run_id
            if disconnected:
                raise httpx2.ReadError("executor disconnected", request=request)
            response = transport.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            translated = httpx2.Response(
                response.status_code,
                content=response.content,
                headers=dict(response.headers),
            )
            if request.url.path.endswith("/measurements/ingest"):
                run_id = request.url.path.removeprefix("/api/v1/runs/").removesuffix(
                    "/measurements/ingest"
                )
                disconnected = True
                raise httpx2.ReadError(
                    "measurement append response was lost",
                    request=request,
                )
            return translated

        lab = LabClient(_daemon_client(send))
        with pytest.raises(RunFinalizationFailed) as failed:
            lab.run(load_invocation())

        assert failed.value.terminal_persistence == "unconfirmed"
        assert isinstance(failed.value.__cause__, httpx2.TransportError)
        assert failed.value.execution_outcome.certainty == "indeterminate"
        assert run_id is not None
        coverage = first_runtime.application.executor.run_coverage(run_id)
        assert coverage.completed_point_count == 0

    assert run_id is not None
    with (
        _runtime(tmp_path, provider) as resumed_runtime,
        TestClient(resumed_runtime.app()) as transport,
    ):
        interrupted = resumed_runtime.application.runs.get_run(run_id)
        assert interrupted.control.state == "attention_required"
        [interrupted_segment] = resumed_runtime.application.executor.execution_segments(
            run_id
        ).items
        assert interrupted_segment.start_point_count == 0
        assert interrupted_segment.end_point_count == 0
        assert interrupted_segment.result == "interrupted"

        resumed = LabClient(_daemon_client(_send_through(transport))).resume(
            run_id,
            load_invocation(),
            executor_id="notebook-2",
        )

        assert resumed.status == "completed"
        assert len(resumed.measurements()) == 3
        segments = resumed.execution_segments().items
        assert [
            (segment.ordinal, segment.start_point_count, segment.end_point_count)
            for segment in segments
        ] == [
            (1, 0, 3),
            (0, 0, 0),
        ]
        assert [segment.result for segment in segments] == ["succeeded", "interrupted"]

        fragments = SQLiteMeasurementDatasetRepository(
            resumed_runtime.application.executor._runs,
            run_id=run_id,
        ).measurement_fragments()
        assert [fragment.segment_id for fragment in fragments] == [
            segments[1].segment_id,
            segments[0].segment_id,
        ]
        assert [fragment.acquisition_start for fragment in fragments] == [0, 1]
        assert [fragment.record_count for fragment in fragments] == [1, 3]
        dataset = resumed.content("dataset", RAW_MEASUREMENTS_DATASET_ID)
        assert fragments[-1].dataset_content_hash == dataset.content_hash

    assert [len(driver.collect_requests) for driver in provider.drivers] == [1, 3]


def _runtime(
    root: Path,
    provider: _TrackingSignalProvider,
) -> LocalDaemonRuntime:
    return LocalDaemonRuntime(
        root,
        bootstrap_config=load_config(),
        instrument_endpoint=LocalInstrumentBackendEndpoint(
            InstrumentBackend(
                provider=provider,
                driver_catalog=DriverCatalog(provider_id=provider.provider_id),
            )
        ),
    )


def _send_through(
    transport: TestClient,
) -> Callable[[httpx2.Request], httpx2.Response]:
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

    return send


def _daemon_client(
    send: Callable[[httpx2.Request], httpx2.Response],
) -> DaemonClient:
    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )
