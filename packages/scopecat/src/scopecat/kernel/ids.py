"""Identifier helpers."""

from __future__ import annotations

import re
from uuid import UUID, uuid7

SAFE_ARTIFACT_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def new_run_id() -> str:
    """Return an opaque run identity in canonical UUIDv7 string form."""

    return str(uuid7())


def validate_run_id(value: str) -> None:
    """Require the exact UUIDv7 spelling without rewriting stable references."""

    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("run_id must be a canonical UUIDv7 string") from None
    if parsed.version != 7 or str(parsed) != value:
        raise ValueError("run_id must be a canonical UUIDv7 string")


def artifact_slug(value: str, *, fallback: str = "artifact") -> str:
    slug = SAFE_ARTIFACT_ID_RE.sub("-", value.strip()).strip("-").lower()
    return slug or fallback
