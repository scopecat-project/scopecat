"""Opt-in parent observations of detached daemons; never restart or probe HTTP."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psutil
from scopecat.runtime_binding import load_runtime_binding


@dataclass
class _Observed:
    root: Path
    process: subprocess.Popen[bytes]
    created: float | None
    target: Path


_observed: dict[Path, _Observed] = {}
_lock = threading.Lock()
_LOG_BYTES = 65536


def observe_spawn(root: Path, process: subprocess.Popen[bytes]) -> None:
    directory = os.environ.get("SCOPECAT_STARTUP_DIAGNOSTICS")
    if directory is None:
        return
    try:
        target = Path(directory).resolve()
        target.mkdir(parents=True, exist_ok=True)
        target = target / (
            f"daemon-lifetime-{os.getpid()}-{process.pid}-{time.monotonic_ns()}"
        )
        try:
            created = psutil.Process(process.pid).create_time()
        except psutil.NoSuchProcess:
            created = None  # Immediate exit; retain the original Popen exit status.
        observation = _Observed(root.resolve(), process, created, target)
        with _lock:
            _observed[target] = observation
        capture("spawn", root=root)
    except (OSError, psutil.Error) as error:
        logging.getLogger(__name__).warning("Daemon diagnostics unavailable: %s", error)


def capture(phase: str, *, root: Path | None = None) -> None:
    """Snapshot before teardown removes records; retire exited process handles."""
    with _lock:
        for key, observed in tuple(_observed.items()):
            if root is not None and observed.root != root.resolve():
                continue
            try:
                code = observed.process.poll()
                log_target = observed.target.with_name(
                    f"{observed.target.name}-{time.monotonic_ns()}.log"
                )
                row = {
                    "time": datetime.now(UTC).isoformat(),
                    "clock_ns": time.monotonic_ns(),
                    "phase": phase,
                    "observer_pid": os.getpid(),
                    "worker": os.environ.get("PYTEST_XDIST_WORKER"),
                    "project_root": str(observed.root),
                    "pid": observed.process.pid,
                    "create_time": observed.created,
                    "returncode": code,
                    "log_tail": log_target.name,
                }
                with observed.target.with_suffix(".jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(json.dumps(row) + "\n")
                log = load_runtime_binding(observed.root).data_root / "daemon.log"
                if log.is_file():
                    with log.open("rb") as stream:
                        stream.seek(0, os.SEEK_END)
                        stream.seek(max(0, stream.tell() - _LOG_BYTES))
                        tail = stream.read(_LOG_BYTES)
                    log_target.write_bytes(tail)
                if code is not None:
                    del _observed[key]
            except OSError as error:
                logging.getLogger(__name__).warning(
                    "Daemon diagnostics unavailable: %s", error
                )
