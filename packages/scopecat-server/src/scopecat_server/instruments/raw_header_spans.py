"""Bounded JSON syntax scanning and span-only worker receipt conversion.

This establishes framing/identity, not measurement semantics. Metadata and inline
value graphs are never decoded. Strong domain validation remains a separately
budgeted operation on the retained original.
"""

from __future__ import annotations

import codecs
import json
import math
import re
from dataclasses import dataclass
from typing import Literal, cast

_NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_HEX = frozenset(b"0123456789abcdefABCDEF")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_HEADER = 1024 * 1024
_MAX_DEPTH = 200
_MAX_ATTACHMENTS = 4096

type Span = tuple[int, int]


class RawHeaderError(ValueError):
    """Malformed or out-of-envelope worker framing."""


@dataclass(frozen=True, slots=True)
class RawAttachmentManifest:
    index: int
    request_id: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class HeaderStructure:
    status: Literal["collected", "not_collected", "unknown"]
    node_count: int
    container_count: int
    max_depth: int
    string_bytes: int
    scalar_bytes: int


@dataclass(frozen=True, slots=True)
class ScannedCollectHeader:
    canonical_header: bytes
    contract_header: bytes
    contract_worker_header: bytes
    manifests: tuple[RawAttachmentManifest, ...]
    status: Literal["collected", "not_collected", "unknown"]
    node_count: int
    container_count: int
    max_depth: int
    string_bytes: int
    scalar_bytes: int


class _Scanner:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.nodes = self.containers = self.depth = self.string_bytes = 0
        self.scalar_bytes = 0

    def whitespace(self) -> None:
        while self.pos < len(self.data) and self.data[self.pos] in b" \t\n\r":
            self.pos += 1

    def require(self, byte: int) -> None:
        if self.pos >= len(self.data) or self.data[self.pos] != byte:
            raise RawHeaderError("invalid JSON delimiter")
        self.pos += 1

    def string(self) -> Span:
        start = self.pos
        self.require(34)
        while self.pos < len(self.data):
            byte = self.data[self.pos]
            self.pos += 1
            if byte == 34:
                self.string_bytes += self.pos - start
                return start, self.pos
            if byte < 32:
                raise RawHeaderError("unescaped JSON control character")
            if byte == 92:
                if self.pos >= len(self.data):
                    raise RawHeaderError("truncated JSON escape")
                escape = self.data[self.pos]
                self.pos += 1
                if escape == 117:
                    digits = self.data[self.pos : self.pos + 4]
                    if len(digits) != 4 or any(item not in _HEX for item in digits):
                        raise RawHeaderError("invalid JSON unicode escape")
                    self.pos += 4
                    codepoint = int(digits, 16)
                    if 0xD800 <= codepoint <= 0xDBFF:
                        pair = self.data[self.pos : self.pos + 6]
                        if (
                            len(pair) != 6
                            or pair[:2] != b"\\u"
                            or any(item not in _HEX for item in pair[2:])
                            or not 0xDC00 <= int(pair[2:], 16) <= 0xDFFF
                        ):
                            raise RawHeaderError("invalid JSON surrogate pair")
                        self.pos += 6
                    elif 0xDC00 <= codepoint <= 0xDFFF:
                        raise RawHeaderError("unpaired JSON surrogate")
                elif escape not in b'"\\/bfnrt':
                    raise RawHeaderError("invalid JSON escape")
        raise RawHeaderError("unterminated JSON string")

    def value(self, depth: int = 0) -> Span:
        self.whitespace()
        start = self.pos
        if start == len(self.data):
            raise RawHeaderError("missing JSON value")
        self.nodes += 1
        byte = self.data[start]
        if byte in (123, 91):
            depth += 1
            if depth > _MAX_DEPTH:
                raise RawHeaderError("JSON nesting exceeds worker parser envelope")
            self.depth = max(self.depth, depth)
            self.containers += 1
            self.pos += 1
            self.whitespace()
            end = 125 if byte == 123 else 93
            if self.pos < len(self.data) and self.data[self.pos] == end:
                self.pos += 1
                return start, self.pos
            while True:
                if byte == 123:
                    self.string()
                    self.whitespace()
                    self.require(58)
                self.value(depth)
                self.whitespace()
                if self.pos < len(self.data) and self.data[self.pos] == end:
                    self.pos += 1
                    break
                self.require(44)
                self.whitespace()
        elif byte == 34:
            self.string()
        elif byte in b"-0123456789":
            match = _NUMBER.match(self.data, self.pos)
            if match is None:
                raise RawHeaderError("invalid JSON number")
            self.pos = match.end()
            number = self.data[start : self.pos]
            if any(byte in number for byte in b".eE"):
                if not math.isfinite(float(number)):
                    raise RawHeaderError("non-finite JSON number")
            elif len(number.lstrip(b"-")) > 4300:
                # Same integer-token limit as the existing Pydantic JSON parser.
                raise RawHeaderError("JSON integer exceeds parser digit limit")
        else:
            for literal in (b"true", b"false", b"null"):
                if self.data.startswith(literal, self.pos):
                    self.pos += len(literal)
                    break
            else:
                raise RawHeaderError("invalid JSON value")
        if byte not in (123, 91):
            self.scalar_bytes += self.pos - start
        return start, self.pos

    def members(self, span: Span) -> dict[str, Span]:
        self.pos = span[0]
        self.require(123)
        result: dict[str, Span] = {}
        self.whitespace()
        if self.data[self.pos] == 125:
            return result
        while True:
            key = self.text(self.string())
            if key in result:
                raise RawHeaderError("duplicate critical JSON field")
            self.whitespace()
            self.require(58)
            result[key] = self.value()
            self.whitespace()
            if self.data[self.pos] == 125:
                return result
            self.require(44)
            self.whitespace()

    def items(self, span: Span, *, limit: int = _MAX_ATTACHMENTS) -> tuple[Span, ...]:
        self.pos = span[0]
        self.require(91)
        result: list[Span] = []
        self.whitespace()
        if self.data[self.pos] == 93:
            return ()
        while True:
            if len(result) >= limit:
                raise RawHeaderError("too many worker descriptors")
            result.append(self.value())
            self.whitespace()
            if self.data[self.pos] == 93:
                return tuple(result)
            self.require(44)
            self.whitespace()

    def raw(self, span: Span) -> bytes:
        return self.data[span[0] : span[1]]

    def text(self, span: Span) -> str:
        if self.data[span[0]] != 34:
            raise RawHeaderError("expected JSON string")
        value = cast("object", json.loads(self.raw(span)))
        if not isinstance(value, str):
            raise RawHeaderError("expected JSON string")
        return value

    def integer(self, span: Span) -> int:
        raw = self.raw(span)
        if not raw or any(byte not in b"0123456789" for byte in raw) or len(raw) > 20:
            raise RawHeaderError("invalid bounded unsigned integer")
        return int(raw)


