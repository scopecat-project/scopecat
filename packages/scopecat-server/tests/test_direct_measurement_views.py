import json
import struct
from collections.abc import Mapping
from typing import NoReturn

import numpy as np
import pytest
from pydantic import JsonValue, ValidationError
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementScalar,
    MeasurementUnavailable,
)
from scopecat.records.metadata import JsonMetadata
from scopecat.sdk.instruments.commands import CollectReceipt

from scopecat_server.instruments.retained_receipt import RawCollectReceipt
from scopecat_server.services.direct_measurement_views import (
    MAX_PREVIEW_BYTES,
    DirectMeasurementPreviewRequest,
    preview_receipt,
)


def _raw(
    values: Mapping[str, MeasurementArray | MeasurementScalar | MeasurementUnavailable],
) -> RawCollectReceipt:
    return RawCollectReceipt.from_receipt(
        CollectReceipt(readback=InstrumentReadback(values=dict(values)))
    )


def test_exact_multichannel_multidimensional_complex_and_int64() -> None:
    raw = _raw(
        {
            "x": MeasurementArray.create(
                values=np.arange(12).reshape(3, 4), dtype="int64"
            ),
            "y": MeasurementArray.create(values=np.arange(12) + 2j, dtype="complex128"),
            "scalar": MeasurementScalar.create(value=2**60 + 1, dtype="int64"),
        }
    )
    before = raw.to_bytes()
    page = preview_receipt(raw, DirectMeasurementPreviewRequest(start=3, limit=4))
    x, y = (next(f for f in page.fields if f.request_id == key) for key in ["x", "y"])
    assert x.shape == [3, 4]
    assert x.values == ["3", "4", "5", "6"]
    assert y.values == [{"real": float(i), "imag": 2.0} for i in range(3, 7)]
    assert x.next_start == y.next_start == 7
    assert not x.complete
    scalar = preview_receipt(
        raw, DirectMeasurementPreviewRequest(request_ids=["scalar"])
    )
    assert scalar.fields[0].values == [str(2**60 + 1)]
    assert scalar.fields[0].complete
    assert raw.to_bytes() == before


def test_zero_and_unknown_shapes() -> None:
    raw = _raw(
        {
            "empty": MeasurementArray.create(values=np.empty((0, 2)), dtype="float64"),
            "missing": MeasurementUnavailable.create(
                dtype="float64",
                shape=(None, 2),
                reason="missing",
                unit=None,
                metadata={},
            ),
        }
    )
    fields = {
        f.request_id: f
        for f in preview_receipt(raw, DirectMeasurementPreviewRequest()).fields
    }
    assert fields["empty"].complete and fields["empty"].total_count == 0
    assert fields["missing"].total_count is None
    assert fields["missing"].shape == [None, 2]
    assert not fields["missing"].complete
    assert fields["missing"].reason == "missing"


def test_string_padded_amplification_byte_cap_and_original_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = 1024
    text = ("界" * 200).encode()
    content = (
        struct.pack("<" + "Q" * (n + 1), *(i * len(text) for i in range(n + 1)))
        + text * n
    )
    raw = RawCollectReceipt.from_parts(
        json.dumps(
            {
                "format_id": "scopecat.collect_receipt.v1",
                "status": "collected",
                "readback": {
                    "values": {
                        "strings": {
                            "kind": "array_attachment",
                            "index": 0,
                            "dtype": "string",
                            "shape": [n],
                        }
                    }
                },
            }
        ).encode(),
        (content,),
    )
    before = raw.to_bytes()

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("preview must not materialize")

    monkeypatch.setattr(RawCollectReceipt, "materialize", forbidden)
    page = preview_receipt(raw, DirectMeasurementPreviewRequest(limit=4096))
    field = page.fields[0]
    assert len(page.model_dump_json().encode()) <= MAX_PREVIEW_BYTES
    assert field.returned_count < n
    assert field.truncated_indices == list(range(field.returned_count))
    assert not field.complete
    for value in field.values:
        assert isinstance(value, str)
        assert len(value.encode()) <= 256
    assert raw.to_bytes() == before


def test_total_point_limit_keeps_coordinates_aligned() -> None:
    raw = _raw(
        {
            key: MeasurementArray.create(
                values=np.arange(3000, dtype=float), dtype="float64"
            )
            for key in ["x", "y", "z"]
        }
    )
    page = preview_receipt(raw, DirectMeasurementPreviewRequest(limit=4096))
    assert sum(f.returned_count for f in page.fields) <= 4096
    assert len({f.next_start for f in page.fields}) == 1
    assert len({f.returned_count for f in page.fields}) == 1


