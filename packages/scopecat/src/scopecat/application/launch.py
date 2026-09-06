"""Project-owned, read-only experiment discovery and preview boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "preview"]
    experiment: str = ""
    inputs: dict[str, JsonValue] = Field(default_factory=dict)


type LaunchProvider = Callable[[LabClient, LaunchRequest], dict[str, JsonValue]]
