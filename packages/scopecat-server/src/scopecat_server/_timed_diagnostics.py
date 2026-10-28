"""Timed Python stack samples without an unsynchronized native frame walker.

The sampling thread must acquire the GIL before calling dump_traceback. Unlike
faulthandler's native watchdog, it cannot diagnose C code indefinitely holding
the GIL. Fatal-signal handling remains separate and is never changed here.
"""

from __future__ import annotations

import faulthandler
import math
from datetime import timedelta
from threading import Event, Lock, Thread
from typing import TextIO

_lock = Lock()
_thread: Thread | None = None
_stop: Event | None = None


def _sample(delay: float, repeat: bool, stream: TextIO, stop: Event) -> None:
    while not stop.wait(delay):
        try:
            stream.write(f"Timeout ({timedelta(seconds=delay)})!\n")
            stream.flush()
            # Called by a Python thread with the GIL, not a native watchdog
            # traversing frames while the interpreter mutates/frees them.
            faulthandler.dump_traceback(file=stream, all_threads=True)
        except OSError, ValueError:
            # Diagnostics must not replace the original operational failure.
            return
        if not repeat:
            return


def _cancel_locked() -> None:
    global _thread, _stop
    if _thread is not None:
        assert _stop is not None
        _stop.set()
        # Do not return while a sample can still use the caller-owned stream.
        # The sampler never acquires _lock, so joining under it is safe.
        _thread.join()
        _thread = None
        _stop = None


def schedule(delay: float, *, file: TextIO, repeat: bool = False) -> None:
    """Replace the process's diagnostic sample; the caller owns the open file."""
    global _thread, _stop
    if not math.isfinite(delay) or delay <= 0:
        raise ValueError("diagnostic sample delay must be finite and positive")
    with _lock:
        _cancel_locked()
        stop = Event()
        thread = Thread(
            target=_sample,
            args=(delay, repeat, file, stop),
            name="scopecat-traceback-sampler",
            daemon=True,
        )
        thread.start()
        _stop = stop
        _thread = thread


def cancel() -> None:
    """Cancel and join the sampler before its file can be closed or replaced."""
    with _lock:
        _cancel_locked()
