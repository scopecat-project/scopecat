"""Durable identities for complete project-local author source revisions."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.content import Sha256ContentHash


class AuthorRevisionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
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


class AuthorRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_generation: int = Field(ge=0)


class AuthorAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef
    run_id: str = Field(min_length=1)
    analysis: str = Field(
        min_length=1, description="Configured author module:analysis name"
    )
    key: str | None = None


class AuthorAnalysisReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef
    analysis_id: str
