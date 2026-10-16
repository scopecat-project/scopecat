from __future__ import annotations

import json
import struct
from dataclasses import replace
from hashlib import sha256
from typing import cast

import pytest
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.records.instrument import CommandChannelBinding, InstrumentReadback
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementPartitionedArray,
    MeasurementScalar,
    MeasurementUnavailable,
)
from scopecat.sdk.attachments import AttachmentBundle
from scopecat.sdk.instruments.backend import (
    BackendInvokeRequest,
    BackendOperationArgument,
    BackendPayload,
)
from scopecat.sdk.instruments.commands import CollectReceipt
from scopecat.sdk.payloads import EncodedPayloadContent

from scopecat_server.instruments.worker_wire import (
    DEFAULT_WIRE_LIMITS,
    WorkerWireError,
    collect_attachment_sizes,
    invoke_attachment_sizes,
    join_collect_receipt,
    join_invoke_request,
    split_collect_receipt,
    split_invoke_request,
)


def _payload(payload_id: str, content: bytes) -> BackendPayload:
    return BackendPayload(
        id=payload_id,
        schema_id=f"tests.{payload_id}/v1",
        codec_id="tests.binary",
        codec_version=2,
        media_type="application/octet-stream",
        content_format="bytes",
        content=EncodedPayloadContent.from_bytes(content),
    )


def _request(*payloads: BackendPayload) -> BackendInvokeRequest:
    return BackendInvokeRequest(
        interface_id="tests.program_player/v1",
        component_path=("channel-a",),
        operation_id="play",
        arguments=tuple(
            BackendOperationArgument(
                id=f"argument-{payload.id}",
                value=StateValue(PayloadRef(payload_id=payload.id)),
            )
            for payload in payloads
        ),
        payloads={payload.id: payload for payload in reversed(payloads)},
        entity_ids=("q0",),
        channel_bindings=(
            CommandChannelBinding(
                entity_id="q0",
                channel_id="drive.ch1",
                interface_id="tests.program_player/v1",
            ),
        ),
    )


def _header_document(header: bytes) -> dict[str, object]:
    document: object = json.loads(header)
    assert isinstance(document, dict)
    return cast("dict[str, object]", document)


