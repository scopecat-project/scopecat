"""Validated framed originals, retained without eager array materialization.

A readonly mapped buffer is borrowed: the caller must pin its owner for the whole
lifetime of this object and any returned view. This module never closes owners.
"""

from __future__ import annotations

import codecs
import math
import struct
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Annotated, Literal, Self, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scopecat.kernel.frozen import freeze_json_mapping
from scopecat.kernel.problems import Problem, ProblemPhase, problem
from scopecat.measurements.array_wire import decode_measurement_array
from scopecat.program.measurement_types import MeasurementDType
from scopecat.records.costs import OperationCostMeasurement
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import MeasurementScalar, MeasurementUnavailable
from scopecat.records.metadata import JsonMetadata
from scopecat.sdk.attachments import (
    AttachmentBundle,
    AttachmentBundleLimits,
    ImmutableBuffer,
)
from scopecat.sdk.instruments.commands import CollectCommand, CollectReceipt
from scopecat.sdk.instruments.contracts import validate_collect_receipt

from .raw_header_spans import (
    HeaderStructure,
    project_canonical_contract_header,
    scan_canonical_header,
    scan_worker_collect_header,
)
from .worker_wire import collect_attachment_sizes, split_collect_receipt

# Preserve all worker-valid originals, including its 4096-array envelope.
RAW_RECEIPT_LIMITS = AttachmentBundleLimits(
    max_header_bytes=8 * 1024 * 1024, max_attachments=4096
)
_MAX_SLICE_ITEMS = 4096
_CHUNK_BYTES = 64 * 1024


class RawReceiptError(ValueError):
    """An original frame violates its executable wire contract."""


class ReceiptMaterializationTooLarge(ValueError):
    """The original remains retained; this requested expanded read is too large."""


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )


class _Array(_WireModel):
    kind: Literal["array_attachment"] = "array_attachment"
    index: int = Field(ge=0)
    dtype: MeasurementDType
    unit: str | None = None
    shape: tuple[Annotated[int, Field(ge=0)], ...] = Field(min_length=1)
    metadata: JsonMetadata = Field(default_factory=dict)


type _Value = Annotated[
    MeasurementScalar | MeasurementUnavailable | _Array,
    Field(discriminator="kind"),
]


class _Readback(_WireModel):
    values: dict[str, _Value] = Field(default_factory=dict)
    metadata: JsonMetadata = Field(default_factory=dict)


class _Header(_WireModel):
    format_id: Literal["scopecat.collect_receipt.v1"] = "scopecat.collect_receipt.v1"
    status: Literal["collected", "not_collected", "unknown"]
    problems: tuple[Problem, ...] = ()
    readback: _Readback | None = None
    metadata: JsonMetadata = Field(default_factory=dict)
    measured_cost: OperationCostMeasurement | None = None


@dataclass(frozen=True, slots=True)
class RawArray:
    request_id: str
    dtype: MeasurementDType
    unit: str | None
    shape: tuple[int, ...]
    metadata: Mapping[str, object]
    index: int
    size_bytes: int
    string_max_codepoints: int = 0

    @property
    def count(self) -> int:
        return 0 if 0 in self.shape else math.prod(self.shape)


