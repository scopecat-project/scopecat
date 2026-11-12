"""Bounded exact windows over retained originals, never a scientific overview.

Callers must hold the pending lease or saved raw_receipt context until projection
finishes. Returned values own no borrowed buffers. Projection cannot mutate or
release originals and does not use the eager receipt materializer.
"""

from collections.abc import Mapping
from itertools import islice
from math import isfinite, prod
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from scopecat.records.measurement import MeasurementUnavailable

from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.retained_receipt import (
    RawArray,
    RawCollectReceipt,
    ReceiptMaterializationTooLarge,
)
from scopecat_server.services.direct_measurements import DirectMeasurementIdentity

MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_CELLS = 4096
_MAX_TEXT = 256


class DirectMeasurementPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: int = Field(default=0, ge=0, le=9007199254740991)
    limit: int = Field(default=512, ge=1, le=MAX_PREVIEW_CELLS)
    request_ids: list[Annotated[str, Field(min_length=1, max_length=1024)]] | None = (
        Field(default=None, min_length=1, max_length=16)
    )


class PendingMeasurementPreviewRequest(
    DirectMeasurementPreviewRequest, DirectMeasurementIdentity
):
    pass


class DirectMeasurementPreviewField(BaseModel):
    request_id: str
    kind: Literal["array", "scalar", "unavailable"]
    dtype: str
    unit: str | None
    unit_truncated: bool = False
    shape: list[int | None]
    total_count: int | None
    start: int
    returned_count: int
    next_start: int | None
    complete: bool
    values: list[JsonValue]
    truncated_indices: list[int]
    metadata: JsonValue
    metadata_truncated: bool
    reason: str | None = None


class DirectMeasurementPreview(BaseModel):
    method: Literal["exact_slice"] = "exact_slice"
    status: str
    raw_bytes: int
    fields: list[DirectMeasurementPreviewField]
    fields_truncated: bool
    metadata: JsonValue
    metadata_truncated: bool
    problems: list[JsonValue]
    problems_truncated: bool


def _text(value: str, limit: int = _MAX_TEXT) -> tuple[str, bool]:
    # Slice codepoints before encoding: never allocate a huge UTF-8 copy.
    prefix = value[:limit].encode("utf-8")
    clipped = prefix[:limit].decode("utf-8", errors="ignore")
    return clipped, len(clipped) != len(value)


class _Metadata:
    """Small total node budget, in addition to bounded individual strings."""

    def __init__(self) -> None:
        self.remaining = 64
        self.truncated = False

    def copy(self, value: object, depth: int = 0) -> JsonValue:
        self.remaining -= 1
        if self.remaining < 0 or depth > 4:
            self.truncated = True
            return None
        if isinstance(value, str):
            text, truncated = _text(value)
            self.truncated |= truncated
            return text
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, BaseModel):
            return self.copy(
                {key: getattr(value, key) for key in type(value).model_fields}, depth
            )
        if isinstance(value, Mapping):
            result: dict[str, JsonValue] = {}
            for key, child in cast("Mapping[str, object]", value).items():
                if self.remaining <= 0:
                    self.truncated = True
                    break
                name, clipped = _text(str(key))
                if clipped or name in result:
                    self.remaining -= 1
                    self.truncated = True
                    continue
                result[name] = self.copy(child, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            result_list: list[JsonValue] = []
            for child in cast("list[object] | tuple[object, ...]", value):
                if self.remaining <= 0:
                    self.truncated = True
                    break
                result_list.append(self.copy(child, depth + 1))
            return result_list
        self.truncated = True
        return None


def _cell(value: object, dtype: str) -> tuple[JsonValue, bool]:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, float) and not isfinite(value):
        return None, False
    if isinstance(value, complex):
        if not isfinite(value.real) or not isfinite(value.imag):
            return None, False
        return {"real": value.real, "imag": value.imag}, False
    if dtype == "int64" and isinstance(value, int):
        return str(value), False
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    raise ValueError("unsupported preview scalar; original remains retained")


