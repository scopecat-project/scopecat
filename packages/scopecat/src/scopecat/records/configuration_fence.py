"""Freshness of a launch selection, distinct from runtime resource ownership."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.content import Sha256ContentHash


class ActiveConfigurationFence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["active_generation"] = "active_generation"
    generation: int = Field(ge=1)


class SetupContentFence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["setup_content"] = "setup_content"
    content_hash: Sha256ContentHash


type ProcedureConfigurationFence = Annotated[
    ActiveConfigurationFence | SetupContentFence, Field(discriminator="kind")
]