@dataclass(frozen=True, slots=True)
class RawCollectReceipt:
    _bundle: AttachmentBundle
    _wire: _Header | None
    arrays: tuple[RawArray, ...]
    metadata_view: Mapping[str, object]
    _status: Literal["collected", "not_collected", "unknown"] = "collected"
    view_available: bool = True
    descriptor_bytes: int = 0
    _worker_contract: bytes | None = None
    contract_error: str | None = None

    @classmethod
    def from_receipt(cls, receipt: CollectReceipt, *, defer_view: bool = False) -> Self:
        # In-process compatibility: the driver already owns its domain object.
        # Crucially this does not flatten the whole attachment bundle.
        frames = split_collect_receipt(receipt)
        return cls.from_worker_frames(
            frames.header, frames.attachments, defer_view=defer_view
        )

    @classmethod
    def from_worker_frames(
        cls,
        header: bytes,
        attachments: tuple[ImmutableBuffer, ...],
        *,
        defer_view: bool = False,
    ) -> Self:
        scanned = scan_worker_collect_header(header)
        sizes = tuple(item.size_bytes for item in scanned.manifests)
        if sizes != tuple(len(value) for value in attachments):
            raise RawReceiptError("worker frame sizes do not match their manifest")
        for manifest, content in zip(scanned.manifests, attachments, strict=True):
            if sha256(content).hexdigest() != manifest.sha256:
                raise RawReceiptError("worker collect attachment hash mismatch")
        # The framed original exists independently of later domain validation.
        raw = cls.from_parts(scanned.canonical_header, attachments, defer_view=True)
        raw = replace(raw, _worker_contract=scanned.contract_worker_header)
        return raw if defer_view else raw.with_view()

    @classmethod
    def from_http_buffer(
        cls, content: bytes | memoryview, *, defer_view: bool = False
    ) -> Self:
        # Reject oversized mutable/noncontiguous carriers before bundle adoption
        # can copy them. The prefix/table bound is part of the wire envelope.
        size = len(content) if isinstance(content, bytes) else content.nbytes
        maximum = (
            RAW_RECEIPT_LIMITS.max_total_attachment_bytes
            + RAW_RECEIPT_LIMITS.max_header_bytes
            + RAW_RECEIPT_LIMITS.max_attachments * 8
            + 20
        )
        if size > maximum:
            raise RawReceiptError("receipt bundle exceeds its framed size limit")
        bundle = AttachmentBundle.from_bytes(content, RAW_RECEIPT_LIMITS)
        return cls.from_parts(bundle.header, bundle.attachments, defer_view=defer_view)

    @classmethod
    def from_bundle(
        cls, header: bytes, attachments: tuple[ImmutableBuffer, ...]
    ) -> Self:
        return cls.from_parts(header, attachments)

    @classmethod
    def from_parts(
        cls,
        header: bytes,
        attachments: tuple[ImmutableBuffer, ...],
        *,
        defer_view: bool = False,
        legacy_full_view: bool = False,
    ) -> Self:
        # Validate sizes before normalization can copy writable buffers.
        sizes = tuple(
            len(item) if isinstance(item, bytes) else item.nbytes
            for item in attachments
        )
        if (
            len(header) > RAW_RECEIPT_LIMITS.max_header_bytes
            or len(attachments) > RAW_RECEIPT_LIMITS.max_attachments
            or any(size > RAW_RECEIPT_LIMITS.max_attachment_bytes for size in sizes)
            or sum(sizes) > RAW_RECEIPT_LIMITS.max_total_attachment_bytes
        ):
            raise RawReceiptError("receipt parts exceed their wire size limits")
        bundle = AttachmentBundle(
            header=header,
            attachments=tuple(_owned_buffer(item) for item in attachments),
        )
        bundle.validate(RAW_RECEIPT_LIMITS)
        structure = scan_canonical_header(header)
        if defer_view:
            return cls(bundle, None, (), {}, structure.status, False, 0)
        contract_header = project_canonical_contract_header(header)
        contract_cost = _structure_cost(scan_canonical_header(contract_header))
        if contract_cost > _CONTRACT_VIEW_BYTES and not legacy_full_view:
            # Even when a contract view is unavailable, these exact valid JSON
            # framing bytes remain an independently saveable/downloadable owner.
            return cls(bundle, None, (), {}, structure.status, False, 0)
        contract_wire = _Header.model_validate_json(contract_header)
        full_cost = _structure_cost(structure)
        view_available = not defer_view and (
            legacy_full_view or full_cost <= _FULL_VIEW_BYTES
        )
        wire = _Header.model_validate_json(header) if view_available else contract_wire
        arrays: list[RawArray] = []
        if wire.readback is not None:
            for request_id, value in wire.readback.values.items():
                if not isinstance(value, _Array):
                    continue
                if value.index >= len(bundle.attachments):
                    raise RawReceiptError("array attachment index is out of range")
                content = bundle.attachments[value.index]
                maximum = _validate_array(value, content)
                arrays.append(
                    RawArray(
                        request_id,
                        value.dtype,
                        value.unit,
                        value.shape,
                        freeze_json_mapping(value.metadata),
                        value.index,
                        len(content),
                        maximum,
                    )
                )
        if sorted(value.index for value in arrays) != list(
            range(len(bundle.attachments))
        ):
            raise RawReceiptError("attachment references must be unique and complete")
        result = cls(
            bundle,
            wire,
            tuple(arrays),
            freeze_json_mapping(wire.metadata),
            structure.status,
            view_available,
            full_cost if view_available else contract_cost,
        )
        # Reuse domain outcome and scalar/unit/shape validation without building arrays.
        result._contract_receipt()
        return result

    @property
    def header(self) -> bytes:
        return self._bundle.header

    @property
    def attachments(self) -> tuple[ImmutableBuffer, ...]:
        return self._bundle.attachments

    @property
    def status(self) -> Literal["collected", "not_collected", "unknown"]:
        return self._status

    @property
    def problems(self) -> tuple[Problem, ...]:
        existing = () if self._wire is None else self._wire.problems
        if self.view_available:
            return existing
        return (
            *existing,
            problem(
                "direct_result_view_unavailable",
                "Original result is retained and can be saved or downloaded; "
                "its structured view exceeds the parsing budget.",
                phase=ProblemPhase.ANALYSIS,
            ),
        )

    @property
    def metadata(self) -> JsonMetadata:
        self.require_view()
        assert self._wire is not None
        return deepcopy(self._wire.metadata)

    @property
    def measured_cost(self) -> OperationCostMeasurement | None:
        return None if self._wire is None else self._wire.measured_cost

    @property
    def payload_bytes(self) -> int:
        return self._bundle.attachment_size_bytes

    @property
    def size_bytes(self) -> int:
        return self._bundle.size_bytes

    @property
    def materialization_bytes(self) -> int:
        # Includes flatten bytearray + bytes, decoded strings/tuple + NumPy's
        # padded Unicode array and its immutable copy. Deliberately conservative.
        extra = sum(
            2 * a.count * max(a.string_max_codepoints, 1) * 4
            + a.count * 80
            + a.size_bytes * 4
            for a in self.arrays
            if a.dtype == "string"
        )
        return self.size_bytes * 2 + len(self.header) * 128 + extra

    def segments(self) -> tuple[ImmutableBuffer, ...]:
        return self._bundle.segments(RAW_RECEIPT_LIMITS)

    def to_bytes(self) -> bytes:
        """Explicit legacy carrier; callers reserve the full flatten scratch first."""
        return self._bundle.to_bytes(RAW_RECEIPT_LIMITS)

    def with_view(self, *, legacy_full_view: bool = False) -> Self:
        if self.contract_error is not None:
            return self
        if self.view_available:
            return self
        if self._worker_contract is not None:
            structure = scan_worker_collect_header(self._worker_contract)
            if (
                legacy_full_view
                or _structure_cost(scan_canonical_header(structure.contract_header))
                <= _CONTRACT_VIEW_BYTES
            ):
                collect_attachment_sizes(self._worker_contract)
        return type(self).from_parts(
            self.header, self.attachments, legacy_full_view=legacy_full_view
        )

    def with_contract_error(self, message: str) -> Self:
        return replace(self, contract_error=message[:512], view_available=False)

    @property
    def resident_bytes(self) -> int:
        return (
            self.size_bytes + self.descriptor_bytes + len(self._worker_contract or b"")
        )

    def require_view(self) -> None:
        if not self.view_available:
            raise ReceiptMaterializationTooLarge(
                "Structured result view exceeds its parsing budget; "
                "the complete original remains retained for saving or download"
            )

    def materialize(self, *, max_bytes: int) -> CollectReceipt:
        self.require_view()
        assert self._wire is not None
        if max_bytes < self.materialization_bytes:
            raise ReceiptMaterializationTooLarge(
                "expanded receipt exceeds the read budget; original is still retained"
            )
        readback = self._wire.readback
        return CollectReceipt(
            status=self.status,
            problems=self.problems,
            metadata=self.metadata,
            measured_cost=self.measured_cost,
            readback=None
            if readback is None
            else InstrumentReadback(
                metadata=readback.metadata,
                values={
                    key: decode_measurement_array(
                        # A domain read must outlive its source's mmap lease.
                        # Independent immutable bytes keep numeric owners valid
                        # without flattening or imposing the HTTP array-count cap.
                        bytes(self.attachments[value.index]),
                        dtype=value.dtype,
                        unit=value.unit,
                        shape=value.shape,
                        metadata=value.metadata,
                    )
                    if isinstance(value, _Array)
                    else value
                    for key, value in readback.values.items()
                },
            ),
        )

    def validate_command(self, command: CollectCommand) -> list[Problem]:
        if self.contract_error is not None:
            return [
                problem(
                    "direct_result_contract_invalid",
                    self.contract_error,
                    phase=ProblemPhase.EXECUTION,
                )
            ]
        if self._wire is None:
            return [
                problem(
                    "direct_result_contract_view_unavailable",
                    "Original retained, but its contract view exceeds "
                    "the parsing budget",
                    phase=ProblemPhase.EXECUTION,
                )
            ]
        return validate_collect_receipt(
            command=command, receipt=self._contract_receipt()
        )

    def _contract_receipt(self) -> CollectReceipt:
        assert self._wire is not None
        readback = self._wire.readback
        return CollectReceipt(
            status=self.status,
            problems=self._wire.problems,
            metadata=self._wire.metadata,
            measured_cost=self.measured_cost,
            readback=None
            if readback is None
            else InstrumentReadback(
                metadata=readback.metadata,
                values={
                    key: MeasurementUnavailable.create(
                        dtype=value.dtype,
                        unit=value.unit,
                        shape=value.shape,
                        reason="missing",
                        metadata=value.metadata,
                    )
                    if isinstance(value, _Array)
                    else value
                    for key, value in readback.values.items()
                },
            ),
        )

    @property
    def inline_values(self) -> Mapping[str, MeasurementScalar | MeasurementUnavailable]:
        return (
            {}
            if self._wire is None or self._wire.readback is None
            else {
                key: value
                for key, value in self._wire.readback.values.items()
                if not isinstance(value, _Array)
            }
        )

    def array(self, request_id: str) -> RawArray:
        return next(value for value in self.arrays if value.request_id == request_id)

    def array_values(
        self, request_id: str, start: int, limit: int
    ) -> tuple[object, ...]:
        """A bounded flat slice. String cells require explicit bounded text access."""
        array = self.array(request_id)
        if start < 0 or limit < 0 or limit > _MAX_SLICE_ITEMS or start > array.count:
            raise ValueError("invalid bounded array slice")
        count = min(limit, array.count - start)
        if array.dtype == "string":
            raise ValueError("use string_value for explicitly bounded string cells")
        dtype = np.dtype("?") if array.dtype == "bool" else _numpy_dtype(array.dtype)
        values = np.frombuffer(
            self.attachments[array.index],
            dtype=dtype,
            count=count,
            offset=start * dtype.itemsize,
        )
        native = cast("list[object]", values.tolist())
        return tuple(native)

    def string_value(
        self, request_id: str, index: int, *, max_bytes: int = 4096
    ) -> tuple[str, bool]:
        """UTF-8 prefix and truncation marker; never expands a whole long cell."""
        array = self.array(request_id)
        if (
            array.dtype != "string"
            or not 0 <= index < array.count
            or not 0 <= max_bytes <= _CHUNK_BYTES
        ):
            raise ValueError("invalid bounded string cell")
        content = memoryview(self.attachments[array.index])
        start, end = struct.unpack_from("<QQ", content, index * 8)
        offset = (array.count + 1) * 8
        selected = bytes(content[offset + start : offset + min(end, start + max_bytes)])
        # A valid original can end this prefix midway through a codepoint.
        return selected.decode("utf-8", errors="ignore"), end - start > max_bytes


