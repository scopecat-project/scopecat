"""Recoverable launch rejection; successful worker replies retain their schema."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LaunchRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["launch_rejection"] = "launch_rejection"
    detail: str
