from __future__ import annotations

import json
import mmap
import struct
from pathlib import Path

import numpy as np
import pytest
from scopecat.daemon.hardware_receipt_wire import (
    decode_collect_receipt,
)
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import MeasurementArray, MeasurementScalar
from scopecat.sdk.instruments.commands import (
    CollectAxisRequest,
    CollectCommand,
    CollectReceipt,
    CollectResultRequest,
)

from scopecat_server.instruments.retained_receipt import (
    RawCollectReceipt,
    RawReceiptError,
    ReceiptMaterializationTooLarge,
)
from scopecat_server.instruments.worker_wire import split_collect_receipt


def _receipt() -> CollectReceipt:
    return CollectReceipt(
        readback=InstrumentReadback(
            values={
                "a": MeasurementArray.create(
                    dtype="float64", unit="Hz", values=np.arange(4, dtype=np.float64)
                ),
                "b": MeasurementScalar.create(dtype="float64", unit="V", value=2.0),
                "c": MeasurementArray.create(
                    dtype="complex128",
                    unit="ratio",
                    values=np.asarray([1 + 2j, 3 + 4j]),
                ),
            }
        )
    )


def _raw_array(dtype: str, shape: list[int], content: bytes) -> RawCollectReceipt:
    header = json.dumps(
        {
            "format_id": "scopecat.collect_receipt.v1",
            "status": "collected",
            "readback": {
                "values": {
                    "value": {
                        "kind": "array_attachment",
                        "index": 0,
                        "dtype": dtype,
                        "shape": shape,
                        "unit": None,
                        "metadata": {},
                    }
                }
            },
        }
    ).encode()
    return RawCollectReceipt.from_parts(header, (content,))


def test_worker_raw_is_canonical_and_numeric_materialization_is_explicit() -> None:
    receipt = _receipt()
    raw = RawCollectReceipt.from_receipt(receipt)
    assert decode_collect_receipt(raw.to_bytes()) == receipt
    assert raw.materialize(max_bytes=raw.materialization_bytes) == receipt
    assert raw.array_values("a", 1, 2) == (1.0, 2.0)
    assert raw.array_values("c", 0, 2) == (1 + 2j, 3 + 4j)
    assert raw.payload_bytes == 64
    with pytest.raises(ValueError, match="bounded"):
        raw.array_values("a", 0, 4097)


def test_raw_string_retention_never_builds_the_padded_numpy_array() -> None:
    n = 4096
    content = struct.pack("<" + "Q" * (n + 1), 0, *([1024] * n)) + b"a" * 1024
    raw = _raw_array("string", [n], content)
    assert raw.payload_bytes == 33800
    assert raw.arrays[0].string_max_codepoints == 1024
    assert raw.materialization_bytes > 32 * 1024 * 1024
    assert raw.string_value("value", 0, max_bytes=16) == ("a" * 16, True)
    assert raw.string_value("value", 1) == ("", False)
    with pytest.raises(
        ReceiptMaterializationTooLarge, match="original is still retained"
    ):
        raw.materialize(max_bytes=1024 * 1024)
    assert raw.payload_bytes == 33800
    assert raw.string_value("value", 0, max_bytes=16) == ("a" * 16, True)


def test_string_prefix_does_not_split_utf8_and_original_roundtrips() -> None:
    text = "字符"
    data = text.encode()
    content = struct.pack("<QQ", 0, len(data)) + data
    raw = _raw_array("string", [1], content)
    assert raw.string_value("value", 0, max_bytes=4) == ("字", True)
    assert raw.string_value("value", 0, max_bytes=6) == (text, False)
    receipt = raw.materialize(max_bytes=raw.materialization_bytes)
    assert receipt.readback is not None
    array = receipt.readback.values["value"]
    assert isinstance(array, MeasurementArray)
    assert array.values.tolist() == [text]


@pytest.mark.parametrize(
    ("dtype", "shape", "content"),
    [
        ("float64", [2], struct.pack("<d", 1.0)),
        ("float64", [1], struct.pack("<d", float("nan"))),
        ("complex128", [1], struct.pack("<dd", 1.0, float("inf"))),
        ("bool", [1], b"\x02"),
        ("string", [1], struct.pack("<QQ", 1, 1) + b"x"),
        ("string", [1], struct.pack("<QQ", 0, 2) + b"x"),
        ("string", [1], struct.pack("<QQ", 0, 1) + b"\xff"),
    ],
)
def test_raw_rejects_malformed_arrays_without_expansion(
    dtype: str, shape: list[int], content: bytes
) -> None:
    with pytest.raises(RawReceiptError):
        _raw_array(dtype, shape, content)


def test_worker_hash_is_checked_before_adoption() -> None:
    frames = split_collect_receipt(_receipt())
    corrupted = (b"\x00" * len(frames.attachments[0]), *frames.attachments[1:])
    with pytest.raises(RawReceiptError, match="hash"):
        RawCollectReceipt.from_worker_frames(frames.header, corrupted)


