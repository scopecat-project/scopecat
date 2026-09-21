"""Durable host operation contracts shared by teaching and deployments."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["create", "connect"]
    project: str
    data_root: str | None = None
    settings_file: str | None = None
    environment_bundle: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("project", "data_root", "settings_file", "environment_bundle")
    @classmethod
    def absolute_path(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or not Path(value).is_absolute()):
            raise ValueError("请选择完整的绝对目录路径")
        return value


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    action: Literal[
        "setup",
        "open",
        "verify",
        "stop",
        "delete",
        "service_start",
        "service_stop",
        "service_remove",
        "service_recheck",
        "service_update",
    ]
    environment_bundle: str | None = None

    @field_validator("environment_bundle")
    @classmethod
    def delivery_path(cls, value: str | None) -> str | None:
        return SetupRequest.absolute_path(value)

    setup: SetupRequest | None = None
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
    service: str | None = None
