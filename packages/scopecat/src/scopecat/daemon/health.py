"""Lightweight daemon readiness contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DaemonHealth(BaseModel):
    """Daemon readiness and the one project owned by this process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok", "degraded"]
    project_id: str
    deployment_id: str
    project_name: str
    project_root: str
    data_root: str
    deployment_root: str


__all__ = ["DaemonHealth"]
