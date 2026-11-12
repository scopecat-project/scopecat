"""An idle worker receiver must not own already-delivered receipt payloads."""

import gc
import weakref
from threading import Event, RLock, Thread
from typing import cast, override

import pytest
from pydantic import BaseModel

from scopecat_server.instruments import worker
from scopecat_server.instruments.retained_receipt import RawCollectReceipt


class _Connection:
    def send_bytes(self, buf: bytes | memoryview) -> None:
        raise AssertionError(f"unused send of {len(buf)} bytes")

    def recv_bytes(self, maxlength: int | None = None) -> bytes:
        raise AssertionError(f"unused receive with limit {maxlength}")

    def poll(self, timeout: float = 0.0) -> bool:
        raise AssertionError(f"unused poll with timeout {timeout}")

    def close(self) -> None:
        pass


def test_idle_receiver_releases_delivered_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Payload:
        pass

    payload = Payload()
    reference = weakref.ref(payload)
    holder = [payload]
    del payload
    idle, stop = Event(), Event()
    delivered = worker._PendingResponse()
    endpoint = worker.SubprocessInstrumentBackendEndpoint.__new__(
        worker.SubprocessInstrumentBackendEndpoint
    )
    endpoint._connection = _Connection()
    endpoint._state_lock = RLock()
    endpoint._pending = {1: delivered}
    endpoint._closed = False
    calls = 0

    def receive(
        _connection: object, _model_type: type[BaseModel]
    ) -> worker._RpcResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return worker._RpcResponse(
                request_id=1, status="ok", attachment_count=0, frame_kind="collect"
            )
        idle.set()
        assert stop.wait(5)
        raise EOFError

    monkeypatch.setattr(worker, "_recv_model", receive)

    def receive_collect(
        _connection: object,
        _attachment_count: int,
        *,
        maximum_receipt_bytes: int | None = None,
    ) -> RawCollectReceipt:
        assert maximum_receipt_bytes is None
        # Only receiver-reference lifetime is under test; this weak-referenceable
        # sentinel intentionally substitutes for a receipt without domain access.
        return cast("RawCollectReceipt", cast("object", holder.pop()))

    monkeypatch.setattr(worker, "_receive_collect", receive_collect)
    thread = Thread(target=endpoint._receive_responses)
    thread.start()
    try:
        assert idle.wait(5)
        assert delivered.received is not None
        delivered.received = None
        gc.collect()
        assert reference() is None
    finally:
        endpoint._closed = True
        stop.set()
        thread.join(5)
    assert not thread.is_alive()


def test_receive_rejects_unadmitted_payload_before_reading_attachment() -> None:
    import json
    from hashlib import sha256

    header = json.dumps(
        {
            "protocol_version": 2,
            "receipt": {"status": "collected", "readback": {"values": {}}},
            "arrays": [{"request_id": "value", "dtype": "float64", "shape": [1]}],
            "attachments": [
                {
                    "index": 0,
                    "request_id": "value",
                    "size_bytes": 8,
                    "sha256": sha256(b"\0" * 8).hexdigest(),
                }
            ],
        }
    ).encode()

    class Connection(_Connection):
        calls = 0

        @override
        def recv_bytes(self, maxlength: int | None = None) -> bytes:
            assert maxlength is not None
            self.calls += 1
            assert self.calls == 1, "must reject manifest before reading attachment"
            return header

    connection = Connection()
    with pytest.raises(ValueError, match="admitted byte envelope"):
        worker._receive_collect(connection, 1, maximum_receipt_bytes=1)
    assert connection.calls == 1


def test_each_received_frame_is_limited_to_manifest_not_global_maximum() -> None:
    class Connection(_Connection):
        def __init__(self) -> None:
            self.limits: list[int | None] = []

        @override
        def recv_bytes(self, maxlength: int | None = None) -> bytes:
            assert maxlength is not None
            self.limits.append(maxlength)
            return b"\0" * maxlength

    connection = Connection()
    assert worker._receive_attachments(
        connection, attachment_count=2, declared_sizes=(8, 0)
    ) == (b"\0" * 8, b"")
    assert connection.limits == [8, 0]
