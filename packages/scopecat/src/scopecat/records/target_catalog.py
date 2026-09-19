"""Catalog-qualified immutable target revisions and explicit mutation commands."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.content import Sha256ContentHash
from scopecat.records.scientific_scope import MeasurementTarget

TargetId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]


class _TargetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetRevisionRef(_TargetModel):
    catalog_id: str = Field(min_length=1)
    target_id: TargetId
    revision: int = Field(ge=1)
    content_hash: Sha256ContentHash


class TargetRevisionDraft(_TargetModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    content: MeasurementTarget
    actor: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class TargetCreateCommand(_TargetModel):
    catalog_id: str = Field(min_length=1)
    target_id: TargetId
    draft: TargetRevisionDraft


class TargetReviseCommand(_TargetModel):
    expected: TargetRevisionRef
    draft: TargetRevisionDraft


class TargetRevision(TargetRevisionDraft):
    ref: TargetRevisionRef
    recorded_at: datetime

    @model_validator(mode="after")
    def consistent_hash(self) -> TargetRevision:
        if self.ref.content_hash != self.content.content_hash:
            raise ValueError("target revision content hash is inconsistent")
        return self


class TargetCatalogPage(_TargetModel):
    items: tuple[TargetRevision, ...] = ()
    next_cursor: int | None = None
