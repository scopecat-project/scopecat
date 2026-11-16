from __future__ import annotations

import json
import random
from hashlib import sha256
from typing import cast

import numpy as np
import pytest
from scopecat.daemon.hardware_receipt_wire import encode_collect_receipt
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import MeasurementArray, MeasurementScalar
from scopecat.sdk.attachments import AttachmentBundle
from scopecat.sdk.instruments.commands import CollectReceipt

from scopecat_server.instruments.raw_header_spans import (
    RawHeaderError,
    project_canonical_contract_header,
    scan_canonical_header,
    scan_worker_collect_header,
)
from scopecat_server.instruments.worker_wire import (
    collect_attachment_sizes,
    split_collect_receipt,
)


def _header(metadata: bytes = b"{}") -> bytes:
    return (
        b'{"protocol_version":2,"receipt":{"status":"collected",'
        b'"readback":{"values":{},"metadata":{}},"metadata":'
        + metadata
        + b'},"arrays":[],"attachments":[]}'
    )


def test_small_generated_receipts_match_existing_wire() -> None:
    rng = random.Random(71)  # noqa: S311 - deterministic fixture generation
    for iteration in range(30):
        receipt = CollectReceipt(
            readback=InstrumentReadback(
                values={
                    'escaped"\\\u2603': MeasurementArray.create(
                        dtype="int64",
                        unit=None,
                        values=np.asarray(
                            [rng.randrange(-100, 100) for _ in range(iteration % 5)],
                            dtype=np.int64,
                        ),
                        metadata={"nested": [{"n": iteration}, None, "text"]},
                    ),
                    "scalar": MeasurementScalar.create(
                        dtype="string", value='值\\"\n', unit=None
                    ),
                }
            ),
            metadata={"bool": True, "nested": [[], {}, {"x": iteration}]},
        )
        frames = split_collect_receipt(receipt)
        scanned = scan_worker_collect_header(frames.header)
        assert (
            project_canonical_contract_header(scanned.canonical_header)
            == scanned.contract_header
        )
        expected = AttachmentBundle.from_bytes(encode_collect_receipt(receipt)).header
        assert json.loads(scanned.canonical_header) == json.loads(expected)
        assert tuple(
            item.size_bytes for item in scanned.manifests
        ) == collect_attachment_sizes(frames.header)
        assert collect_attachment_sizes(
            scanned.contract_worker_header
        ) == collect_attachment_sizes(frames.header)
        for manifest, payload in zip(
            scanned.manifests, frames.attachments, strict=True
        ):
            assert manifest.sha256 == sha256(payload).hexdigest()
        structure = scan_canonical_header(scanned.canonical_header)
        assert structure.node_count > structure.container_count
        assert structure.scalar_bytes > 0


def test_metadata_spans_preserved_without_graph_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_loads = json.loads

    def strings_only(data: bytes) -> object:
        assert data.startswith(b'"'), "scanner must never parse a graph"
        return cast("object", original_loads(data))

    monkeypatch.setattr(json, "loads", strings_only)
    metadata = (
        b'{"many":[' + b",".join([b"{}"] * 4000) + b'], "metadata": { "keep" : true }}'
    )
    scanned = scan_worker_collect_header(_header(metadata))
    assert metadata in scanned.canonical_header
    assert metadata not in scanned.contract_header
    assert scanned.container_count > 4000
    assert scan_canonical_header(scanned.contract_header).container_count < 20


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"null",
        b"[]",
        b"{}x",
        b'{"x":01}',
        b'{"x":1.}',
        b'{"x":1e+}',
        b'{"x":NaN}',
        b'{"x":truex}',
        b'{"x":[1,]}',
        b'{"x":{},}',
        b'{"x":"\\q"}',
        b'{"x":"\\u12x4"}',
        b'{"x":"\x00"}',
        b'{"x":"\xff"}',
    ],
)
def test_malformed_json_fails(payload: bytes) -> None:
    with pytest.raises(RawHeaderError):
        scan_worker_collect_header(payload)


def test_duplicate_escaped_critical_field_rejected() -> None:
    with pytest.raises(RawHeaderError, match="duplicate"):
        scan_worker_collect_header(
            _header().replace(
                b'"status":"collected"',
                b'"status":"collected","sta\\u0074us":"unknown"',
            )
        )


def test_depth_and_header_limits() -> None:
    with pytest.raises(RawHeaderError, match="nesting"):
        scan_worker_collect_header(_header(b"[" * 201 + b"0" + b"]" * 201))
    with pytest.raises(RawHeaderError, match="size limit"):
        scan_worker_collect_header(b" " * (1024 * 1024 + 1))


def _arrays_header(count: int) -> bytes:
    digest = sha256(b"").hexdigest()
    arrays = [
        {"request_id": f"a{i:04}", "dtype": "int64", "shape": [0]} for i in range(count)
    ]
    manifests = [
        {"index": i, "request_id": f"a{i:04}", "size_bytes": 0, "sha256": digest}
        for i in range(count)
    ]
    return json.dumps(
        {
            "protocol_version": 2,
            "receipt": {"status": "collected", "readback": {"values": {}}},
            "arrays": arrays,
            "attachments": manifests,
        },
        separators=(",", ":"),
    ).encode()


