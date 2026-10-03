"""Explicit scientific record selection, independent of tutorial ownership."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataCleanupSelection(BaseModel):
    """Exact records requested for deletion, never an implicit dependency cascade.

    A lifecycle service must fence writers, settle workers and validate retained
    scientific references before passing this selection to the storage layer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    runs: tuple[str, ...] = ()
    analyses: tuple[str, ...] = ()
    procedures: tuple[str, ...] = ()
    setups: tuple[str, ...] = ()
    setup_definitions: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    captures: tuple[str, ...] = ()

    @field_validator("*")
    @classmethod
    def unique_selection(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(values)))


class DataCleanupBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: str
    reason: str


class DataCleanupPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection: DataCleanupSelection
    fingerprint: str
    blockers: tuple[DataCleanupBlocker, ...]
    record_count: int
    bytes_to_reclaim: int


class DataCleanupCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_key: str = Field(min_length=1, max_length=200)
    preview: DataCleanupPreview


class DataCleanupOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    selection: DataCleanupSelection
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: Literal["prepared", "records_removed", "complete"] = "prepared"
    error: str | None = None
