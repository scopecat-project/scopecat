"""Deterministic structural identities for transient compiler/runtime values."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Buffer, Iterable, Mapping, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from typing import cast

from pydantic import BaseModel

from scopecat.kernel.entity import (
    EntityRef,
    entity_identity,
)
from scopecat.kernel.payloads import PayloadValue


def stable_content_hash(value: object) -> str:
    """Hash an already-JSON-safe identity with canonical JSON."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_json_hash(value: object) -> str:
    """Identify canonical JSON content with an explicit hash algorithm."""

    return f"sha256:{stable_content_hash(value)}"


def sha256_content_hash(content: Buffer) -> str:
    """Identify an exact byte representation with an explicit hash algorithm."""

    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def sha256_content_hash_segments(segments: Iterable[Buffer]) -> str:
    """Identify ordered contiguous buffers without joining their content."""

    digest = hashlib.sha256()
    for segment in segments:
        digest.update(segment)
    return f"sha256:{digest.hexdigest()}"


def content_fingerprint(value: object) -> object:
    """Return a type-preserving deterministic structural identity.

    Opaque extension objects must explicitly provide
    ``__scopecat_fingerprint__``. Process-local object identity is never a
    valid cache key.
    """

    if value is None:
        return {"kind": "none"}
    if isinstance(value, type):
        return {
            "kind": "python_type",
            "module": value.__module__,
            "qualname": value.__qualname__,
        }
    if isinstance(value, Enum):
        return {
            "kind": "enum",
            "type": _type_name(value),
            "value": content_fingerprint(cast("object", value.value)),
        }
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, int):
        return {"kind": "int", "value": str(value)}
    if isinstance(value, Decimal):
        normalized = Decimal(0) if value == 0 else value.normalize()
        return {"kind": "decimal", "value": str(normalized)}
    if isinstance(value, float | complex):
        return _numeric_fingerprint(value)
    if isinstance(value, str):
        return {"kind": "str", "value": value}
    if isinstance(value, datetime | date | time):
        return {
            "kind": type(value).__name__,
            "value": value.isoformat(),
        }
    if isinstance(value, bytes | bytearray | memoryview):
        return _bytes_fingerprint(cast("Buffer", value))
    if isinstance(value, EntityRef):
        return {"kind": "entity", "value": entity_identity(value)}
    if isinstance(value, PayloadValue):
        return {
            "kind": "payload",
            "schema_id": value.schema_id,
            "value": content_fingerprint(value.payload),
        }
    if isinstance(value, BaseModel):
        return {
            "kind": "model",
            "type": _type_name(value),
            # Read declared fields directly so intentionally excluded wire
            # fields still participate in transient content identity.
            "fields": [
                [name, content_fingerprint(cast("object", getattr(value, name)))]
                for name in type(value).model_fields
            ],
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "kind": "dataclass",
            "type": _type_name(value),
            "fields": [
                [
                    field.name,
                    content_fingerprint(cast("object", getattr(value, field.name))),
                ]
                for field in dataclass_fields(value)
            ],
        }
    if isinstance(value, Mapping):
        return _mapping_fingerprint(cast("Mapping[object, object]", value))
    if isinstance(value, set | frozenset):
        selected_set = cast("set[object] | frozenset[object]", value)
        items: list[object] = [content_fingerprint(item) for item in selected_set]
        items.sort(key=canonical_json)
        return {
            "kind": "set",
            "type": _type_name(cast("object", value)),
            "items": items,
        }
    if isinstance(value, Sequence):
        sequence = value
        return {
            "kind": "sequence",
            "type": _type_name(cast("object", value)),
            "items": [content_fingerprint(item) for item in sequence],
        }
    shape = cast("Sequence[object] | None", getattr(value, "shape", None))
    dtype = cast("object | None", getattr(value, "dtype", None))
    if shape is not None or dtype is not None:
        return _array_fingerprint(value, shape=shape, dtype=dtype)
    fingerprint = getattr(value, "__scopecat_fingerprint__", None)
    if callable(fingerprint):
        return {
            "kind": "extension",
            "type": _type_name(value),
            "value": content_fingerprint(fingerprint()),
        }
    msg = (
        f"value of type {_type_name(value)} has no stable fingerprint; "
        "use a model/dataclass/collection/array or implement "
        "__scopecat_fingerprint__()"
    )
    raise TypeError(msg)