def test_metadata_and_scalar_text_are_bounded() -> None:
    raw = _raw(
        {
            "text": MeasurementScalar.create(
                value="界" * 10000, dtype="string", metadata={"large": "a" * 20000}
            )
        }
    )
    field = preview_receipt(raw, DirectMeasurementPreviewRequest()).fields[0]
    assert field.truncated_indices == [0]
    assert field.metadata_truncated
    assert not field.complete


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 4097},
        {"start": -1},
        {"start": True},
        {"request_ids": []},
    ],
)
def test_invalid_ranges(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DirectMeasurementPreviewRequest.model_validate(kwargs)


def test_unknown_id_does_not_affect_original() -> None:
    raw = _raw({"a": MeasurementScalar.create(value=1.0)})
    before = raw.to_bytes()
    with pytest.raises(ValueError, match="unknown"):
        preview_receipt(raw, DirectMeasurementPreviewRequest(request_ids=["missing"]))
    assert raw.to_bytes() == before


def test_scalar_complex_bool_and_complete_small_window() -> None:
    raw = _raw(
        {
            "complex": MeasurementScalar.create(value=3 + 4j, dtype="complex128"),
            "bool": MeasurementScalar.create(value=True, dtype="bool"),
            "text": MeasurementScalar.create(value="short", dtype="string"),
        }
    )
    fields = {
        f.request_id: f
        for f in preview_receipt(raw, DirectMeasurementPreviewRequest()).fields
    }
    assert fields["complex"].values == [{"real": 3.0, "imag": 4.0}]
    assert fields["bool"].values == [True]
    assert fields["text"].values == ["short"]
    assert all(f.complete for f in fields.values())


def test_field_count_and_metadata_truncation_and_end_page() -> None:
    raw = _raw({str(i): MeasurementScalar.create(value=float(i)) for i in range(20)})
    page = preview_receipt(raw, DirectMeasurementPreviewRequest(start=10))
    assert len(page.fields) == 16 and page.fields_truncated
    assert all(not f.values and f.next_start is None for f in page.fields)


def test_byte_shrink_preserves_coordinate_window() -> None:
    text = "a" * 256
    raw = _raw(
        {
            "x": MeasurementArray.create(
                values=np.arange(2048, dtype=float), dtype="float64"
            ),
            "y": MeasurementArray.create(
                values=np.array([text] * 2048), dtype="string"
            ),
        }
    )
    page = preview_receipt(raw, DirectMeasurementPreviewRequest(limit=4096))
    x, y = page.fields
    assert x.returned_count == y.returned_count < 2048
    assert x.next_start == y.next_start
    assert len(page.model_dump_json().encode()) <= MAX_PREVIEW_BYTES


def test_problem_details_bounded_without_full_model_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat.kernel.problems import Problem, ProblemPhase, problem

    nested: list[JsonValue] = ["z" * 300 for _ in range(100)]
    metadata: JsonMetadata = {"nested": nested}
    raw = RawCollectReceipt.from_receipt(
        CollectReceipt(
            status="unknown",
            problems=tuple(
                problem(
                    code="test",
                    message="x" * 300,
                    phase=ProblemPhase.EXECUTION,
                    details={"nested": ["y" * 300] * 20},
                )
                for _ in range(12)
            ),
            metadata=metadata,
        )
    )

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("must not serialize full problems for bounded view")

    monkeypatch.setattr(Problem, "model_dump", forbidden)
    monkeypatch.setattr(RawCollectReceipt, "metadata", property(forbidden))
    page = preview_receipt(raw, DirectMeasurementPreviewRequest())
    assert len(page.problems) == 8 and page.problems_truncated
    assert page.metadata_truncated
    assert len(page.model_dump_json().encode()) <= MAX_PREVIEW_BYTES


def test_opaque_original_reports_conflict_not_empty_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat_server.errors import BackendConflict
    from scopecat_server.instruments.retained_receipt import (
        ReceiptMaterializationTooLarge,
    )

    raw = _raw({"a": MeasurementScalar.create(value=1.0)})
    before = raw.to_bytes()

    def unavailable(self: RawCollectReceipt) -> NoReturn:
        raise ReceiptMaterializationTooLarge(
            "Preview unavailable; original retained for saving"
        )

    monkeypatch.setattr(RawCollectReceipt, "require_view", unavailable)
    with pytest.raises(BackendConflict, match="original retained"):
        preview_receipt(raw, DirectMeasurementPreviewRequest())
    assert raw.to_bytes() == before
