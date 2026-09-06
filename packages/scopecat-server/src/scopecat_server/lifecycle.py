"""Project-scoped daemon process lifecycle."""

from __future__ import annotations

import math
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import httpx2
import psutil
from pydantic import ValidationError
from scopecat.daemon.endpoint import (
    DAEMON_SHUTDOWN_PATH,
    DAEMON_SHUTDOWN_TOKEN_HEADER,
    DaemonEndpointError,
    DaemonEndpointRecord,
    daemon_record_path,
    read_daemon_endpoint_record,
)
from scopecat.daemon.health import DaemonHealth
from scopecat.project import Project, open_project

from .scaffold import scaffold_paths, write_project_scaffold

type DaemonState = Literal["running", "stopped", "stale", "degraded"]

_HEALTH_PATH = "/api/v1/health"
_PROCESS_TIME_TOLERANCE_SECONDS = 0.01


class DaemonLifecycleError(RuntimeError):
    """A requested daemon lifecycle transition cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class DaemonStatus:
    """Observed process and health state for one project."""

    state: DaemonState
    record: DaemonEndpointRecord | None = None
    detail: str | None = None


def initialize_project(target: str | Path) -> Project:
    """Create a runnable source-controlled project and ignore daemon state."""

    root = Path(target).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "scopecat.toml"
    if manifest.exists():
        raise DaemonLifecycleError(f"project already initialized: {manifest}")
    collisions = tuple(path for path in scaffold_paths(root) if path.exists())
    if collisions:
        relative_paths = ", ".join(
            path.relative_to(root).as_posix() for path in collisions
        )
        raise DaemonLifecycleError(
            f"project scaffold path already exists: {relative_paths}"
        )
    write_project_scaffold(root)

    ignore = root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if ".scopecat/" not in existing.splitlines():
        separator = "" if not existing or existing.endswith("\n") else "\n"
        ignore.write_text(f"{existing}{separator}.scopecat/\n", encoding="utf-8")
    return open_project(root)


def inspect_daemon(project: Project, *, health_timeout: float = 0.5) -> DaemonStatus:
    """Inspect endpoint identity first, then the daemon's HTTP health."""

    try:
        record = read_daemon_endpoint_record(project.root)
    except DaemonEndpointError as error:
        return DaemonStatus(state="stale", detail=str(error))
    if record is None:
        return DaemonStatus(state="stopped")
    if record.project_root.resolve() != project.root:
        return DaemonStatus(
            state="stale",
            record=record,
            detail=f"record belongs to {record.project_root}",
        )

    try:
        process = _matching_process(record)
    except DaemonLifecycleError as error:
        return DaemonStatus(state="degraded", record=record, detail=str(error))
    if process is None:
        return DaemonStatus(
            state="stale",
            record=record,
            detail="recorded process no longer matches its identity",
        )

    try:
        health = _read_health(record.base_url, timeout=health_timeout)
    except (httpx2.HTTPError, ValueError, ValidationError) as error:
        return DaemonStatus(
            state="degraded",
            record=record,
            detail=f"health check failed: {error}",
        )
    if health.status == "degraded":
        return DaemonStatus(
            state="degraded",
            record=record,
            detail="daemon reported degraded health",
        )
    return DaemonStatus(state="running", record=record)


