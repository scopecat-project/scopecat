"""Acquisition address namespaces, independent of mutable research grouping."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

RecordCollectionId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]


class RecordCollectionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    expected_revision: int = Field(default=0, ge=0)


class RecordCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: RecordCollectionId
    name: str
    description: str
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    is_default: bool


class RecordCollectionPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[RecordCollection, ...] = ()
    next_cursor: int | None = None


class RunAddress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    collection_id: RecordCollectionId
    number: int = Field(ge=1)
    run_id: str