def _fields(fields: dict[str, Span], allowed: set[str], required: set[str]) -> None:
    if not required <= fields.keys() or fields.keys() - allowed:
        raise RawHeaderError("invalid worker descriptor fields")


def _object(
    scanner: _Scanner, fields: dict[str, Span], replacements: dict[str, bytes]
) -> bytes:
    parts = [
        json.dumps(key, ensure_ascii=True).encode()
        + b":"
        + (replacements[key] if key in replacements else scanner.raw(span))
        for key, span in fields.items()
    ]
    parts.extend(
        json.dumps(key).encode() + b":" + value
        for key, value in replacements.items()
        if key not in fields
    )
    return b"{" + b",".join(parts) + b"}"


def scan_worker_collect_header(header: bytes) -> ScannedCollectHeader:
    if len(header) > _MAX_HEADER:
        raise RawHeaderError("worker header exceeds size limit")
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        for start in range(0, len(header), 65536):
            decoder.decode(header[start : start + 65536])
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise RawHeaderError("invalid UTF-8 JSON header") from error
    scanner = _Scanner(header)
    root_span = scanner.value()
    scanner.whitespace()
    if scanner.pos != len(header):
        raise RawHeaderError("trailing JSON content")
    complexity = (
        scanner.nodes,
        scanner.containers,
        scanner.depth,
        scanner.string_bytes,
        scanner.scalar_bytes,
    )
    root = scanner.members(root_span)
    _fields(
        root,
        {"protocol_version", "receipt", "arrays", "attachments"},
        {"protocol_version", "receipt"},
    )
    if scanner.integer(root["protocol_version"]) != 2:
        raise RawHeaderError("unsupported worker version")
    receipt = scanner.members(root["receipt"])
    _fields(
        receipt,
        {"status", "problems", "readback", "metadata", "measured_cost"},
        {"status"},
    )
    status = scanner.text(receipt["status"])
    if status not in ("collected", "not_collected", "unknown"):
        raise RawHeaderError("invalid receipt status")
    array_spans = scanner.items(root["arrays"]) if "arrays" in root else ()
    manifest_spans = scanner.items(root["attachments"]) if "attachments" in root else ()
    if len(array_spans) != len(manifest_spans):
        raise RawHeaderError("worker attachment count mismatch")
    inline: dict[str, Span] = {}
    readback: dict[str, Span] | None = None
    if "readback" in receipt and scanner.raw(receipt["readback"]) != b"null":
        readback = scanner.members(receipt["readback"])
        _fields(readback, {"values", "metadata"}, set())
        if "values" in readback:
            inline = scanner.members(readback["values"])
    elif array_spans:
        raise RawHeaderError("array receipt requires readback")
    manifests: list[RawAttachmentManifest] = []
    additions: dict[str, bytes] = {}
    contract_arrays: list[bytes] = []
    previous: str | None = None
    total = 0
    for index, (array_span, manifest_span) in enumerate(
        zip(array_spans, manifest_spans, strict=True)
    ):
        array = scanner.members(array_span)
        _fields(
            array,
            {"request_id", "dtype", "unit", "shape", "metadata"},
            {"request_id", "dtype", "shape"},
        )
        contract_arrays.append(
            _object(scanner, array, _empty_mapping(scanner, array, "metadata"))
        )
        request_id = scanner.text(array.pop("request_id"))
        if (
            not request_id
            or request_id in inline
            or (previous is not None and request_id <= previous)
        ):
            raise RawHeaderError("duplicate or unordered worker request identity")
        previous = request_id
        fields = scanner.members(manifest_span)
        _fields(
            fields,
            {"index", "request_id", "size_bytes", "sha256"},
            {"index", "request_id", "size_bytes", "sha256"},
        )
        size = scanner.integer(fields["size_bytes"])
        digest = scanner.text(fields["sha256"])
        if (
            scanner.integer(fields["index"]) != index
            or scanner.text(fields["request_id"]) != request_id
        ):
            raise RawHeaderError("worker manifest identity mismatch")
        if size > 128 * 1024 * 1024 or _SHA.fullmatch(digest) is None:
            raise RawHeaderError("invalid worker attachment manifest")
        total += size
        if total > 512 * 1024 * 1024:
            raise RawHeaderError("worker attachments exceed aggregate limit")
        manifests.append(RawAttachmentManifest(index, request_id, size, digest))
        additions[request_id] = _object(
            scanner,
            array,
            {"kind": b'"array_attachment"', "index": str(index).encode()},
        )
    changes = {"format_id": b'"scopecat.collect_receipt.v1"'}
    if readback is not None:
        changes["readback"] = _object(
            scanner, readback, {"values": _object(scanner, inline, additions)}
        )
    canonical = _object(scanner, receipt, changes)
    if len(canonical) > 8 * 1024 * 1024:
        raise RawHeaderError("canonical header exceeds size limit")
    canonical_scanner = _Scanner(canonical)
    canonical_fields = canonical_scanner.members((0, len(canonical)))
    contract = _project_receipt(canonical_scanner, canonical_fields)
    worker_contract = _object(
        scanner,
        root,
        {
            "receipt": _project_receipt(scanner, receipt),
            "arrays": b"[" + b",".join(contract_arrays) + b"]",
        },
    )
    return ScannedCollectHeader(
        canonical, contract, worker_contract, tuple(manifests), status, *complexity
    )