def test_full_4096_descriptor_envelope() -> None:
    header = _arrays_header(4096)
    assert len(header) < 1024 * 1024
    scanned = scan_worker_collect_header(header)
    assert len(scanned.manifests) == 4096
    assert len(collect_attachment_sizes(scanned.contract_worker_header)) == 4096


@pytest.mark.parametrize(
    "mutation", ["duplicate", "collision", "index", "hash", "size"]
)
def test_manifest_identity_failures(mutation: str) -> None:
    document = json.loads(_arrays_header(2))
    if mutation == "duplicate":
        document["arrays"][1]["request_id"] = "a0000"
    elif mutation == "collision":
        document["receipt"]["readback"]["values"]["a0000"] = {
            "kind": "scalar",
            "dtype": "int64",
            "value": 0,
        }
    elif mutation == "index":
        document["attachments"][1]["index"] = 0
    elif mutation == "hash":
        document["attachments"][0]["sha256"] = "bad"
    else:
        document["attachments"][0]["size_bytes"] = 128 * 1024 * 1024 + 1
    with pytest.raises(RawHeaderError):
        scan_worker_collect_header(json.dumps(document).encode())


@pytest.mark.parametrize(
    "metadata",
    [
        b'{"x":1e999}',
        b'{"x":-1e999}',
        b'{"x":"\\ud800"}',
        b'{"x":"\\udfff"}',
        b'{"x":"\\ud800\\u1234"}',
        b'{"x":' + b"1" * 4301 + b"}",
    ],
)
def test_stripped_metadata_retains_existing_scalar_validity(metadata: bytes) -> None:
    with pytest.raises(RawHeaderError):
        scan_worker_collect_header(_header(metadata))


def test_valid_surrogate_pair_and_large_integer_remain_supported() -> None:
    metadata = b'{"x":"\\ud83d\\ude00","n":' + b"1" * 4300 + b"}"
    scanned = scan_worker_collect_header(_header(metadata))
    assert metadata in scanned.canonical_header


@pytest.mark.parametrize("metadata", [b"null", b"[]", b"3", b'"text"'])
def test_stripped_metadata_must_still_be_object(metadata: bytes) -> None:
    with pytest.raises(RawHeaderError, match="must be a JSON object"):
        scan_worker_collect_header(_header(metadata))


def test_canonical_projection_preserves_scientific_values_and_problem_structure() -> (
    None
):
    header = json.dumps(
        {
            "format_id": "scopecat.collect_receipt.v1",
            "status": "collected",
            "metadata": {"private": [{"metadata": "original"}]},
            "readback": {
                "values": {
                    "a": {
                        "kind": "scalar",
                        "dtype": "string",
                        "value": "metadata",
                        "metadata": {"value": [1, 2, 3]},
                    }
                },
                "metadata": {"nested": [{}]},
            },
            "problems": [
                {
                    "code": "example",
                    "phase": "execution",
                    "message": "metadata",
                    "details": {"arbitrary": [{}]},
                    "occurrence_id": "id",
                }
            ],
        }
    ).encode()
    projected = json.loads(project_canonical_contract_header(header))
    assert projected["metadata"] == {}
    assert projected["readback"]["metadata"] == {}
    assert projected["readback"]["values"]["a"]["metadata"] == {}
    assert projected["readback"]["values"]["a"]["value"] == "metadata"
    assert projected["problems"][0]["details"] == {}
    assert projected["problems"][0]["occurrence_id"] == "id"


@pytest.mark.parametrize("path", ["receipt", "readback", "inline", "array", "problem"])
def test_every_stripped_mapping_is_type_checked(path: str) -> None:
    document = json.loads(_arrays_header(1))
    receipt = document["receipt"]
    if path == "receipt":
        receipt["metadata"] = []
    elif path == "readback":
        receipt["readback"]["metadata"] = []
    elif path == "inline":
        receipt["readback"]["values"]["scalar"] = {
            "kind": "scalar",
            "value": 1,
            "metadata": [],
        }
    elif path == "array":
        document["arrays"][0]["metadata"] = []
    else:
        receipt["problems"] = [{"details": []}]
    with pytest.raises(RawHeaderError, match="must be a JSON object"):
        scan_worker_collect_header(json.dumps(document).encode())


def test_deterministic_mutations_fail_cleanly_or_preserve_receipt() -> None:
    rng = random.Random(903)  # noqa: S311 - deterministic malformed corpus
    original = _header(b'{"nested":[{},[],true,false,null,-1.25e+2,"escaped\\\\text"]}')
    for _ in range(300):
        candidate = bytearray(original)
        index = rng.randrange(len(candidate))
        candidate[index] = rng.randrange(256)
        try:
            scanned = scan_worker_collect_header(bytes(candidate))
        except RawHeaderError:
            continue
        decoded = json.loads(candidate)
        canonical = json.loads(scanned.canonical_header)
        assert canonical["metadata"] == decoded["receipt"]["metadata"]
        assert canonical["status"] == decoded["receipt"]["status"]
