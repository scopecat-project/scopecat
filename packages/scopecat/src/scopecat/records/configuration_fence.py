"""Exact execution context required by a scientific procedure."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from scopecat.records.setup import SetupRevisionRef


class SetupRevisionFence(BaseModel):
    """An immutable maintained context, independent of any client's defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["setup_revision"] = "setup_revision"
    revision: SetupRevisionRef


type ProcedureConfigurationFence = SetupRevisionFence
