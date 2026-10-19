"""Read the foreground launcher's live, instance-local development UI binding."""

from pathlib import Path
from urllib.parse import urlsplit

import psutil
from pydantic import BaseModel, ConfigDict, Field
from scopecat.daemon.endpoint import read_daemon_endpoint_record
from scopecat.runtime_binding import load_runtime_binding

RECORD_NAME = "development-ui.json"


class DevelopmentUI(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str
    ui: str
    owner_pid: int = Field(gt=0)
    owner_created: float
    frontend_pid: int = Field(gt=0)
    frontend_created: float


def current_ui(root: Path) -> str:
    """Reject stale owners and other backends instead of guessing a UI port."""
    path = load_runtime_binding(root).coordination_root / RECORD_NAME
    record = DevelopmentUI.model_validate_json(path.read_bytes())
    endpoint = read_daemon_endpoint_record(root)
    if endpoint is None or record.backend != endpoint.base_url:
        raise ValueError("Development UI belongs to another backend")
    for address in (record.backend, record.ui):
        url = urlsplit(address)
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.port is None
            or url.username is not None
            or url.password is not None
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid development origin")
    if record.ui.rstrip("/") == record.backend.rstrip("/"):
        raise ValueError("Development UI cannot redirect to itself")
    for pid, created in (
        (record.owner_pid, record.owner_created),
        (record.frontend_pid, record.frontend_created),
    ):
        process = psutil.Process(pid)
        if process.create_time() != created or process.status() == psutil.STATUS_ZOMBIE:
            raise ValueError("Development UI process is no longer available")
    return record.ui