def test_command_validation_preserves_shape_dtype_and_missing_checks() -> None:
    raw = _raw_array("float64", [4], struct.pack("<dddd", 1, 2, 3, 4))
    request = CollectResultRequest(
        id="value",
        interface_id="test.signal/v1",
        acquisition_id="sample",
        result_id="value",
        dtype="float64",
        dimensions=[CollectAxisRequest(id="sample", kind="index", size=None)],
    )
    command = CollectCommand(
        command_id="test", instrument_id="test", point_count=1, requests=[request]
    )
    assert raw.validate_command(command) == []
    wrong = request.model_copy(update={"dtype": "int64"})
    assert any(
        p.code.endswith("dtype_mismatch")
        for p in raw.validate_command(command.model_copy(update={"requests": [wrong]}))
    )
    wrong = request.model_copy(update={"dimensions": []})
    assert any(
        p.code.endswith("shape_mismatch")
        for p in raw.validate_command(command.model_copy(update={"requests": [wrong]}))
    )


def test_readonly_mapping_is_borrowed_without_copying(tmp_path: Path) -> None:
    wire = RawCollectReceipt.from_receipt(_receipt()).to_bytes()
    path = tmp_path / "receipt.bin"
    path.write_bytes(wire)
    with path.open("rb") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        raw = RawCollectReceipt.from_http_buffer(memoryview(mapping))
        assert raw.array_values("a", 0, 4) == (0.0, 1.0, 2.0, 3.0)
        assert all(
            isinstance(value, memoryview) and value.obj is mapping
            for value in raw.attachments
        )
        del raw
        mapping.close()


def test_negative_outcome_preserves_original_problem() -> None:
    receipt = CollectReceipt(
        status="not_collected",
        problems=(problem("rejected", "synthetic", phase=ProblemPhase.EXECUTION),),
    )
    raw = RawCollectReceipt.from_receipt(receipt)
    assert raw.status == "not_collected"
    assert raw.problems == receipt.problems
    assert raw.materialize(max_bytes=raw.materialization_bytes) == receipt


def test_duplicate_attachment_reference_is_rejected() -> None:
    raw = _raw_array("int64", [1], struct.pack("<q", 1))
    document = json.loads(raw.header)
    document["readback"]["values"]["other"] = document["readback"]["values"]["value"]
    with pytest.raises(RawReceiptError, match="unique"):
        RawCollectReceipt.from_parts(json.dumps(document).encode(), raw.attachments)


def test_readonly_view_over_mutable_owner_is_copied() -> None:
    raw = _raw_array("int64", [1], struct.pack("<q", 4))
    mutable = bytearray(raw.attachments[0])
    retained = RawCollectReceipt.from_parts(
        raw.header, (memoryview(mutable).toreadonly(),)
    )
    struct.pack_into("<q", mutable, 0, 99)
    assert retained.array_values("value", 0, 1) == (4,)


def test_boolean_preview_preserves_boolean_type() -> None:
    raw = _raw_array("bool", [2], b"\x00\x01")
    values = raw.array_values("value", 0, 2)
    assert values[0] is False
    assert values[1] is True


def test_parts_limits_are_checked_before_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat.sdk.attachments import AttachmentBundleLimits

    from scopecat_server.instruments import retained_receipt as module

    monkeypatch.setattr(
        module,
        "RAW_RECEIPT_LIMITS",
        AttachmentBundleLimits(max_attachment_bytes=4),
    )

    def forbidden(_content: object) -> None:
        raise AssertionError("must not copy before checking limits")

    monkeypatch.setattr(module, "_owned_buffer", forbidden)
    with pytest.raises(RawReceiptError, match="wire size limits"):
        RawCollectReceipt.from_parts(b"{}", (memoryview(bytearray(8)),))


def test_materialize_worker_array_count_without_flattening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = CollectReceipt(
        readback=InstrumentReadback(
            values={
                f"a{index}": MeasurementArray.create(
                    dtype="int64", unit=None, values=np.asarray([index], dtype=np.int64)
                )
                for index in range(1025)
            }
        )
    )
    raw = RawCollectReceipt.from_receipt(receipt)

    def forbidden(_self: RawCollectReceipt) -> bytes:
        raise AssertionError("materialization must not flatten receipt framing")

    monkeypatch.setattr(RawCollectReceipt, "to_bytes", forbidden)
    result = raw.materialize(max_bytes=raw.materialization_bytes)
    assert result == receipt
    assert raw.payload_bytes == 1025 * 8


def test_materialized_numeric_values_outlive_mapped_original(tmp_path: Path) -> None:
    raw = _raw_array("int64", [2], struct.pack("<qq", 4, 9))
    path = tmp_path / "receipt"
    path.write_bytes(raw.to_bytes())
    with (
        path.open("rb") as file,
        mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
    ):
        borrowed = RawCollectReceipt.from_http_buffer(memoryview(mapped))
        result = borrowed.materialize(max_bytes=borrowed.materialization_bytes)
        del borrowed
    assert result.readback is not None
    array = result.readback.values["value"]
    assert isinstance(array, MeasurementArray)
    assert array.values.tolist() == [4, 9]
