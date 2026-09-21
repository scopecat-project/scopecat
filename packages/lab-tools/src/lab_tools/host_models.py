"""Durable host operation contracts shared by teaching and deployments."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["create", "connect", "adapter"]
    project: str
    data_root: str | None = None
    settings_file: str | None = None
    environment_bundle: str | None = None
    adapter_distribution: str | None = None
    adapter_manifest: str | None = None
    author_workspace: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator(
        "project",
        "data_root",
        "settings_file",
        "environment_bundle",
        "author_workspace",
    )
    @classmethod
    def absolute_path(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or not Path(value).is_absolute()):
            raise ValueError("请选择完整的绝对目录路径")
        return value

    @model_validator(mode="after")
    def adapter_selection(self) -> SetupRequest:
        from scopecat.installed_adapter import parse_adapter_reference

        if self.mode == "create" and self.author_workspace is not None:
            raise ValueError(
                "独立作者目录需接入安装式实验室；基础项目已有自己的作者代码"
            )
        if self.mode == "adapter":
            if self.environment_bundle is None:
                raise ValueError("安装式实验室需要选择完整交付目录")
            parse_adapter_reference(
                {
                    "distribution": self.adapter_distribution,
                    "manifest": self.adapter_manifest,
                }
            )
        elif self.adapter_distribution is not None or self.adapter_manifest is not None:
            raise ValueError("适配包声明只用于创建新的安装式实验室")
        return self


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
