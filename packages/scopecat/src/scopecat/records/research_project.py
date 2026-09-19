"""Mutable research organization around immutable laboratory evidence."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchProjectEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    expected_revision: int = Field(default=0, ge=0)


class ResearchProject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    description: str
    revision: int
    created_at: datetime
    updated_at: datetime


class ResearchProjectPage(BaseModel):
    items: tuple[ResearchProject, ...] = ()
    next_cursor: int | None = None


class ResearchMemberPage(BaseModel):
    ids: tuple[str, ...] = ()
    next_cursor: str | None = None


class RunHistoryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_collection: str | None = None
    batch_id: str | None = None
    research_project: str | None = None
    working_point: str | None = None
    deployment_id: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class ResearchMember(BaseModel):
    project_id: str
    kind: Literal["samples", "runs"]
    identity: str
    present: bool
