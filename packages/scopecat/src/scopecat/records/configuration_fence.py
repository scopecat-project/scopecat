"""Freshness of a launch selection, distinct from runtime resource ownership."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.content import Sha256ContentHash
from scopecat.records.setup import SetupRevisionRef


class ActiveConfigurationFence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["active_generation"] = "active_generation"
    generation: int = Field(ge=1)


class SetupContentFence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["setup_content"] = "setup_content"
    content_hash: Sha256ContentHash


class SetupRevisionFence(BaseModel):
    """An immutable maintained context, independent of any client's defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["setup_revision"] = "setup_revision"
    revision: SetupRevisionRef


type ProcedureConfigurationFence = Annotated[
    ActiveConfigurationFence | SetupContentFence | SetupRevisionFence,
    Field(discriminator="kind"),
]
