"""Discovery record for the daemon currently owning one project."""

from __future__ import annotations

from datetime import datetime
from os import environ
from pathlib import Path

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scopecat.daemon.health import DaemonHealth
from scopecat.runtime_binding import load_runtime_binding

DAEMON_URL_ENV = "SCOPECAT_DAEMON_URL"
DAEMON_RECORD_NAME = "daemon.json"
DAEMON_SHUTDOWN_PATH = "/api/v1/shutdown"
DAEMON_SHUTDOWN_TOKEN_HEADER = "X-Scopecat-Shutdown-Token"  # noqa: S105


class DaemonEndpointError(RuntimeError):
    """A project has no usable daemon endpoint record."""


class DaemonEndpointRecord(BaseModel):
    """Identity and endpoint of the process currently serving one project."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_root: Path
    data_root: Path
    deployment_root: Path
    pid: int = Field(gt=0)
    process_create_time: float = Field(gt=0)
    base_url: str = Field(min_length=1)
    shutdown_token: str = Field(min_length=32)
    started_at: datetime


def daemon_record_path(project_root: str | Path) -> Path:
    """Return the single dynamic endpoint record for ``project_root``."""

    return load_runtime_binding(project_root).data_root / DAEMON_RECORD_NAME


def read_daemon_endpoint_record(
    project_root: str | Path,
) -> DaemonEndpointRecord | None:
    """Read a project's endpoint record without guessing a fallback address."""

    path = daemon_record_path(project_root)
    try:
        return DaemonEndpointRecord.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return None
    except (OSError, ValidationError) as error:
        raise DaemonEndpointError(
            f"cannot read daemon record {path}: {error}"
        ) from error


def resolve_daemon_endpoint(
    project_root: str | Path,
    *,
    explicit: str | None = None,
) -> str:
    """Resolve explicit, environment, then project-record endpoint priority."""

    root = Path(project_root).resolve()
    binding = load_runtime_binding(root)
    endpoint = explicit or environ.get(DAEMON_URL_ENV)
    if endpoint is not None:
        verify_daemon_binding(endpoint, root)
        return endpoint

    record = read_daemon_endpoint_record(root)
    if record is None:
        raise DaemonEndpointError(
            f"no daemon endpoint for {root}; start it with 'scopecat start'"
        )
    if (
        record.project_root.resolve() != root
        or record.data_root.resolve() != binding.data_root
        or record.deployment_root.resolve() != binding.deployment_root
    ):
        raise DaemonEndpointError(
            f"daemon record belongs to another project binding: {record.project_root}"
        )
    verify_daemon_binding(record.base_url, root)
    return record.base_url


def verify_daemon_binding(endpoint: str, root: Path) -> None:
    """Check explicit endpoint overrides against the local workspace binding."""
    binding = load_runtime_binding(root)
    try:
        with httpx2.Client(timeout=5, trust_env=False) as client:
            response = client.get(f"{endpoint.rstrip('/')}/api/v1/health")
            response.raise_for_status()
            health = DaemonHealth.model_validate(response.json())
        if (
            Path(health.project_root).resolve() != root
            or Path(health.data_root).resolve() != binding.data_root
            or Path(health.deployment_root).resolve() != binding.deployment_root
        ):
            raise DaemonEndpointError("daemon endpoint belongs to another binding")
    except (httpx2.HTTPError, ValidationError) as error:
        raise DaemonEndpointError(f"cannot verify daemon binding: {error}") from error


__all__ = [
    "DAEMON_RECORD_NAME",
    "DAEMON_SHUTDOWN_PATH",
    "DAEMON_SHUTDOWN_TOKEN_HEADER",
    "DAEMON_URL_ENV",
    "DaemonEndpointError",
    "DaemonEndpointRecord",
    "daemon_record_path",
    "read_daemon_endpoint_record",
    "resolve_daemon_endpoint",
]
