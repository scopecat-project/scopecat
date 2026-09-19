"""Store-local source ownership, separate from immutable content identity."""

from typing import Annotated

from pydantic import Field

AuthorWorkspaceId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
]


SERVICE_AUTHOR_WORKSPACE = "legacy"
