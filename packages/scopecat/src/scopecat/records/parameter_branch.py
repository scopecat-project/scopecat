"""Named parameter history; branch heads are mutable, revisions are not."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.analysis import ProjectAnalysisDecisionReference
from scopecat.records.parameter_revision import ParameterRevisionRef


class ParameterBranchPublication(BaseModel):
    """Exact evidence for one accepted candidate, not blanket branch validity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    verification: ProjectAnalysisDecisionReference


class ParameterBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    generation: int = Field(ge=1)
    revision: ParameterRevisionRef
    previous: ParameterRevisionRef | None = None
    actor: str = Field(min_length=1)
    note: str = ""
    publication: ParameterBranchPublication | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
