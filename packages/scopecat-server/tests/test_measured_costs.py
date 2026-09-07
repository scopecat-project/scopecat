"""Saved operation cost projection and all receipt transports preserve facts."""

from scopecat.control.models import DurableEvent
from scopecat.daemon.hardware_receipt_wire import (
    decode_collect_receipt,
    encode_collect_receipt,
)
from scopecat.records.costs import OperationCostMeasurement, RunOperationCost
from scopecat.sdk.instruments import CollectReceipt, InstrumentReadback
from scopecat.sdk.instruments.authoring import DriverSuccess
from scopecat.sdk.instruments.driver_adapter import (
    project_acquisition_preparation_outcome,
)

from scopecat_server.services.measured_costs import measured_costs


def test_projection_keeps_unknown_cost_once_and_marks_partial_unavailable() -> None:
    cost = RunOperationCost(
        operation_id="trigger",
        instrument_id="device",
        operation="invoke",
        wall_seconds=0.2,
        status="unknown",
        connection_generation="worker-connection",
        connection_context="cold",
        measured=OperationCostMeasurement(
            source="adapter-clock",
            transfer_seconds=0.1,
            uploaded_bytes=32,
            reused_bytes=0,
            retained_bytes=32,
        ),
    )
    events = [
        DurableEvent(
            event_id=1,
            kind="run_hardware_batch_unknown",
            payload={"costs": [cost.model_dump(mode="json")]},
        ),
        DurableEvent(
            event_id=2,
            kind="unrelated",
            payload={"costs": [cost.model_dump(mode="json")]},
        ),
    ]
    summary = measured_costs(events, truncated=True)
    assert summary.operations == (cost,)
    assert summary.compilation is None
    assert summary.finalizations == ()
    assert summary.terminal_commit_seconds is None
    assert summary.truncated
    assert summary.operations[0].measured is not None
    assert summary.operations[0].measured.rendered_bytes is None


def test_public_prepare_outcome_preserves_measurement() -> None:
    measurement = OperationCostMeasurement(source="adapter", uploaded_bytes=0)
    receipt = project_acquisition_preparation_outcome(
        DriverSuccess(None, measured_cost=measurement)
    )
    assert receipt.measured_cost == measurement


def test_collect_http_bundle_preserves_measurement() -> None:
    receipt = CollectReceipt(
        readback=InstrumentReadback(values={}),
        measured_cost=OperationCostMeasurement(
            source="adapter", acquire_seconds=0.01, uploaded_bytes=0
        ),
    )
    assert decode_collect_receipt(encode_collect_receipt(receipt)) == receipt
