from datetime import UTC, datetime
from unittest.mock import Mock

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.wire import RunHardwareBatchCommand
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementPartitionedArray,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareBatch,
    RunHardwareBatchReceipt,
    RunHardwareInvoke,
    RunHardwareValue,
)

from scopecat_server.http.transport import create_app


@pytest.mark.parametrize("partitioned", [False, True])
def test_run_endpoint_and_daemon_consumer_reject_unavailable_samples(
    partitioned: bool,
) -> None:
    array = MeasurementArray.create(
        values=[1.0, 0.0],
        availability=MeasurementArrayAvailability.create(valid=[True, False]),
    )
    value = (
        MeasurementPartitionedArray.create(partitions=(array,), axis=0)
        if partitioned
        else array
    )
    application = Mock()
    now = datetime(2026, 10, 16, tzinfo=UTC)
    application.instruments.execute_run_hardware.return_value = RunHardwareBatchReceipt(
        operation_id="batch-1",
        completed_effect_ids=("read-1",),
        values=(
            RunHardwareValue(
                value_id="trace",
                value=value,
                evidence=InstrumentAcquisitionEvidence(
                    command_id="read-1",
                    instrument_id="scope-1",
                    interface_id="test.scope/v1",
                    acquisition_id="read",
                    result_id="trace",
                    started_at=now,
                    completed_at=now,
                ),
            ),
        ),
    )
    # No lifespan: only the real route/codec/consumer, never workers or devices.
    transport = TestClient(create_app(application))

    def send(request: httpx2.Request) -> httpx2.Response:
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        assert response.status_code == 500
        assert response.headers["content-type"] == "application/json"
        assert "readback" not in response.json()
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    command = RunHardwareBatchCommand(
        lease_id="lease-1",
        sequence=0,
        batch=RunHardwareBatch(
            operation_id="batch-1",
            actions=(
                RunHardwareInvoke(
                    effect_id="read-1",
                    instrument_id="scope-1",
                    resource_id="scope-1",
                    interface_id="test.scope/v1",
                    operation_id="read",
                ),
            ),
        ),
    )
    with (
        DaemonClient(
            "http://testserver", transport=httpx2.MockTransport(send)
        ) as client,
        pytest.raises(httpx2.HTTPStatusError, match="availability"),
    ):
        client.execute_run_hardware("run-1", command)
    application.instruments.execute_run_hardware.assert_called_once_with(
        "run-1", command
    )
