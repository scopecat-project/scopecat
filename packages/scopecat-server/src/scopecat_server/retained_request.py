"""Typed internal requests for revision-isolated retained-data operations."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from scopecat.automation.wire import ProcedureSubmitCommand
from scopecat.records.analysis_actions import RunAnalysisActionsRequest
from scopecat.records.author_revision import AuthorAnalysisRequest, AuthorRevisionRef
from scopecat.records.comparison import ComparisonRequest


class AnalysisCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["analysis"] = "analysis"
    exit_call_id: str | None = None
    request: AuthorAnalysisRequest

    @property
    def code_revision(self) -> AuthorRevisionRef:
        return self.request.code_revision


class AnalysisActionsCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["analysis_actions"] = "analysis_actions"
    request: RunAnalysisActionsRequest

    @property
    def code_revision(self) -> AuthorRevisionRef:
        return self.request.code_revision


class ComparisonCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["comparison"] = "comparison"
    request: ComparisonRequest

    @property
    def code_revision(self) -> AuthorRevisionRef | None:
        return self.request.code_revision


class ProcedureValidationCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["procedure_validation"] = "procedure_validation"
    request: ProcedureSubmitCommand

    @property
    def code_revision(self) -> AuthorRevisionRef:
        if self.request.source is None:
            raise ValueError("managed procedures require retained source")
        return self.request.source.code_revision


type RetainedRequest = Annotated[
    AnalysisCall | AnalysisActionsCall | ComparisonCall | ProcedureValidationCall,
    Field(discriminator="kind"),
]
