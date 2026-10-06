"""Raw parameter editor records; saving these never validates scientific input."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.scientific_selection import ParameterConfiguration


class ParameterDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParameterDraftAtom(ParameterDraftModel):
    text: str = Field(max_length=262144)
    unit: str = Field(default="", max_length=4096)


class ParameterDraftValue(ParameterDraftModel):
    id: str = Field(max_length=4096)
    shape: Literal["scalar", "table"]
    value: ParameterDraftAtom | None = None
    rows: list[dict[str, ParameterDraftAtom]] = Field(
        default_factory=list, max_length=10000
    )


class ParameterDraftInput(ParameterDraftModel):
    name: str = Field(max_length=4096)
    actor: str = Field(max_length=4096)
    note: str = Field(default="", max_length=262144)
    branch: str = Field(default="", max_length=4096)
    branch_generation: int | None = Field(default=None, ge=0)
    values: list[ParameterDraftValue] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def bound_editor_size(self) -> Self:
        if len(self.model_dump_json().encode("utf-8")) > 1048576:
            raise ValueError("Parameter working input is limited to 1 MiB")
        return self


class ParameterDraftStart(ParameterDraftModel):
    draft_id: UUID
    base: ParameterRevisionRef
    actor: str = Field(max_length=4096)
    copy_from: UUID | None = None
    copy_revision: int | None = Field(default=None, ge=1)
    working_branch: str = Field(default="", max_length=4096)
    branch_generation: int | None = Field(default=None, ge=0)


class ParameterDraftSave(ParameterDraftModel):
    expected_revision: int = Field(ge=1)
    input: ParameterDraftInput
    discard: bool = False


class ParameterDraftCommit(ParameterDraftModel):
    expected_revision: int = Field(ge=1)


class ParameterDraft(ParameterDraftModel):
    draft_id: UUID
    revision: int
    working_branch: str = ""
    base: ParameterRevisionRef
    input: ParameterDraftInput
    state: Literal["saved", "conflict", "discarded", "completed"]
    created_at: datetime
    completed_from: int | None = None
    result: ParameterRevisionRef | None = None


class ParameterDraftView(ParameterDraftModel):
    draft: ParameterDraft
    head_revision: int
    branch_changed: bool


class ParameterDraftPage(ParameterDraftModel):
    items: list[ParameterDraft]
    next_cursor: int | None = None


class ParameterDraftFrozen(ParameterDraftModel):
    draft_id: UUID
    revision: int
    configuration: ParameterConfiguration
