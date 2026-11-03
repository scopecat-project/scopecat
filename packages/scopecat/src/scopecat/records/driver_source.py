"""An explicitly selected source snapshot for the application's driver worker."""

from typing import Literal

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


class DriverSourceCommand(BaseModel):
    """One compare-and-swap operation on the local source collection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str = Field(min_length=1)
    expected_previous: str | None = None
    source_id: str = Field(min_length=1)
    action: Literal["capture", "enable", "disable"] = "capture"
    source_root: str | None = None
    builtin: Literal["public", "simulation"] | None = None
    actor: str = Field(min_length=1)


class LocalDriverSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    enabled: bool = True
    builtin: Literal["public", "simulation"] | None = None
    selection: DriverSourceSelection
    driver_ids: tuple[str, ...]
    # Old single-source references retain their exact implementation identity.
    legacy_identity: bool = False


class DriverSourceCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str | None = None
    provider_id: str = "scopecat.local-sources"
    sources: tuple[LocalDriverSource, ...] = ()
    request: DriverSourceCommand | None = None


class LocalDriverSourceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    collection: DriverSourceCollection
    errors: dict[str, str] = Field(default_factory=dict)
    unmanaged_backend: bool = False
