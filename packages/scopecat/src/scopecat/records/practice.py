"""Owned practice content inside an application, never another deployment."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.data_cleanup import DataCleanupPreview

type PracticeResource = Literal[
    "setup",
    "setup_definition",
    "parameters",
    "branch",
    "procedure",
    "run",
    "analysis",
    "source",
    "plan",
    "schedule",
    "calibration_task",
    "calibration_profile",
]
type PracticeFileDisposition = Literal["preserve", "discard"]


class PracticeScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    lesson: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: Literal["active", "cleaning", "cleared"] = "active"
    directory: str
    kind: Literal["peak", "notebook"] = "peak"
    workspace_id: str | None = None
    cleanup_preview: DataCleanupPreview | None = None
    procedure_id: str | None = None
    file_disposition: PracticeFileDisposition | None = None
    cleanup_error: str | None = None
    workers_retired: bool = False


class PracticeCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[PracticeScope, ...]


class PracticeCreateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_key: str = Field(min_length=1, max_length=200)
    lesson: Literal["manual-peaks"] = "manual-peaks"


class PracticeClearCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    files: PracticeFileDisposition


class NotebookPracticeCreateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = Field(min_length=1)
    lesson: str = Field(min_length=1)


class NotebookPracticeClearCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preview: DataCleanupPreview
    files: PracticeFileDisposition = "preserve"
