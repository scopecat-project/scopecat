from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from scopecat.daemon.hardware_receipt_wire import (
    HardwareReceiptWireError,
    decode_collect_receipt,
    decode_run_hardware_receipt,
    encode_collect_receipt,
    encode_run_hardware_receipt,
)
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.state import StateValue
from scopecat.records.instrument import (
    InstrumentReadback,
    InstrumentStateSetting,
    state_member_target,
)
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementArray,
    MeasurementScalar,
    MeasurementUnavailable,
)
from scopecat.sdk.instruments.commands import CollectReceipt
from scopecat.sdk.instruments.execution import (
    RunHardwareBatchReceipt,
    RunHardwareStateActionReceipt,
    RunHardwareValue,
)
from scopecat.sdk.instruments.members import InterfaceRef

_NOW = datetime(2026, 8, 14, tzinfo=UTC)


def test_collect_receipt_keeps_numeric_arrays_out_of_json_header() -> None:
    array = MeasurementArray.create(
        values=np.asarray([1 + 2j, 3 - 4j], dtype=np.complex128),
        dtype="complex128",
        unit="V",
        metadata={"source": "driver"},
    )
    receipt = CollectReceipt(
        readback=InstrumentReadback(
            values={
                "trace": array,
                "count": MeasurementScalar.create(value=2, dtype="int64"),
            },
            metadata={"batch": 1},
        ),
        metadata={"elapsed_seconds": 0.25},
    )

    content = encode_collect_receipt(receipt)
    restored = decode_collect_receipt(content)

    assert b'"real"' not in content
    assert restored.status == "collected"
    assert restored.metadata == receipt.metadata
    assert restored.readback is not None
    assert receipt.readback is not None
    assert restored.readback.metadata == receipt.readback.metadata
    restored_array = restored.readback.values["trace"]
    assert isinstance(restored_array, MeasurementArray)
    assert restored_array.dtype == "complex128"
    assert restored_array.unit == "V"
    assert restored_array.metadata == array.metadata
    np.testing.assert_array_equal(restored_array.values, array.values)
    assert restored.readback.values["count"] == receipt.readback.values["count"]


def test_run_hardware_receipt_round_trips_arrays_and_unavailable_values() -> None:
    receipt = RunHardwareBatchReceipt(
        completed_effect_ids=("invoke-1", "collect-1"),
        operation_id="batch-1",
        state_actions=(
            RunHardwareStateActionReceipt(
                operation_id="static-z.apply",
                instrument_id="awg-0",
                point_index=3,
                status="applied",
                assignments=(
                    InstrumentStateSetting(
                        target=state_member_target(
                            InterfaceRef("test.static_z/v1").property("bias")
                        ),
                        value=StateValue(Quantity(0.15, "V")),
                    ),
                ),
                metadata={"hold_mode": "end_with_keep"},
            ),
        ),
        values=(
            RunHardwareValue(
                point_index=3,
                value_id="iq",
                value=MeasurementArray.create(
                    values=np.asarray([0.25 - 0.5j], dtype=np.complex128),
                    dtype="complex128",
                    unit="V",
                ),
                evidence=_evidence("iq"),
            ),
            RunHardwareValue(
                point_index=3,
                value_id="missing",
                value=MeasurementUnavailable.create(
                    reason="missing",
                    dtype="float64",
                    unit="V",
                    shape=(4,),
                    metadata={"detail": "no lock"},
                ),
                evidence=_evidence("missing"),
            ),
        ),
    )

    restored = decode_run_hardware_receipt(encode_run_hardware_receipt(receipt))

    assert restored.operation_id == receipt.operation_id
    assert restored.completed_effect_ids == receipt.completed_effect_ids
    assert restored.state_actions == receipt.state_actions
    assert restored.values[0].point_index == 3
    assert restored.values[0].evidence == receipt.values[0].evidence
    original_array = receipt.values[0].value
    restored_array = restored.values[0].value
    assert isinstance(original_array, MeasurementArray)
    assert isinstance(restored_array, MeasurementArray)
    np.testing.assert_array_equal(restored_array.values, original_array.values)
    assert restored.values[1] == receipt.values[1]


def test_hardware_receipt_rejects_truncated_attachments() -> None:
    content = encode_collect_receipt(
        CollectReceipt(
            readback=InstrumentReadback(
                values={
                    "trace": MeasurementArray.create(
                        values=np.asarray([1.0, 2.0]),
                        unit="V",
                    )
                }
            )
        )
    )

    with pytest.raises(HardwareReceiptWireError, match="truncated"):
        decode_collect_receipt(content[:-1])


def _evidence(result_id: str) -> InstrumentAcquisitionEvidence:
    return InstrumentAcquisitionEvidence(
        command_id="collect-1",
        instrument_id="digitizer-0",
        interface_id="test.digitizer/v1",
        component_path=("input-a",),
        acquisition_id="fetch",
        result_id=result_id,
        started_at=_NOW,
        completed_at=_NOW,
    )