def serve_project(
    project: Project,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    static_dir: str | Path | None = None,
    lease_ttl: timedelta | None = None,
) -> None:
    """Serve one project in the foreground and publish its actual endpoint."""

    import uvicorn

    from .runtime import LocalDaemonRuntime

    status = inspect_daemon(project)
    if status.state in {"running", "degraded"}:
        raise DaemonLifecycleError(
            f"project daemon is already {status.state}: {project.root}"
        )
    if status.state == "stale":
        _remove_record_if_stale(project)

    listener = _bind_listener(host, port)
    actual_port = cast("tuple[str, int]", listener.getsockname())[1]
    runtime: LocalDaemonRuntime | None = None
    record: DaemonEndpointRecord | None = None
    try:
        runtime = LocalDaemonRuntime(
            project.root,
            bootstrap_spec=project.bootstrap_spec,
            instrument_backend_spec=project.instrument_backend_spec,
            lease_ttl=lease_ttl,
        )
        shutdown_token = secrets.token_urlsafe(32)
        record = DaemonEndpointRecord(
            project_root=project.root,
            pid=os.getpid(),
            process_create_time=psutil.Process().create_time(),
            base_url=_base_url(host, actual_port),
            shutdown_token=shutdown_token,
            started_at=datetime.now(UTC),
        )
        write_daemon_endpoint_record(record)
        server: uvicorn.Server

        def request_shutdown(token: str) -> bool:
            if not secrets.compare_digest(token, shutdown_token):
                return False
            server.should_exit = True
            return True

        server = uvicorn.Server(
            uvicorn.Config(
                runtime.app(
                    static_dir=static_dir,
                    request_shutdown=request_shutdown,
                ),
                host=host,
                port=actual_port,
                access_log=False,
                lifespan="on",
            )
        )
        server.run(sockets=[listener])
    finally:
        if record is not None:
            _remove_owned_record(record)
        if runtime is not None:
            runtime.close()
        listener.close()


def start_project(
    project: Project,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    timeout: float = 10.0,
    static_dir: str | Path | None = None,
    lease_ttl: timedelta | None = None,
) -> DaemonEndpointRecord:
    """Start a detached daemon with the current Python interpreter."""

    status = inspect_daemon(project)
    if status.state == "running" and status.record is not None:
        return status.record
    if status.state == "degraded":
        raise DaemonLifecycleError(
            f"project daemon process exists but is degraded: {status.detail}"
        )
    if status.state == "stale":
        _remove_record_if_stale(project)

    state_dir = project.root / ".scopecat"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    log_path = state_dir / "daemon.log"
    log_fd = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    command = [
        sys.executable,
        "-m",
        "scopecat_server.cli",
        "serve",
        str(project.root),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if lease_ttl is not None:
        command.extend(
            (
                "--executor-lease-ttl-seconds",
                str(lease_ttl.total_seconds()),
            )
        )
    if static_dir is None:
        command.append("--api-only")
    else:
        command.extend(("--static-dir", str(Path(static_dir).resolve())))
    with os.fdopen(log_fd, "a", encoding="utf-8") as log:
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter/module command
            command,
            cwd=project.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DaemonLifecycleError(
                _startup_failure_message(project, process.returncode)
            )
        observed = inspect_daemon(project)
        if (
            observed.state == "running"
            and observed.record is not None
            and _spawned_process_owns_record(process, observed.record)
        ):
            return observed.record
        time.sleep(0.05)

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    _remove_record_if_stale(project)
    raise DaemonLifecycleError(
        f"daemon did not become healthy within {timeout:g} seconds; see {log_path}"
    )


def stop_project(project: Project, *, timeout: float = 10.0) -> DaemonStatus:
    """Stop only the process whose PID and creation time match the record."""

    status = inspect_daemon(project)
    if status.state == "stopped":
        return status
    if status.state == "stale":
        _remove_record_if_stale(project)
        return status
    if status.record is None:
        raise DaemonLifecycleError("daemon status has no process identity")

    process = _matching_process(status.record)
    if process is None:
        _remove_record_if_stale(project)
        return DaemonStatus(
            state="stale",
            record=status.record,
            detail="recorded process exited before it could be stopped",
        )
    _request_graceful_shutdown(status.record)
    try:
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    _remove_owned_record(status.record)
    return status


def open_project_gui(project: Project) -> str:
    """Open the recorded daemon GUI in the system browser."""

    status = inspect_daemon(project)
    if status.state not in {"running", "degraded"} or status.record is None:
        raise DaemonLifecycleError(
            f"project daemon is {status.state}; start it before opening the GUI"
        )
    if not webbrowser.open(status.record.base_url):
        raise DaemonLifecycleError("the system browser could not be opened")
    return status.record.base_url


def write_daemon_endpoint_record(record: DaemonEndpointRecord) -> Path:
    """Atomically publish one private endpoint record."""

    state_dir = record.project_root.resolve() / ".scopecat"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    destination = daemon_record_path(record.project_root)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=state_dir,
        prefix=".daemon-",
        suffix=".json",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(record.model_dump_json(indent=2))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _matching_process(record: DaemonEndpointRecord) -> psutil.Process | None:
    try:
        process = psutil.Process(record.pid)
        create_time = process.create_time()
    except psutil.NoSuchProcess, psutil.ZombieProcess:
        return None
    except psutil.AccessDenied as error:
        raise DaemonLifecycleError(
            f"cannot verify process identity for pid {record.pid}"
        ) from error
    if not math.isclose(
        create_time,
        record.process_create_time,
        rel_tol=0,
        abs_tol=_PROCESS_TIME_TOLERANCE_SECONDS,
    ):
        return None
    return process


def _spawned_process_owns_record(
    process: subprocess.Popen[bytes],
    record: DaemonEndpointRecord,
) -> bool:
    if record.pid == process.pid:
        return True
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.AccessDenied, psutil.NoSuchProcess:
        return False
    return any(child.pid == record.pid for child in children)


def _read_health(base_url: str, *, timeout: float) -> DaemonHealth:
    with httpx2.Client(timeout=timeout, trust_env=False) as client:
        response = client.get(f"{base_url.rstrip('/')}{_HEALTH_PATH}")
        response.raise_for_status()
        return DaemonHealth.model_validate(response.json())


def _request_graceful_shutdown(record: DaemonEndpointRecord) -> None:
    try:
        with httpx2.Client(timeout=2, trust_env=False) as client:
            response = client.post(
                f"{record.base_url.rstrip('/')}{DAEMON_SHUTDOWN_PATH}",
                headers={DAEMON_SHUTDOWN_TOKEN_HEADER: record.shutdown_token},
            )
            response.raise_for_status()
    except httpx2.HTTPError:
        process = _matching_process(record)
        if process is not None:
            process.terminate()


def _bind_listener(host: str, port: int) -> socket.socket:
    bind_host = "127.0.0.1" if host == "localhost" else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bind_host, port))
        listener.listen(socket.SOMAXCONN)
    except BaseException:
        listener.close()
        raise
    return listener