# A structure-aware admission estimate, not a wire-byte multiplier or OS RSS
# promise. The hard gate bounds nodes, containers, strings and scalar tokens
# before any optional Pydantic/frozen/deepcopy graph is constructed.
_FULL_VIEW_BYTES = 16 * 1024 * 1024
_CONTRACT_VIEW_BYTES = 32 * 1024 * 1024


def _structure_cost(value: HeaderStructure) -> int:
    return (
        value.node_count * 1024
        + value.container_count * 1024
        + value.string_bytes * 16
        + value.scalar_bytes * 32
    )


def _owned_buffer(content: ImmutableBuffer) -> ImmutableBuffer:
    # A readonly view can still expose a writable bytearray/mmap owner. Copy
    # that case rather than retaining data another caller can mutate in place.
    if isinstance(content, memoryview):
        owner = memoryview(content.obj)
        if not owner.readonly:
            return content.tobytes()
    return content


def _numpy_dtype(dtype: MeasurementDType) -> np.dtype[np.generic]:
    return np.dtype(
        {"float64": "<f8", "int64": "<i8", "complex128": "<c16", "bool": "u1"}[dtype]
    )


def _validate_array(array: _Array, content: ImmutableBuffer) -> int:
    width = 8 if array.dtype == "string" else _numpy_dtype(array.dtype).itemsize
    maximum_count = len(content) // width - (1 if array.dtype == "string" else 0)
    count = 0 if 0 in array.shape else 1
    if count:
        for extent in array.shape:
            if extent > maximum_count // count:
                raise RawReceiptError("array shape exceeds its bounded attachment")
            count *= extent
    if array.dtype == "string":
        table = (count + 1) * 8
        if table > len(content):
            raise RawReceiptError("invalid string offset table size")
        view = memoryview(content)
        previous = struct.unpack_from("<Q", view, 0)[0]
        if previous != 0:
            raise RawReceiptError("string offsets must start at zero")
        maximum = 0
        for index in range(count):
            end = struct.unpack_from("<Q", view, (index + 1) * 8)[0]
            if end < previous or end > len(view) - table:
                raise RawReceiptError("invalid string offsets")
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            length = 0
            try:
                for offset in range(previous, end, _CHUNK_BYTES):
                    length += len(
                        decoder.decode(
                            view[
                                table + offset : table + min(end, offset + _CHUNK_BYTES)
                            ]
                        )
                    )
                length += len(decoder.decode(b"", final=True))
            except UnicodeDecodeError as error:
                raise RawReceiptError("invalid UTF-8 string attachment") from error
            maximum = max(maximum, length)
            previous = end
        if previous != len(view) - table:
            raise RawReceiptError("string attachment has trailing bytes")
        return maximum
    dtype = _numpy_dtype(array.dtype)
    if count * dtype.itemsize != len(content):
        raise RawReceiptError("array shape does not match attachment size")
    if array.dtype == "int64":
        return 0
    step = max(1, _CHUNK_BYTES // dtype.itemsize)
    for start in range(0, count, step):
        values = np.frombuffer(
            content,
            dtype=dtype,
            count=min(step, count - start),
            offset=start * dtype.itemsize,
        )
        valid = (
            np.all(
                np.frombuffer(
                    content,
                    dtype=np.uint8,
                    count=min(step, count - start),
                    offset=start,
                )
                <= 1
            )
            if array.dtype == "bool"
            else np.isfinite(values).all()
        )
        if not valid:
            raise RawReceiptError("array attachment contains invalid values")
    return 0
