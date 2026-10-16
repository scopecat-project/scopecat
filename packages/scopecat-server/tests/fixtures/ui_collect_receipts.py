"""Exercise the UI receipt decoder against the current Python receipt producer."""

from __future__ import annotations

import base64
import json
from unittest.mock import Mock

import numpy as np
from fastapi.testclient import TestClient
from scopecat.daemon.hardware_receipt_wire import encode_collect_receipt
from scopecat.kernel.problems import (
    ExternalLocation,
    ModelLocation,
    Problem,
    ProblemPhase,
    RuntimeLocation,
    StorageLocation,
)
from scopecat.records.costs import OperationCostMeasurement
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementScalar,
    MeasurementUnavailable,
)
from scopecat.sdk.instruments.commands import CollectReceipt

from scopecat_server.http.transport import create_app

values: dict[str, MeasurementAcquisitionValue] = {
    "float": MeasurementScalar.create(value=1.5),
    "int_min": MeasurementScalar.create(value=-(2**63), dtype="int64"),
    "int_max": MeasurementScalar.create(value=2**63 - 1, dtype="int64"),
    "bool": MeasurementScalar.create(value=False, dtype="bool"),
    "string": MeasurementScalar.create(value="猫", dtype="string"),
    "complex": MeasurementScalar.create(value=complex(-0.0, 2.0), dtype="complex128"),
    "missing": MeasurementUnavailable.create(
        reason="missing", dtype="float64", unit=None, shape=(None, 2**53), metadata={}
    ),
    "missing_scalar": MeasurementUnavailable.create(
        reason="invalid", dtype="float64", unit=None, shape=(), metadata={}
    ),
}
values.update(
    {
        "trace": MeasurementArray.create(values=np.array([1.5, -2.0]), dtype="float64"),
        "int_array": MeasurementArray.create(
            values=np.array([-(2**63), 2**63 - 1], dtype=np.int64), dtype="int64"
        ),
        "bool_array": MeasurementArray.create(
            values=np.array([True, False]), dtype="bool"
        ),
        "string_array": MeasurementArray.create(
            values=np.array(["猫", ""]), dtype="string"
        ),
        "complex_array": MeasurementArray.create(
            values=np.array([complex(-0.0, 2.0)]), dtype="complex128"
        ),
        "matrix": MeasurementArray.create(
            values=np.array([[1.0, 2.0], [3.0, 4.0]]), dtype="float64"
        ),
        "empty": MeasurementArray.create(values=np.empty((0, 2)), dtype="float64"),
    }
)
locations = (
    ModelLocation(root="plan", path=("steps", 0)),
    StorageLocation(ref="data"),
    ExternalLocation(uri="file:test", row=1, column="A"),
    RuntimeLocation(run_id="run", point_index=0),
)
receipt = CollectReceipt(
    status="unknown",
    readback=InstrumentReadback(values=values),
    problems=tuple(
        Problem(
            code="example",
            message="Example",
            phase=phase,
            location=locations[index % len(locations)],
            related_locations=locations,
            details={"source": "producer", "nested": [1, None]},
        )
        for index, phase in enumerate(ProblemPhase)
    ),
)
samples = []
application = Mock()
# No lifespan: workers and devices must not be started by this contract fixture.
client = TestClient(create_app(application))
request: dict[str, object] = {
    "command_id": "collect-contract",
    "instrument_id": "vna-1",
    "interface_id": "test.receipt/v1",
    "component_path": [],
    "acquisition_id": "read",
    "result_ids": [],
}

for sample in (
    CollectReceipt(
        readback=InstrumentReadback(values=values),
        measured_cost=OperationCostMeasurement(
            source="test", acquire_seconds=0.25, uploaded_bytes=16
        ),
    ),
    receipt,
    CollectReceipt(status="not_collected", problems=receipt.problems),
):
    application.instruments.collect.return_value = sample
    response = client.post(
        "/api/v1/instrument-sessions/session-1/instruments/vna-1/collect",
        json=request,
        headers={"Accept": "application/vnd.scopecat.hardware-receipt.v2"},
    )
    assert response.status_code == 200, response.text
    assert application.instruments.collect.call_args.args[:2] == ("session-1", "vna-1")
    assert (
        application.instruments.collect.call_args.args[2].command_id
        == "collect-contract"
    )
    content = response.content
    assert content == encode_collect_receipt(sample)
    expected = json.loads(sample.model_dump_json())
    # The UI omits availability for fully available arrays.
    # Do not erase actual diagnostics.
    for value in (expected.get("readback") or {}).get("values", {}).values():
        if value.get("kind") == "array" and value.get("availability") is None:
            value.pop("availability", None)
    samples.append(
        {
            "expected": expected,
            "media_type": response.headers["content-type"],
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
    )
application.instruments.collect.return_value = CollectReceipt(
    readback=InstrumentReadback(
        values={
            "trace": MeasurementArray.create(
                values=[1.0, 0.0],
                availability=MeasurementArrayAvailability.create(valid=[True, False]),
            )
        }
    )
)
response = client.post(
    "/api/v1/instrument-sessions/session-1/instruments/vna-1/collect",
    json=request,
)
assert response.status_code == 500
assert "does not support measurement array availability" in response.json()["detail"]
assert "readback" not in response.json()
print(
    json.dumps(
        {
            "receipts": samples,
            "unsupported": {
                "status": response.status_code,
                "body": response.json(),
                "media_type": response.headers["content-type"],
            },
        },
        ensure_ascii=False,
    )
)
