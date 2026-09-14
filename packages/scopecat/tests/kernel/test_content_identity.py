from __future__ import annotations

import hashlib

import numpy as np

from scopecat.kernel.content_identity import (
    canonical_json,
    content_fingerprint,
    stable_content_hash,
)


class _NoToBytesArray(np.ndarray):
    def tobytes(  # pyright: ignore[reportImplicitOverride]
        self, *args: object, **kwargs: object
    ) -> bytes:
        del args, kwargs
        raise AssertionError("contiguous arrays should be hashed through their buffer")


def test_stable_content_hash_matches_canonical_json_bytes() -> None:
    value = {
        "unicode": "并行波形",
        "nested": [{"value": index, "enabled": index % 2 == 0} for index in range(100)],
    }

    assert (
        stable_content_hash(value)
        == hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    )


def test_contiguous_array_fingerprint_hashes_the_existing_buffer() -> None:
    value = np.arange(128, dtype=np.float64).view(_NoToBytesArray)

    fingerprint = content_fingerprint(value)

    assert isinstance(fingerprint, dict)
    assert fingerprint["sha256"] == hashlib.sha256(memoryview(value)).hexdigest()


def test_noncontiguous_array_fingerprint_retains_c_order_identity() -> None:
    value = np.arange(24, dtype=np.int64).reshape(4, 6)[:, ::2]

    fingerprint = content_fingerprint(value)

    assert isinstance(fingerprint, dict)
    assert fingerprint["sha256"] == hashlib.sha256(value.tobytes()).hexdigest()


def test_mapping_key_optimization_preserves_existing_identities() -> None:
    # Digests captured before key-encoding reuse, including JSON escaping order.
    cases = [
        (
            {
                "line\n": {'"': 1, "\\": -0.0, "é": float("nan")},
                "z": [True, None, float("inf"), float("-inf")],
            },
            "88b5c8e03d662f901fad18056ce0d7b4d9b2086c464c0ce3c889ef386461ef0f",
        ),
        (
            {1: "integer", "1": "string", (1, "x"): {"nested": 2.5}},
            "52aa1c97d4294ef2903aa5b4f00c2212542316c612dc7abba952c13543bb950d",
        ),
    ]
    for mapping, expected in cases:
        assert stable_content_hash(content_fingerprint(mapping)) == expected
        reversed_mapping = dict(reversed(list(mapping.items())))
        assert stable_content_hash(content_fingerprint(reversed_mapping)) == expected


def test_mapping_value_edits_are_never_cached() -> None:
    values = {"drive": {"frequency": 4.8}}
    original = content_fingerprint(values)
    assert content_fingerprint(values) == original
    values["drive"]["frequency"] = 4.9
    assert content_fingerprint(values) != original
    values["drive"]["frequency"] = 4.8
    assert content_fingerprint(values) == original
