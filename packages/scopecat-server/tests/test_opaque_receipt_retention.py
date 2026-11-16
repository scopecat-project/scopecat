"""Bounded synthetic graph-heavy originals stay owned without expanding views."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Literal

import pytest
from scopecat.control.models import InstrumentSession
from scopecat.sdk.instruments.commands import (
    CollectAxisRequest,
    CollectCommand,
    CollectResultRequest,
    InteractiveCollectIntent,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription

from scopecat_server.instruments._runtime_state import CollectReceiptReplay
from scopecat_server.instruments.pending_collections import (
    PendingCollections,
    RetainedCollection,
)
from scopecat_server.instruments.retained_receipt import (
    RawCollectReceipt,
    ReceiptMaterializationTooLarge,
)

_KEY = ("session", "instrument", "opaque")


def _raw(
    *, dtype: str = "float64", shape: int = 1, metadata_nodes: int = 20000
) -> RawCollectReceipt:
    attachment = struct.pack("<d", 2.5)
    # 60 KB of valid source JSON, not a large payload allocation.
    metadata = b'{"many":[' + b",".join([b"{}"] * metadata_nodes) + b"]}"
    header = (
        b'{"protocol_version":2,"receipt":{"status":"collected",'
        b'"readback":{"values":{}},"metadata":' + metadata + b"},"
        b'"arrays":[{"request_id":"value","dtype":"float64","unit":null,"shape":[1],"metadata":{}}],'
        b'"attachments":[{"index":0,"request_id":"value","size_bytes":8,"sha256":"'
        + sha256(attachment).hexdigest().encode()
        + b'"}]}'
    )
    header = header.replace(
        b'"dtype":"float64"', b'"dtype":' + json.dumps(dtype).encode()
    )
    header = header.replace(b'"shape":[1]', f'"shape":[{shape}]'.encode())
    return RawCollectReceipt.from_worker_frames(header, (attachment,), defer_view=True)


def _command() -> CollectCommand:
    return CollectCommand(
        command_id="opaque",
        instrument_id="instrument",
        point_count=1,
        requests=[
            CollectResultRequest(
                id="value",
                interface_id="test.signal/v1",
                acquisition_id="sample",
                result_id="value",
                dtype="float64",
                dimensions=[CollectAxisRequest(id="sample", kind="index", size=1)],
            )
        ],
    )


def _collection(raw: RawCollectReceipt) -> RetainedCollection:
    return (
        InstrumentSession.model_construct(),
        InstrumentDescription.model_construct(),
        CollectReceiptReplay(
            intent=InteractiveCollectIntent.model_construct(retain_result=True),
            command=_command(),
            receipt=raw,
        ),
    )


@dataclass
class _Snapshot:
    storage_policy: Literal["memory_only", "allow_temporary_disk"]
    revision: int = 0


class _Settings:
    def __init__(self, policy: Literal["memory_only", "allow_temporary_disk"]) -> None:
        self.coordination_lock = RLock()
        self.snapshot = _Snapshot(policy)

    def read(self) -> _Snapshot:
        return self.snapshot


def _pending(tmp_path: Path, *, disk: bool = False) -> PendingCollections:
    return PendingCollections(
        settings=_Settings("allow_temporary_disk" if disk else "memory_only"),
        spool_root=tmp_path,
        memory_bytes=2 * 1024 * 1024,
        work_bytes=4 * 1024 * 1024,
        disk_bytes=2 * 1024 * 1024,
        parser_bytes=64 * 1024,
    )


def test_graph_heavy_original_roundtrips_without_structured_view() -> None:
    raw = _raw()
    assert (
        raw.validate_command(_command())[0].code
        == "direct_result_contract_view_unavailable"
    )
    raw = raw.with_view()
    assert raw.status == "collected"
    assert not raw.view_available
    assert raw.metadata_view == {}
    assert raw.validate_command(_command()) == []
    assert raw.array_values("value", 0, 1) == (2.5,)
    original = json.loads(raw.header)
    assert original["metadata"] == {"many": [{} for _ in range(20000)]}
    for retained in (
        raw.with_view(),
        RawCollectReceipt.from_http_buffer(raw.to_bytes()),
    ):
        assert not retained.view_available
        assert retained.header == raw.header
        assert retained.to_bytes() == raw.to_bytes()
        assert retained.validate_command(_command()) == []
        with pytest.raises(
            ReceiptMaterializationTooLarge, match="original remains retained"
        ):
            retained.require_view()
        with pytest.raises(ReceiptMaterializationTooLarge):
            retained.materialize(max_bytes=1024 * 1024)
        assert retained.to_bytes() == raw.to_bytes()


def test_view_construction_failure_cannot_revoke_pending_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = _pending(tmp_path)
    raw = _raw()

    def fail(_self: RawCollectReceipt) -> RawCollectReceipt:
        raise ValueError("injected view construction failure")

    monkeypatch.setattr(RawCollectReceipt, "with_view", fail)
    with pending.reserve(_KEY, expected_bytes=128 * 1024):
        retained = pending.retain(_KEY, _collection(raw))
    assert retained.contract_error == "injected view construction failure"
    assert retained.to_bytes() == raw.to_bytes()
    assert pending.require(_KEY)[2].receipt is retained
    with pending.lease(_KEY) as collection:
        assert collection[2].receipt is retained
        assert (
            retained.validate_command(_command())[0].code
            == "direct_result_contract_invalid"
        )
        assert retained.to_bytes() == raw.to_bytes()
    pending.release(_KEY)
    assert pending.find(_KEY) is None


def test_temporary_disk_preserves_opaque_canonical_original(tmp_path: Path) -> None:
    pending = _pending(tmp_path, disk=True)
    raw = _raw()
    with pending.reserve(_KEY, expected_bytes=128 * 1024):
        pending.retain(_KEY, _collection(raw))
    assert pending.capacity()["disk_bytes"] == raw.size_bytes
    assert not pending.capacity()["storage_degraded"]
    with pending.lease(_KEY) as collection:
        stored = collection[2].receipt
        assert isinstance(stored, RawCollectReceipt)
        assert not stored.view_available
        assert stored.to_bytes() == raw.to_bytes()
        assert isinstance(stored.attachments[0], memoryview)
        assert stored.validate_command(_command()) == []
    del stored, collection
    pending.release(_KEY)
    assert pending.find(_KEY) is None
    assert pending.capacity()["disk_bytes"] == 0


@pytest.mark.parametrize(
    "dtype,shape", [("unsupported", 1), ("float64", 2), ("float64", -1)]
)
def test_valid_frames_with_invalid_array_contract_remain_owned(
    tmp_path: Path,
    dtype: str,
    shape: int,
) -> None:
    pending = _pending(tmp_path)
    raw = _raw(dtype=dtype, shape=shape, metadata_nodes=1)
    original = raw.to_bytes()
    assert raw.status == "collected"
    assert not raw.view_available
    with pending.reserve(_KEY, expected_bytes=128 * 1024):
        retained = pending.retain(_KEY, _collection(raw))
    assert retained.contract_error
    assert (
        retained.validate_command(_command())[0].code
        == "direct_result_contract_invalid"
    )
    assert retained.to_bytes() == original
    with pending.lease(_KEY) as collection:
        assert collection[2].receipt is retained
    pending.release(_KEY)
    assert pending.find(_KEY) is None


def test_legacy_explicit_full_view_uses_caller_budget_not_direct_view_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat_server.instruments import retained_receipt as module

    monkeypatch.setattr(module, "_FULL_VIEW_BYTES", 1)
    raw = _raw(metadata_nodes=1)
    assert not raw.with_view().view_available
    legacy = raw.with_view(legacy_full_view=True)
    assert legacy.view_available
    assert legacy.to_bytes() == raw.to_bytes()
    receipt = legacy.materialize(max_bytes=legacy.materialization_bytes)
    assert receipt.metadata == {"many": [{}]}
    assert legacy.validate_command(_command()) == []
