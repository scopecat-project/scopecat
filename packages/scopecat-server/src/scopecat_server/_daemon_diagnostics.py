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
from scopecat.daemon.endpoint import DaemonEndpointError, read_daemon_endpoint_record
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


def _process_snapshot(pid: int) -> dict[str, object]:
    try:
        process = psutil.Process(pid)
        return {
            "pid": pid,
            "create_time": process.create_time(),
            "status": process.status(),
            "threads": process.num_threads(),
            "children": [
                {"pid": child.pid, "create_time": child.create_time()}
                for child in process.children(recursive=True)
            ],
        }
    except psutil.Error as error:
        return {"pid": pid, "observation_error": type(error).__name__}


def _endpoint_snapshot(root: Path) -> dict[str, object] | None:
    try:
        record = read_daemon_endpoint_record(root)
    except DaemonEndpointError:
        return {"observation_error": "DaemonEndpointError"}
    if record is None:
        return None
    return {
        "pid": record.pid,
        "create_time": record.process_create_time,
        "shutdown_accepted": record.shutdown_accepted,
        "process": _process_snapshot(record.pid),
    }


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
                    "launcher": _process_snapshot(observed.process.pid),
                    "endpoint": _endpoint_snapshot(observed.root),
                }
                with observed.target.with_suffix(".jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(json.dumps(row) + "\n")
                log = (
                    load_runtime_binding(observed.root).coordination_root / "daemon.log"
                )
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