def _encode_header(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def _collected(
    values: dict[str, MeasurementAcquisitionValue],
) -> CollectReceipt:
    return CollectReceipt(
        readback=InstrumentReadback(
            values=values,
            metadata={"source": "driver"},
        ),
        metadata={"elapsed_seconds": 0.25},
    )


def test_invoke_wire_separates_json_descriptor_from_binary_content() -> None:
    request = _request(_payload("program", b"\x00\xffbinary\x00"))

    frames = split_invoke_request(request)
    document = json.loads(frames.header)

    assert frames.attachments == (b"\x00\xffbinary\x00",)
    assert set(document) == {
        "attachments",
        "payloads",
        "protocol_version",
        "request",
    }
    assert set(document["request"]) == {
        "arguments",
        "component_path",
        "interface_id",
        "operation_id",
        "entity_ids",
        "channel_bindings",
    }
    assert set(document["payloads"][0]) == {
        "codec_id",
        "codec_version",
        "content_format",
        "id",
        "media_type",
        "part_count",
        "schema_id",
    }
    assert set(document["attachments"][0]) == {
        "index",
        "part_index",
        "payload_id",
        "sha256",
        "size_bytes",
    }
    header_text = frames.header.decode()
    assert "content_base64" not in header_text
    assert '"body"' not in header_text
    assert "command_id" not in header_text
    assert "instrument_id" not in header_text
    assert "resource_id" not in header_text
    assert invoke_attachment_sizes(frames.header) == (len(frames.attachments[0]),)
    assert join_invoke_request(frames.header, frames.attachments) == request


def test_invoke_wire_orders_multiple_payload_attachments_by_id() -> None:
    first = _payload("alpha", b"\x00first")
    second = _payload("zeta", b"\xffsecond")
    request = _request(first, second)

    frames = split_invoke_request(request)
    document = json.loads(frames.header)
    restored = join_invoke_request(frames.header, frames.attachments)

    assert [payload["id"] for payload in document["payloads"]] == [
        "alpha",
        "zeta",
    ]
    assert [item["payload_id"] for item in document["attachments"]] == [
        "alpha",
        "zeta",
    ]
    assert frames.attachments == (
        first.content.require_bytes(),
        second.content.require_bytes(),
    )
    assert restored == request


def test_invoke_wire_preserves_payload_bundle_parts() -> None:
    content = EncodedPayloadContent.from_bundle(
        AttachmentBundle(
            header=b'{"program":"readout"}',
            attachments=(memoryview(b"waveform-a"), memoryview(b"waveform-b")),
        )
    )
    payload = BackendPayload(
        id="program",
        schema_id="tests.program/v1",
        codec_id="tests.bundle",
        codec_version=1,
        media_type="application/vnd.tests.bundle",
        content_format="attachment_bundle",
        content=content,
    )

    frames = split_invoke_request(_request(payload))
    restored = join_invoke_request(frames.header, frames.attachments)

    assert tuple(map(bytes, frames.attachments)) == (
        b'{"program":"readout"}',
        b"waveform-a",
        b"waveform-b",
    )
    restored_bundle = restored.payloads["program"].content.require_bundle()
    assert restored_bundle.header == content.require_bundle().header
    assert tuple(map(bytes, restored_bundle.attachments)) == (
        b"waveform-a",
        b"waveform-b",
    )


def test_invoke_wire_supports_array_fragment_heavy_payloads() -> None:
    bundle = AttachmentBundle(
        header=b'{"program":"fragmented"}',
        attachments=tuple(memoryview(bytes([index % 251])) for index in range(300)),
    )
    payload = BackendPayload(
        id="program",
        schema_id="tests.program/v1",
        codec_id="tests.bundle",
        codec_version=1,
        media_type="application/vnd.tests.bundle",
        content_format="attachment_bundle",
        content=EncodedPayloadContent.from_bundle(bundle),
    )

    frames = split_invoke_request(_request(payload))
    restored = join_invoke_request(frames.header, frames.attachments)

    assert len(frames.attachments) == 301
    assert len(restored.payloads["program"].content.require_bundle().attachments) == 300


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_invoke_wire_rejects_missing_or_extra_attachments(change: str) -> None:
    frames = split_invoke_request(_request(_payload("program", b"content")))
    attachments = (
        frames.attachments[:-1]
        if change == "missing"
        else (*frames.attachments, b"unexpected")
    )

    with pytest.raises(WorkerWireError, match="count"):
        join_invoke_request(frames.header, attachments)


def test_invoke_wire_rejects_attachment_order_changes() -> None:
    request = _request(
        _payload("alpha", b"aaaa"),
        _payload("zeta", b"zzzz"),
    )
    frames = split_invoke_request(request)

    with pytest.raises(WorkerWireError, match="hash mismatch"):
        join_invoke_request(frames.header, tuple(reversed(frames.attachments)))


def test_invoke_wire_rejects_attachment_length_and_hash_tampering() -> None:
    frames = split_invoke_request(_request(_payload("program", b"payload")))

    with pytest.raises(WorkerWireError, match="length mismatch"):
        join_invoke_request(frames.header, (b"payloa",))
    with pytest.raises(WorkerWireError, match="hash mismatch"):
        join_invoke_request(frames.header, (b"payloae",))


def test_invoke_wire_rejects_unknown_version_and_public_provenance() -> None:
    frames = split_invoke_request(_request(_payload("program", b"payload")))
    wrong_version_document = _header_document(frames.header)
    wrong_version_document["protocol_version"] = 3
    wrong_version = _encode_header(wrong_version_document)
    provenance_document = _header_document(frames.header)
    request_document = provenance_document["request"]
    assert isinstance(request_document, dict)
    request_document["command_id"] = "invoke-1"
    with_provenance = _encode_header(provenance_document)

    with pytest.raises(WorkerWireError, match="invalid"):
        join_invoke_request(wrong_version, frames.attachments)
    with pytest.raises(WorkerWireError, match="invalid"):
        join_invoke_request(with_provenance, frames.attachments)


def test_invoke_wire_enforces_attachment_count_and_size_limits() -> None:
    two_frames = split_invoke_request(
        _request(
            _payload("alpha", b"abc"),
            _payload("zeta", b"def"),
        )
    )
    one_frame = split_invoke_request(_request(_payload("program", b"abcd")))

    with pytest.raises(WorkerWireError, match="count exceeds"):
        join_invoke_request(
            two_frames.header,
            two_frames.attachments,
            limits=replace(DEFAULT_WIRE_LIMITS, max_attachments=1),
        )
    with pytest.raises(WorkerWireError, match="attachment exceeds"):
        join_invoke_request(
            one_frame.header,
            one_frame.attachments,
            limits=replace(DEFAULT_WIRE_LIMITS, max_attachment_bytes=3),
        )
    with pytest.raises(WorkerWireError, match="total size"):
        join_invoke_request(
            two_frames.header,
            two_frames.attachments,
            limits=replace(DEFAULT_WIRE_LIMITS, max_total_attachment_bytes=5),
        )


def test_collect_wire_uses_canonical_binary_for_every_array_dtype() -> None:
    receipt = _collected(
        {
            "string": MeasurementArray.create(
                dtype="string",
                values=("猫", "ready"),
                metadata={"encoding": "labels"},
            ),
            "float": MeasurementArray.create(
                dtype="float64",
                unit="V",
                values=(1.25, -2.5),
            ),
            "bool": MeasurementArray.create(
                dtype="bool",
                values=(True, False, True),
            ),
            "int": MeasurementArray.create(
                dtype="int64",
                unit="count",
                values=(-1, 2),
            ),
            "complex": MeasurementArray.create(
                dtype="complex128",
                unit="ratio",
                values=(
                    complex(1.0, -0.5),
                    complex(-2.0, 3.25),
                ),
            ),
        }
    )

    frames = split_collect_receipt(receipt)
    document = _header_document(frames.header)
    arrays = cast("list[dict[str, object]]", document["arrays"])
    manifests = cast("list[dict[str, object]]", document["attachments"])
    request_ids = ["bool", "complex", "float", "int", "string"]

    assert [item["request_id"] for item in arrays] == request_ids
    assert [item["request_id"] for item in manifests] == request_ids
    assert all("values" not in item for item in arrays)
    receipt_document = cast("dict[str, object]", document["receipt"])
    readback_document = cast("dict[str, object]", receipt_document["readback"])
    assert readback_document["values"] == {}
    assert "ready" not in frames.header.decode()
    assert frames.attachments == (
        b"\x01\x00\x01",
        struct.pack("<dddd", 1.0, -0.5, -2.0, 3.25),
        struct.pack("<dd", 1.25, -2.5),
        struct.pack("<qq", -1, 2),
        struct.pack("<QQQ", 0, 3, 8) + "猫ready".encode(),
    )
    assert collect_attachment_sizes(frames.header) == tuple(
        len(item) for item in frames.attachments
    )
    assert join_collect_receipt(frames.header, frames.attachments) == receipt


def test_collect_wire_keeps_scalars_in_header_and_arrays_in_attachments() -> None:
    receipt = _collected(
        {
            "trace": MeasurementArray.create(
                dtype="float64",
                unit="V",
                values=((0.5, 1.5),),
                metadata={"channel": 2},
            ),
            "temperature": MeasurementScalar.create(
                dtype="float64",
                unit="K",
                value=0.02,
                metadata={"sensor": "mixing-chamber"},
            ),
        }
    )

    frames = split_collect_receipt(receipt)
    document = _header_document(frames.header)
    receipt_document = cast("dict[str, object]", document["receipt"])
    readback_document = cast("dict[str, object]", receipt_document["readback"])

    assert set(cast("dict[str, object]", readback_document["values"])) == {
        "temperature"
    }
    assert cast("list[dict[str, object]]", document["arrays"]) == [
        {
            "dtype": "float64",
            "metadata": {"channel": 2},
            "request_id": "trace",
            "shape": [1, 2],
            "unit": "V",
        }
    ]
    assert join_collect_receipt(frames.header, frames.attachments) == receipt


def test_collect_wire_keeps_unavailable_values_in_json_header() -> None:
    receipt = _collected(
        {
            "scalar": MeasurementUnavailable.create(
                dtype="float64",
                unit="V",
                shape=(),
                reason="overload",
                metadata={"source": "instrument"},
            ),
            "trace": MeasurementUnavailable.create(
                dtype="complex128",
                unit="ratio",
                shape=(2, 3),
                reason="missing",
                metadata={},
            ),
            "ragged": MeasurementUnavailable.create(
                dtype="float64",
                unit="V",
                shape=(None,),
                reason="missing",
                metadata={},
            ),
            "mixed": MeasurementUnavailable.create(
                dtype="float64",
                unit="V",
                shape=(2, None),
                reason="invalid",
                metadata={},
            ),
        }
    )

    frames = split_collect_receipt(receipt)
    document = _header_document(frames.header)
    receipt_document = cast("dict[str, object]", document["receipt"])
    readback_document = cast("dict[str, object]", receipt_document["readback"])
    inline_values = cast("dict[str, dict[str, object]]", readback_document["values"])

    assert document["protocol_version"] == 2
    assert frames.attachments == ()
    assert collect_attachment_sizes(frames.header) == ()
    assert inline_values["scalar"] == {
        "dtype": "float64",
        "kind": "unavailable",
        "metadata": {"source": "instrument"},
        "reason": "overload",
        "shape": [],
        "unit": "V",
    }
    assert inline_values["trace"]["shape"] == [2, 3]
    assert inline_values["trace"]["reason"] == "missing"
    assert inline_values["ragged"]["shape"] == [None]
    assert inline_values["mixed"]["shape"] == [2, None]
    assert join_collect_receipt(frames.header, ()) == receipt


def test_collect_wire_round_trips_mixed_inline_and_array_values() -> None:
    receipt = _collected(
        {
            "available": MeasurementScalar.create(
                dtype="float64",
                unit="V",
                value=1.25,
            ),
            "missing": MeasurementUnavailable.create(
                dtype="float64",
                unit="V",
                shape=(2,),
                reason="invalid",
                metadata={},
            ),
            "trace": MeasurementArray.create(
                dtype="float64",
                unit="V",
                values=(0.5, 1.5),
            ),
        }
    )

    frames = split_collect_receipt(receipt)
    document = _header_document(frames.header)
    receipt_document = cast("dict[str, object]", document["receipt"])
    readback_document = cast("dict[str, object]", receipt_document["readback"])

    assert set(cast("dict[str, object]", readback_document["values"])) == {
        "available",
        "missing",
    }
    assert [
        item["request_id"]
        for item in cast("list[dict[str, object]]", document["arrays"])
    ] == ["trace"]
    assert len(frames.attachments) == 1
    assert join_collect_receipt(frames.header, frames.attachments) == receipt


@pytest.mark.parametrize(
    "shape",
    [
        [-1],
        [1] * 17,
    ],
)
def test_collect_wire_rejects_invalid_unavailable_shapes(shape: list[int]) -> None:
    receipt = _collected(
        {
            "missing": MeasurementUnavailable.create(
                dtype="float64",
                unit="V",
                shape=(1,),
                reason="missing",
                metadata={},
            )
        }
    )
    frames = split_collect_receipt(receipt)
    document = _header_document(frames.header)
    receipt_document = cast("dict[str, object]", document["receipt"])
    readback_document = cast("dict[str, object]", receipt_document["readback"])
    values = cast("dict[str, dict[str, object]]", readback_document["values"])
    values["missing"]["shape"] = shape

    with pytest.raises(WorkerWireError, match="invalid"):
        collect_attachment_sizes(_encode_header(document))


def test_collect_wire_rejects_duplicate_inline_and_array_request_ids() -> None:
    frames = split_collect_receipt(
        _collected(
            {
                "inline": MeasurementUnavailable.create(
                    dtype="float64",
                    unit=None,
                    shape=(),
                    reason="missing",
                    metadata={},
                ),
                "trace": MeasurementArray.create(values=(1.0,)),
            }
        )
    )
    document = _header_document(frames.header)
    [array] = cast("list[dict[str, object]]", document["arrays"])
    [attachment] = cast("list[dict[str, object]]", document["attachments"])
    array["request_id"] = "inline"
    attachment["request_id"] = "inline"

    with pytest.raises(WorkerWireError, match="invalid"):
        collect_attachment_sizes(_encode_header(document))


def test_collect_wire_rejects_version_one_headers() -> None:
    frames = split_collect_receipt(
        _collected({"signal": MeasurementScalar.create(value=1.0)})
    )
    document = _header_document(frames.header)
    document["protocol_version"] = 1

    with pytest.raises(WorkerWireError, match="invalid"):
        collect_attachment_sizes(_encode_header(document))


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_collect_wire_rejects_missing_or_extra_attachments(change: str) -> None:
    frames = split_collect_receipt(
        _collected(
            {
                "signal": MeasurementArray.create(
                    values=(1.0,),
                )
            }
        )
    )
    attachments = (
        frames.attachments[:-1]
        if change == "missing"
        else (*frames.attachments, b"unexpected")
    )

    with pytest.raises(WorkerWireError, match="count"):
        join_collect_receipt(frames.header, attachments)


def test_collect_wire_rejects_manifest_order_size_and_hash_tampering() -> None:
    frames = split_collect_receipt(
        _collected(
            {
                "alpha": MeasurementArray.create(values=(1.0,)),
                "zeta": MeasurementArray.create(values=(2.0,)),
            }
        )
    )
    order_document = _header_document(frames.header)
    arrays = cast("list[object]", order_document["arrays"])
    order_document["arrays"] = list(reversed(arrays))

    with pytest.raises(WorkerWireError, match="invalid"):
        join_collect_receipt(
            _encode_header(order_document),
            frames.attachments,
        )
    with pytest.raises(WorkerWireError, match="length mismatch"):
        join_collect_receipt(
            frames.header,
            (frames.attachments[0][:-1], frames.attachments[1]),
        )
    with pytest.raises(WorkerWireError, match="hash mismatch"):
        join_collect_receipt(
            frames.header,
            (struct.pack("<d", 3.0), frames.attachments[1]),
        )


@pytest.mark.parametrize(
    "shape",
    [
        [1_000_000, 0],
        [1] * 17,
    ],
)
def test_collect_wire_rejects_shapes_that_expand_beyond_frame_limits(
    shape: list[int],
) -> None:
    receipt = _collected(
        {
            "empty": MeasurementArray.create(
                values=((),),
            )
        }
    )
    frames = split_collect_receipt(receipt)
    assert join_collect_receipt(frames.header, frames.attachments) == receipt
    document = _header_document(frames.header)
    [array] = cast("list[dict[str, object]]", document["arrays"])
    array["shape"] = shape

    with pytest.raises(WorkerWireError, match="invalid"):
        collect_attachment_sizes(_encode_header(document))


def test_collect_wire_enforces_attachment_limits_when_splitting_and_joining() -> None:
    frames = split_collect_receipt(
        _collected(
            {
                "alpha": MeasurementArray.create(values=(1.0,)),
                "zeta": MeasurementArray.create(values=(2.0,)),
            }
        )
    )

    with pytest.raises(WorkerWireError, match="count exceeds"):
        split_collect_receipt(
            join_collect_receipt(frames.header, frames.attachments),
            limits=replace(DEFAULT_WIRE_LIMITS, max_attachments=1),
        )
    with pytest.raises(WorkerWireError, match="attachment exceeds"):
        join_collect_receipt(
            frames.header,
            frames.attachments,
            limits=replace(DEFAULT_WIRE_LIMITS, max_attachment_bytes=7),
        )
    with pytest.raises(WorkerWireError, match="total size"):
        join_collect_receipt(
            frames.header,
            frames.attachments,
            limits=replace(DEFAULT_WIRE_LIMITS, max_total_attachment_bytes=15),
        )


def test_collect_wire_rejects_invalid_string_offsets_and_utf8() -> None:
    frames = split_collect_receipt(
        _collected(
            {
                "labels": MeasurementArray.create(
                    dtype="string",
                    values=("valid",),
                )
            }
        )
    )
    invalid_offsets = struct.pack("<QQ", 1, 1) + b"x"
    invalid_utf8 = struct.pack("<QQ", 0, 1) + b"\xff"
    offset_document = _header_document(frames.header)
    offset_manifest = cast(
        "list[dict[str, object]]",
        offset_document["attachments"],
    )[0]
    offset_manifest["size_bytes"] = len(invalid_offsets)
    offset_manifest["sha256"] = sha256(invalid_offsets).hexdigest()
    utf8_document = _header_document(frames.header)
    utf8_manifest = cast(
        "list[dict[str, object]]",
        utf8_document["attachments"],
    )[0]
    utf8_manifest["size_bytes"] = len(invalid_utf8)
    utf8_manifest["sha256"] = sha256(invalid_utf8).hexdigest()

    with pytest.raises(WorkerWireError, match="offsets"):
        join_collect_receipt(
            _encode_header(offset_document),
            (invalid_offsets,),
        )
    with pytest.raises(WorkerWireError, match="UTF-8"):
        join_collect_receipt(
            _encode_header(utf8_document),
            (invalid_utf8,),
        )


@pytest.mark.parametrize("partitioned", [False, True])
@pytest.mark.parametrize("valid", [[True, False], [False, False]])
def test_worker_collect_wire_rejects_availability(
    partitioned: bool, valid: list[bool]
) -> None:
    array = MeasurementArray.create(
        values=[1.0, 0.0],
        availability=MeasurementArrayAvailability.create(valid=valid),
    )
    value = (
        MeasurementPartitionedArray.create(partitions=(array,), axis=0)
        if partitioned
        else array
    )
    receipt = CollectReceipt(readback=InstrumentReadback(values={"trace": value}))
    with pytest.raises(
        WorkerWireError, match="does not support measurement array availability"
    ):
        frames = split_collect_receipt(receipt)
        join_collect_receipt(frames.header, frames.attachments)
