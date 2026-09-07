"""Project-owned experiment discovery, preview and durable submission boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "preview", "submit"]
    experiment: str = ""
    request_key: str = ""
    sample: str | None = None
    actor: str = "operator"
    expected_config_hash: str | None = None
    expected_generation: int | None = None
    inputs: dict[str, JsonValue] = Field(default_factory=dict)


type LaunchProvider = Callable[[LabClient, LaunchRequest], dict[str, JsonValue]]


class LaunchSubmission(BaseModel):
    """A durable admission, with an independent best-effort worker wakeup."""

    procedure_id: str
    dispatch_error: str | None = None
