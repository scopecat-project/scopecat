"""Named parameter history; branch heads are mutable, revisions are not."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.parameter_revision import ParameterRevisionRef


class ParameterBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    generation: int = Field(ge=1)
    revision: ParameterRevisionRef
    previous: ParameterRevisionRef | None = None
    actor: str = Field(min_length=1)
    note: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
