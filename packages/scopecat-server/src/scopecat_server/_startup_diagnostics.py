"""Opt-in detached startup evidence, independent of application logging."""

from __future__ import annotations

import faulthandler
import os
import time
from pathlib import Path
from typing import TextIO

_stream: TextIO | None = None
_started = time.monotonic()


def begin() -> None:
    """Record early Python entry and one stack before the normal deadline."""
    global _stream
    directory = os.environ.get("SCOPECAT_STARTUP_DIAGNOSTICS")
    if directory is None:
        return
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    _stream = (target / f"daemon-startup-{os.getpid()}.log").open(
        "w", encoding="utf-8", buffering=1
    )
    stage("python entry; importing CLI")
    faulthandler.dump_traceback_later(5, file=_stream)


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
