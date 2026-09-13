"""Opt-in detached startup evidence, independent of application logging."""

from __future__ import annotations

import faulthandler
import os
import time
from pathlib import Path
from typing import Literal, TextIO

_stream: TextIO | None = None
_started = time.monotonic()


def begin(*, process: Literal["daemon", "instrument"] = "daemon") -> None:
    """Record early Python entry and one stack before the normal deadline."""
    global _stream, _started
    directory = os.environ.get("SCOPECAT_STARTUP_DIAGNOSTICS")
    if directory is None:
        return
    _started = time.monotonic()
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    _stream = (target / f"{process}-startup-{os.getpid()}.log").open(
        "w", encoding="utf-8", buffering=1
    )
    stage(
        f"python entry; pid={os.getpid()} parent={os.getppid()} "
        f"clock_ns={time.monotonic_ns()}"
    )
    # The daemon may finish child readiness after the old five-second sample.
    # Capture its remaining construction closer to the unchanged health deadline.
    faulthandler.dump_traceback_later(8 if process == "daemon" else 5, file=_stream)


def stage(message: str) -> None:
    if _stream is not None:
        _stream.write(f"{time.monotonic() - _started:.3f}s {message}\n")
        _stream.flush()


def finish() -> None:
    global _stream
    if _stream is not None:
        faulthandler.cancel_dump_traceback_later()
        _stream.close()
        _stream = None
