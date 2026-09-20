"""Retained provenance and declared coverage of a software execution scene."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class SoftwareExecutionScenario(BaseModel):
    """A software model, not a promise of physical accuracy or OS sandboxing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["software"] = "software"
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    seed: int | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    capabilities: tuple[str, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()
