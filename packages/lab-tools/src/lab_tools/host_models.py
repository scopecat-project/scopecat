"""Durable host operation contracts shared by teaching and deployments."""

from __future__ import annotations

import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    action: Literal[
        "open",
        "verify",
        "stop",
        "delete",
        "service_start",
        "service_stop",
        "service_remove",
    ]
    topic: str | None = None
    workspace: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    reset: bool = False
    service: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class Operation(BaseModel):
    command: Command
    status: Literal["starting", "running", "succeeded", "failed", "interrupted"]
    created: float = Field(default_factory=time.time)
    pid: int | None = None
    process_time: float | None = None
    detail: str = ""
    workspace: str | None = None
