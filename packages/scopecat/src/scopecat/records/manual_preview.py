"""A checked preview's relevant manual-operation boundary, not hardware readback."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.content import Sha256ContentHash


class ManualPreviewBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_hash: Sha256ContentHash
    config_source_hash: Sha256ContentHash
    code_revision: AuthorRevisionRef | None = None


class ManualPreviewFence(BaseModel):
    """Reference to server-recorded preview facts; never a permanent run permission."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: int = Field(ge=1)
    binding: ManualPreviewBinding


class PreviewInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    exclusivity_key: str


class ManualPreviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observed_cursor: int = Field(ge=0)
    binding: ManualPreviewBinding
    instruments: tuple[PreviewInstrument, ...]


class ManualPreviewChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_ids: tuple[str, ...]
    action: str
    occurred_at: datetime
    reason: str


class ManualPreviewValidity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    valid: bool
    changes: tuple[ManualPreviewChange, ...] = ()
