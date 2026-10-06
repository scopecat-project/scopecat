"""Application editing records, deliberately outside scientific decision models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionDraftTarget(DraftModel):
    procedure_run_id: str = Field(min_length=1, max_length=256)
    step_key: str = Field(min_length=1, max_length=1024)
    attempt: int = Field(ge=1)


class DecisionDraftBaseline(DraftModel):
    run_revision: int = Field(ge=1)
    step_revision: int = Field(ge=1)
    request_hash: str = Field(min_length=1, max_length=128)


class DecisionDraftInput(DraftModel):
    actor: str = Field(max_length=4096)
    actor_kind: Literal["human", "ai", "service"]
    note: str = Field(max_length=262144)
    value_text: str = Field(max_length=1048576)
    use_json: bool


class DecisionDraftSave(DraftModel):
    target: DecisionDraftTarget
    baseline: DecisionDraftBaseline
    expected_revision: int = Field(ge=0)
    input: DecisionDraftInput
    discard: bool = False


class DecisionDraft(DraftModel):
    revision: int
    target: DecisionDraftTarget
    baseline: DecisionDraftBaseline
    input: DecisionDraftInput
    state: Literal["saved", "conflict", "discarded"]
    created_at: datetime


class DecisionDraftView(DraftModel):
    draft: DecisionDraft | None
    head_revision: int
    validity: Literal["current", "baseline_changed", "no_longer_waiting"]


class DecisionDraftPage(DraftModel):
    items: list[DecisionDraft]
    next_cursor: int | None = None
