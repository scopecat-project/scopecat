"""Opt-in detached startup evidence, independent of application logging."""

from __future__ import annotations

import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from threading import RLock
from typing import Literal, TextIO

import scopecat_server._timed_diagnostics as _timed_diagnostics

_stream: TextIO | None = None
_started = time.monotonic()
_stream_lock = RLock()


def begin(*, process: Literal["daemon", "instrument"] = "daemon") -> None:
    """Record early Python entry and one stack before the normal deadline."""
    global _stream, _started
    with _stream_lock:
        finish()
        directory = os.environ.get("SCOPECAT_STARTUP_DIAGNOSTICS")
        if directory is None:
            stage(f"python entry; pid={os.getpid()}")
            return
        _started = time.monotonic()
        entered_ns = time.monotonic_ns()
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        _stream = (target / f"{process}-startup-{os.getpid()}.log").open(
            "w", encoding="utf-8", buffering=1
        )
        stage(
            f"python entry; pid={os.getpid()} parent={os.getppid()} "
            f"clock_ns={entered_ns}"
        )
        launch = os.environ.get("SCOPECAT_STARTUP_LAUNCH_NS")
        if process == "daemon" and launch is not None:
            elapsed = (entered_ns - int(launch)) / 1_000_000_000
            stage(f"launch request to Python entry: {elapsed:.3f}s")
        # The daemon may finish child readiness after the old five-second sample.
        # Capture its remaining construction closer to the unchanged health deadline.
        _timed_diagnostics.schedule(8 if process == "daemon" else 5, file=_stream)


def stage(message: str) -> None:
    with _stream_lock:
        if os.environ.get("SCOPECAT_STARTUP_PROGRESS") == "1":
            print(f"[startup] {message}", file=sys.stderr, flush=True)
        if _stream is not None:
            _stream.write(f"{time.monotonic() - _started:.3f}s {message}\n")
            _stream.flush()


def observe_shutdown() -> None:
    """Sample a slow authorized shutdown before the caller's default deadline."""
    with _stream_lock:
        if _stream is not None:
            with suppress(OSError, ValueError):
                stage("shutdown accepted")
                _timed_diagnostics.schedule(5, file=_stream)


def shutdown_stage(message: str) -> None:
    """Leave normal shutdown output unchanged unless diagnostics are enabled."""
    with _stream_lock:
        if _stream is not None:
            with suppress(OSError, ValueError):
                stage(message)


def finish() -> None:
    global _stream
    with _stream_lock:
        if _stream is not None:
            _timed_diagnostics.cancel()
            with suppress(OSError, ValueError):
                _stream.close()
            _stream = None
