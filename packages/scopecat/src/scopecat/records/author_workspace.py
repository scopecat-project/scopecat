"""Store-local source ownership, separate from immutable content identity."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

AuthorWorkspaceId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
]


SERVICE_AUTHOR_WORKSPACE = "legacy"


class AuthorWorkspaceSummary(BaseModel):
    """Retained source identity and its current local execution availability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: AuthorWorkspaceId
    name: str
    available: bool
    unavailable_reason: str | None = None


class AuthorWorkspaceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[AuthorWorkspaceSummary, ...] = ()
