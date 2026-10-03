"""An explicitly selected source snapshot for the application's driver worker."""

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.content import Sha256ContentHash


class DriverSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    python: str | None = None
    expected_previous: str | None = None
    actor: str = Field(min_length=1)


class DriverSourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: DriverSourceUpdate
    python: str
    code_revision: AuthorRevisionRef
    factory: str
    artifact_hash: Sha256ContentHash


class DriverSourceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    active: DriverSourceSelection | None = None
