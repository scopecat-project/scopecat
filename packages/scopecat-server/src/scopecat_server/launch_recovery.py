"""Application-owned raw experiment edits and immutable pre-submit receipts."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from scopecat.application.launch import LaunchCatalogEntry
from scopecat.automation import ProcedureDefinitionRef
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.author_workspace import AuthorWorkspaceId
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.scientific_selection import ScientificSelection


class RecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LaunchDraftTarget(RecoveryModel):
    workspace_id: AuthorWorkspaceId
    experiment: str = Field(min_length=1, max_length=4096)


class RawControl(RecoveryModel):
    mode: Literal["default", "fixed", "values", "range"]
    value: str = ""
    values: str = ""
    start: str = ""
    stop: str = ""
    points: str = ""
    unit: str | None = None


class LaunchDraftInput(RecoveryModel):
    declaration: LaunchCatalogEntry
    code_revision: AuthorRevisionRef | None = None
    pinned_source: bool = False
    values: dict[str, str]
    controls: dict[str, RawControl]
    selection: ScientificSelection
    actor: str
    collection: str | None = None
    working_input: dict[str, JsonValue] | None = None
    plan: dict[str, JsonValue] | None = None
    plan_dirty: bool = False
    handoff: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def bound_input(self) -> Self:
        if len(self.model_dump_json().encode()) > 1048576:
            raise ValueError("Experiment editing input is limited to 1 MiB")
        return self


class LaunchDraftSave(RecoveryModel):
    operation_id: UUID
    target: LaunchDraftTarget
    expected_revision: int = Field(ge=0)
    input: LaunchDraftInput
    discard: bool = False

    @model_validator(mode="after")
    def same_target(self) -> Self:
        if self.target.experiment != self.input.declaration.id:
            raise ValueError("Draft declaration belongs to another experiment")
        return self


class LaunchDraftRecord(RecoveryModel):
    revision: int
    target: LaunchDraftTarget
    input: LaunchDraftInput
    state: Literal["saved", "conflict", "discarded"]
    created_at: datetime


class LaunchDraftView(RecoveryModel):
    head: LaunchDraftRecord | None = None
    saved: LaunchDraftRecord | None = None


class LaunchDraftPage(RecoveryModel):
    items: list[LaunchDraftRecord]
    next_cursor: int | None = None


class LaunchAttemptSave(RecoveryModel):
    definition: ProcedureDefinitionRef
    request: LaunchRequest

    @model_validator(mode="after")
    def submitted_intent(self) -> Self:
        if self.request.action != "submit":
            raise ValueError("Retain only explicit submission requests")
        if len(self.model_dump_json().encode()) > 1048576:
            raise ValueError("Original submission is limited to 1 MiB")
        return self


class LaunchAttemptRecord(LaunchAttemptSave):
    sequence: int
    created_at: datetime


class LaunchAttemptPage(RecoveryModel):
    items: list[LaunchAttemptRecord]
    next_cursor: int | None = None


class LaunchAttemptResolution(RecoveryModel):
    attempt: LaunchAttemptRecord
    procedure_id: str | None = None
