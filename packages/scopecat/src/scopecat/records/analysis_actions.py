"""Read-only description of explicitly declared, retained-source run analyses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.author_workspace import AuthorWorkspaceId


class RunAnalysisActionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(min_length=1)
    workspace_id: AuthorWorkspaceId
    code_revision: AuthorRevisionRef


class AnalysisActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    kind: Literal["number", "integer", "text", "boolean", "quantity", "choice"]
    required: bool
    default: JsonValue = None
    choices: tuple[str, ...] = ()


class RunAnalysisAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str
    analysis: str
    description: str
    inputs: tuple[AnalysisActionInput, ...] = ()
    unavailable_reason: str | None = None


class RunAnalysisActions(RunAnalysisActionsRequest):
    experiment: str
    actions: tuple[RunAnalysisAction, ...] = ()