def _numeric_fingerprint(value: object) -> dict[str, object]:
    if isinstance(value, complex):
        return {
            "kind": "complex",
            "real": _numeric_fingerprint(value.real),
            "imag": _numeric_fingerprint(value.imag),
        }
    selected = cast("float", value)
    if math.isnan(selected):
        encoded = "nan"
    elif math.isinf(selected):
        encoded = "+inf" if selected > 0 else "-inf"
    else:
        encoded = selected.hex()
    return {"kind": "float", "value": encoded}


def _bytes_fingerprint(
    value: Buffer,
) -> dict[str, object]:
    buffer_identity = _contiguous_buffer_identity(value)
    if buffer_identity is None:
        encoded_bytes = bytes(value)
        length = len(encoded_bytes)
        digest = hashlib.sha256(encoded_bytes).hexdigest()
    else:
        length, digest = buffer_identity
    return {
        "kind": "bytes",
        "length": length,
        "sha256": digest,
    }


def _array_fingerprint(
    value: object,
    *,
    shape: Sequence[object] | None,
    dtype: object | None,
) -> dict[str, object]:
    if cast("bool", getattr(dtype, "hasobject", False)):
        msg = f"cannot fingerprint object-backed array {_type_name(value)}"
        raise TypeError(msg)
    buffer_identity = _contiguous_buffer_identity(cast("Buffer", value))
    if buffer_identity is None:
        to_bytes = getattr(value, "tobytes", None)
        if not callable(to_bytes):
            msg = f"array-like value {_type_name(value)} has no stable byte codec"
            raise TypeError(msg)
        try:
            encoded = to_bytes()
        except Exception as error:
            msg = f"array-like value {_type_name(value)} cannot be encoded"
            raise TypeError(msg) from error
        if not isinstance(encoded, bytes):
            msg = f"array-like value {_type_name(value)} returned non-byte content"
            raise TypeError(msg)
        digest = hashlib.sha256(encoded).hexdigest()
    else:
        _, digest = buffer_identity
    strides = cast("Sequence[object] | None", getattr(value, "strides", None))
    return {
        "kind": "array",
        "type": _type_name(value),
        "sha256": digest,
        "shape": list(shape) if shape is not None else None,
        "dtype": str(dtype) if dtype is not None else None,
        "strides": list(strides) if strides else None,
    }


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _mapping_fingerprint(value: Mapping[object, object]) -> dict[str, object]:
    ordered: list[tuple[str, list[object]]] = []
    for key, item in value.items():
        key_fingerprint = content_fingerprint(key)
        order = (
            _string_key_order(key)
            if type(key) is str
            else canonical_json(key_fingerprint)
        )
        ordered.append((order, [key_fingerprint, content_fingerprint(item)]))
    ordered.sort(key=lambda item: item[0])
    return {
        "kind": "mapping",
        "type": _type_name(cast("object", value)),
        "entries": [entry for _, entry in ordered],
    }


@lru_cache(maxsize=1024)
def _string_key_order(key: str) -> str:
    # Cache only immutable key encodings, never model/config values or digests.
    return canonical_json({"kind": "str", "value": key})


def _contiguous_buffer_identity(value: Buffer) -> tuple[int, str] | None:
    try:
        view = memoryview(value)
    except TypeError:
        return None
    if not view.c_contiguous:
        return None
    return view.nbytes, hashlib.sha256(view).hexdigest()


def model_wire_content_hash(model: BaseModel) -> str:
    """Hash the normalized JSON snapshot that a durable model writer publishes.

    JSON normalization deliberately happens before structural fingerprinting:
    this makes the digest match the reloaded wire while the fingerprint still
    gives NaN and infinities deterministic JSON-safe identities.
    """

    wire_snapshot = model.model_dump(mode="json")
    return stable_content_hash(content_fingerprint(wire_snapshot))


def _type_name(value: object) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"
