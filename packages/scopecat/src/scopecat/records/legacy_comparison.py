"""Read-only v1 comparison-request evidence, before source-owner qualification."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.comparison import ComparisonSelection


class LegacyComparisonRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["list", "inspect", "fit", "candidate", "reject", "handoff"]
    code_revision: AuthorRevisionRef | None = None
    model_id: str = ""
    model_version: str = ""
    primary_run: str = ""
    secondary_run: str = ""
    primary: ComparisonSelection | None = None
    secondary: ComparisonSelection | None = None
    parameters: dict[str, FiniteFloat] = Field(default_factory=dict)
    analysis_id: str = ""
    analysis_hash: str = ""
    actor: str = Field(default="operator", min_length=1)
    reason: str = ""