def _empty_mapping(
    scanner: _Scanner, fields: dict[str, Span], key: str
) -> dict[str, bytes]:
    if key in fields and scanner.data[fields[key][0]] != 123:
        raise RawHeaderError("metadata/details must be a JSON object")
    return {key: b"{}"}


def _project_fields(scanner: _Scanner, span: Span, key: str) -> bytes:
    fields = scanner.members(span)
    return _object(scanner, fields, _empty_mapping(scanner, fields, key))


def _project_receipt(scanner: _Scanner, fields: dict[str, Span]) -> bytes:
    changes = _empty_mapping(scanner, fields, "metadata")
    if "readback" in fields and scanner.raw(fields["readback"]) != b"null":
        readback = scanner.members(fields["readback"])
        replacements = _empty_mapping(scanner, readback, "metadata")
        if "values" in readback:
            values = scanner.members(readback["values"])
            projected = {
                key: _project_fields(scanner, span, "metadata")
                for key, span in values.items()
            }
            replacements["values"] = _object(scanner, values, projected)
        changes["readback"] = _object(scanner, readback, replacements)
    if "problems" in fields:
        projected_problems = [
            _project_fields(scanner, span, "details")
            for span in scanner.items(fields["problems"], limit=len(scanner.data))
        ]
        changes["problems"] = b"[" + b",".join(projected_problems) + b"]"
    return _object(scanner, fields, changes)


def scan_canonical_header(header: bytes) -> HeaderStructure:
    """Inspect a saved canonical header without decoding metadata/value graphs."""
    if len(header) > 8 * 1024 * 1024:
        raise RawHeaderError("canonical header exceeds size limit")
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        for start in range(0, len(header), 65536):
            decoder.decode(header[start : start + 65536])
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise RawHeaderError("invalid UTF-8 JSON header") from error
    scanner = _Scanner(header)
    span = scanner.value()
    scanner.whitespace()
    if scanner.pos != len(header):
        raise RawHeaderError("trailing JSON content")
    complexity = (
        scanner.nodes,
        scanner.containers,
        scanner.depth,
        scanner.string_bytes,
        scanner.scalar_bytes,
    )
    fields = scanner.members(span)
    _fields(
        fields,
        {"format_id", "status", "problems", "readback", "metadata", "measured_cost"},
        {"status"},
    )
    if (
        "format_id" in fields
        and scanner.text(fields["format_id"]) != "scopecat.collect_receipt.v1"
    ):
        raise RawHeaderError("unsupported canonical receipt format")
    status = scanner.text(fields["status"])
    if status not in ("collected", "not_collected", "unknown"):
        raise RawHeaderError("invalid receipt status")
    return HeaderStructure(status, *complexity)


def project_canonical_contract_header(header: bytes) -> bytes:
    """Validate syntax/envelope, then strip only schema-owned free-form graphs."""
    scan_canonical_header(header)
    scanner = _Scanner(header)
    return _project_receipt(scanner, scanner.members((0, len(header))))