def preview_receipt(
    raw: RawCollectReceipt, request: DirectMeasurementPreviewRequest
) -> DirectMeasurementPreview:
    """Return an exact C-order page with a hard serialized byte and cell cap.

    Byte pressure shrinks all fields' windows together, including coordinates.
    Oversize identifiers/shapes fail the view only, never the retained original.
    """
    try:
        raw.require_view()
    except ReceiptMaterializationTooLarge as error:
        raise BackendConflict(str(error)) from error
    available = {**raw.inline_values, **{a.request_id: a for a in raw.arrays}}
    ids = (
        list(islice(available, 16))
        if request.request_ids is None
        else list(dict.fromkeys(request.request_ids))
    )
    if any(key not in available for key in ids):
        raise ValueError("unknown preview request_id")
    if any(len(key) > 1024 for key in ids):
        raise ValueError("preview identity exceeds display limit; original retained")
    limit = min(request.limit, MAX_PREVIEW_CELLS // max(len(ids), 1))
    metadata = _Metadata()
    bounded_metadata = metadata.copy(raw.metadata_view)
    problems: list[JsonValue] = []
    problem_budget = _Metadata()
    for problem in raw.problems[:8]:
        problems.append(problem_budget.copy(problem))
    while True:
        fields: list[DirectMeasurementPreviewField] = []
        for key in ids:
            source = available[key]
            shape = (
                list(source.shape)
                if isinstance(source, RawArray) or source.kind == "unavailable"
                else []
            )
            if len(shape) > 32:
                raise ValueError(
                    "preview shape exceeds display limit; original retained"
                )
            kind = "array" if isinstance(source, RawArray) else source.kind
            total = (
                None
                if any(n is None for n in shape)
                else prod(n for n in shape if n is not None)
            )
            values: list[JsonValue] = []
            truncated_indices: list[int] = []
            if isinstance(source, RawArray):
                count = min(limit, max(0, source.count - request.start))
                if source.dtype == "string":
                    cells = [
                        raw.string_value(key, i, max_bytes=_MAX_TEXT)
                        for i in range(request.start, request.start + count)
                    ]
                else:
                    cells = [
                        _cell(v, source.dtype)
                        for v in raw.array_values(
                            key, min(request.start, source.count), count
                        )
                    ]
            elif source.kind == "scalar" and request.start == 0:
                cells = [_cell(source.value, source.dtype)]
            else:
                cells = []
            for offset, (value, truncated) in enumerate(cells):
                values.append(value)
                if truncated:
                    truncated_indices.append(request.start + offset)
            field_metadata = _Metadata()
            field_metadata_value = field_metadata.copy(source.metadata)
            unit, unit_truncated = (
                (None, False) if source.unit is None else _text(source.unit)
            )
            end = request.start + len(values)
            fields.append(
                DirectMeasurementPreviewField(
                    request_id=key,
                    kind=kind,
                    dtype=source.dtype,
                    unit=unit,
                    unit_truncated=unit_truncated,
                    shape=shape,
                    total_count=total,
                    start=request.start,
                    returned_count=len(values),
                    next_start=end
                    if kind != "unavailable" and total is not None and end < total
                    else None,
                    complete=kind != "unavailable"
                    and request.start == 0
                    and len(values) == total
                    and not truncated_indices,
                    values=values,
                    truncated_indices=truncated_indices,
                    metadata=field_metadata_value,
                    metadata_truncated=field_metadata.truncated,
                    reason=source.reason
                    if isinstance(source, MeasurementUnavailable)
                    else None,
                )
            )
        response = DirectMeasurementPreview(
            status=raw.status,
            raw_bytes=raw.size_bytes,
            fields=fields,
            fields_truncated=len(ids) < len(available),
            metadata=bounded_metadata,
            metadata_truncated=metadata.truncated,
            problems=problems,
            problems_truncated=len(raw.problems) > 8 or problem_budget.truncated,
        )
        if len(response.model_dump_json().encode()) <= MAX_PREVIEW_BYTES:
            return response
        if limit == 1:
            raise ValueError(
                "preview metadata exceeds display budget; original retained"
            )
        limit = max(1, limit // 2)
