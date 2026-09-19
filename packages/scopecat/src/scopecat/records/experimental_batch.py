"""Stable physical-event scope, independent of sample and record collection."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

ExperimentalBatchId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]


def absent_batch(value: object) -> bool:
    return value is None


def require_batch_match(selected: str | None, actual: str | None) -> None:
    if selected is not None and selected != actual:
        raise ValueError(
            "selected batch does not match the working point or retained evidence; "
            "create a working point for this batch or explicitly "
            "select the original batch"
        )


class ExperimentalBatchEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    expected_revision: int = Field(default=0, ge=0)


class ExperimentalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: ExperimentalBatchId
    name: str
    description: str
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class ExperimentalBatchPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[ExperimentalBatch, ...] = ()
    next_cursor: int | None = None
