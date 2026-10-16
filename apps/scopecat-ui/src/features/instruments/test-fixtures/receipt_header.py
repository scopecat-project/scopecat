"""Exercise the UI header guards against the current Python receipt producer."""

from __future__ import annotations

import base64
import json

from scopecat.daemon.hardware_receipt_wire import encode_collect_receipt
from scopecat.kernel.problems import (
    ExternalLocation,
    ModelLocation,
    Problem,
    ProblemPhase,
    RuntimeLocation,
    StorageLocation,
)
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import MeasurementScalar, MeasurementUnavailable
from scopecat.sdk.attachments import AttachmentBundle
from scopecat.sdk.instruments.commands import CollectReceipt

values = {
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
for sample in (
    CollectReceipt(readback=InstrumentReadback(values=values)),
    receipt,
    CollectReceipt(status="not_collected", problems=receipt.problems),
):
    content = encode_collect_receipt(sample)
    bundle = AttachmentBundle.from_bytes(content)
    assert not bundle.attachments
    samples.append(
        {
            "header_json": bundle.header.decode("utf-8"),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
    )
print(json.dumps(samples, ensure_ascii=False))
