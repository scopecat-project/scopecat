"""Small stderr diagnostics for the existing isolated author/launch workers."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pydantic import ValidationError

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
        "retained analysis",
        "retained comparison",
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


def worker_server_timing(stderr: str, *, total_seconds: float) -> str:
    """Nested milliseconds for the developer HTTP timing surface."""
    timings = {"launch": total_seconds * 1000}
    for line in stderr.splitlines():
        if line.startswith("Scopecat launch timing: "):
            phases = cast(
                "dict[str, float]",
                json.loads(line.removeprefix("Scopecat launch timing: ")),
            )
            timings.update({name: seconds * 1000 for name, seconds in phases.items()})
    return ", ".join(f"{name};dur={duration:.3f}" for name, duration in timings.items())


def report_validation_error(error: ValidationError) -> None:
    """Keep field locations in the final diagnostic line consumed by HTTP."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'value'}: {item['msg']}"
        for item in error.errors(include_url=False, include_input=False)
    )
    print(" ".join(f"{error.title}: {details}".splitlines()), file=sys.stderr)
