"""Durable, bounded analysis of complete groups from an ongoing acquisition."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.author_revision import (
    AuthorAnalysisGroupReceipt,
    AuthorAnalysisRequest,
)

type AnalysisFollowState = Literal["running", "completed", "attention", "stopped"]


class AnalysisFollowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    analysis: AuthorAnalysisRequest
    max_groups: int = Field(default=1000, ge=1, le=10000)
    max_points_per_group: int = Field(default=4096, ge=1, le=65536)
    max_input_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    timeout_seconds: float = Field(default=60, gt=0, le=600)

    @model_validator(mode="after")
    def require_grouping(self) -> Self:
        if (
            self.analysis.grouping is None
            or self.analysis.measurement_slice is not None
        ):
            raise ValueError(
                "follow requires grouping and selects its own immutable inputs"
            )
        return self


class AnalysisFollowView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request: AnalysisFollowRequest
    state: AnalysisFollowState
    group_count: int | None = None
    finished_count: int = 0
    failed_count: int = 0
    active_group: int | None = None
    error: str | None = None


class AnalysisFollowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cursor: int
    group_index: int
    state: Literal["running", "succeeded", "failed", "incomplete", "uncertain"]
    measurement_slice: str | None = None
    receipt: AuthorAnalysisGroupReceipt | None = None
    error: str | None = None


class AnalysisFollowPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    follow: AnalysisFollowView
    events: tuple[AnalysisFollowEvent, ...]
    next_cursor: int
    has_more: bool = False
