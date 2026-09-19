"""Durable identities for complete project-local author source revisions."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.analysis_grouping import AnalysisGrouping
from scopecat.records.author_workspace import (
    SERVICE_AUTHOR_WORKSPACE,
    AuthorWorkspaceId,
)
from scopecat.records.content import Sha256ContentHash


class AuthorRevisionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content_hash: Sha256ContentHash


class InstalledAuthorPackage(BaseModel):
    """Installed module tree identity; deployment must preserve these exact bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    distribution: str
    version: str
    content_hash: Sha256ContentHash


class AuthorRevisionManifest(BaseModel):
    """Local source closure plus the external environment required for recovery.

    Installed distributions are identified, not archived. Recovery requires this
    same environment; a manifest is not a hermetic environment image.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    files: dict[str, Sha256ContentHash]
    source_roots: tuple[str, ...]
    refresh_roots: tuple[str, ...]
    python: str
    packages: dict[str, str]
    installed_authors: dict[str, InstalledAuthorPackage] = Field(default_factory=dict)
    maintenance_hash: Sha256ContentHash

    @field_validator("files")
    @classmethod
    def local_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or PureWindowsPath(name).drive
                or ".." in path.parts
                or "\\" in name
                or path.as_posix() != name
                or not path.parts
            ):
                raise ValueError(f"invalid source path: {name}")
        return value

    @property
    def ref(self) -> AuthorRevisionRef:
        return AuthorRevisionRef(
            content_hash=sha256_json_hash(self.model_dump(mode="json"))
        )


class AuthorRevisionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest: AuthorRevisionManifest
    files: dict[str, str]  # base64 encoded bytes; arbitrary local data files retained


class AuthorRevisionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    generation: int = Field(default=0, ge=0)
    active: AuthorRevisionRef | None = None
    preparation_id: str | None = None


class AuthorPreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_generation: int = Field(ge=0)


class AuthorPreparation(BaseModel):
    """Persisted validation outcome; observing it never captures new source."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    expected_generation: int
    code_revision: AuthorRevisionRef
    status: Literal[
        "queued",
        "running",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    ]
    phase: str
    created_at: datetime
    updated_at: datetime
    result: AuthorRevisionState | None = None
    error: str | None = None
    error_type: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "cancelled", "interrupted"}


class AuthorAnalysisRequest(BaseModel):
    workspace_id: AuthorWorkspaceId = SERVICE_AUTHOR_WORKSPACE
    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef
    run_id: str = Field(min_length=1)
    analysis: str = Field(
        min_length=1, description="Configured author module:analysis name"
    )
    key: str | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    grouping: AnalysisGrouping | None = None


class AuthorAnalysisGroupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    coordinates: dict[str, JsonValue]
    point_indices: tuple[int, ...]
    analysis_id: str
    error: str | None = None


class AuthorAnalysisReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef
    analysis_id: str
    groups: tuple[AuthorAnalysisGroupReceipt, ...] = ()