def _base_url(host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}"


def _remove_record_if_stale(project: Project) -> None:
    try:
        current = read_daemon_endpoint_record(project.root)
    except DaemonEndpointError:
        daemon_record_path(project.root).unlink(missing_ok=True)
        return
    if current is None:
        return
    if current.project_root.resolve() != project.root:
        daemon_record_path(project.root).unlink(missing_ok=True)
        return
    try:
        process = _matching_process(current)
    except DaemonLifecycleError:
        return
    if process is None:
        daemon_record_path(project.root).unlink(missing_ok=True)


def _remove_owned_record(owner: DaemonEndpointRecord) -> None:
    try:
        current = read_daemon_endpoint_record(owner.project_root)
    except DaemonEndpointError:
        return
    if (
        current is not None
        and current.pid == owner.pid
        and math.isclose(
            current.process_create_time,
            owner.process_create_time,
            rel_tol=0,
            abs_tol=_PROCESS_TIME_TOLERANCE_SECONDS,
        )
        and current.project_root.resolve() == owner.project_root.resolve()
    ):
        daemon_record_path(owner.project_root).unlink(missing_ok=True)


def _startup_failure_message(project: Project, return_code: int | None) -> str:
    log_path = project.root / ".scopecat" / "daemon.log"
    try:
        tail = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-10:])
    except OSError:
        tail = ""
    detail = f"\n{tail}" if tail else ""
    return f"daemon exited with status {return_code}; see {log_path}{detail}"


__all__ = [
    "DaemonLifecycleError",
    "DaemonState",
    "DaemonStatus",
    "initialize_project",
    "inspect_daemon",
    "open_project_gui",
    "serve_project",
    "start_project",
    "stop_project",
    "write_daemon_endpoint_record",
]
