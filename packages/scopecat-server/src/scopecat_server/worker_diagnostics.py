"""Small stderr diagnostics for the existing isolated author/launch workers."""

from __future__ import annotations

import sys

DIAGNOSTIC_LIMIT = 8192
AUTHOR_VALIDATION_TIMEOUT_EXIT = 75
_STAGE_PREFIX = "Scopecat worker stage: "
_STAGES = frozenset(
    {
        "framework imports",
        "source compilation",
        "application import",
        "source identity validation",
        "author revision initialization",
        "project application load",
        "launch provider",
    }
)


def report_stage(stage: str) -> None:
    print(f"{_STAGE_PREFIX}{stage}", file=sys.stderr, flush=True)


def diagnostic_excerpt(stderr: str | bytes | None) -> tuple[str, str]:
    """Return a known stage and at most 8 KiB of decoded stderr evidence."""
    raw = (
        stderr.encode("utf-8", errors="replace")
        if isinstance(stderr, str)
        else stderr or b""
    )
    stage = "worker startup (no stage received)"
    for line in raw.splitlines():
        if line.startswith(_STAGE_PREFIX.encode()):
            candidate = line[len(_STAGE_PREFIX) :].decode("utf-8", errors="replace")
            if candidate in _STAGES:
                stage = candidate
    tail = raw[-DIAGNOSTIC_LIMIT:].decode("utf-8", errors="replace")
    if len(raw) > DIAGNOSTIC_LIMIT:
        tail = "[earlier stderr omitted; retaining last 8 KiB]\n" + tail
    return stage, tail
