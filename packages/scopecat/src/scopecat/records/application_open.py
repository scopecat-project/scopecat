"""Exact object targets and acknowledgements for an already-running desktop."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class OpenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApplicationOpenTarget(OpenModel):
    kind: Literal["run", "procedure", "analysis", "run_analysis", "sample_analysis"]
    object_id: str = Field(min_length=1, max_length=512)
    owner_id: str | None = Field(default=None, min_length=1, max_length=512)
    view: Literal["task", "decision"] = "task"

    @model_validator(mode="after")
    def validate_owner(self) -> ApplicationOpenTarget:
        if (self.kind in {"run_analysis", "sample_analysis"}) != (
            self.owner_id is not None
        ):
            raise ValueError("Owned analysis requires its exact run or sample owner")
        if self.view == "decision" and self.kind != "procedure":
            raise ValueError("Decision view requires a procedure")
        return self


class DesktopHostRegistration(OpenModel):
    project_id: str
    deployment_id: str
    process_id: int = Field(gt=0)
    process_created: float
    instance: UUID


class DesktopHostLease(OpenModel):
    host_id: UUID
    token: UUID


class DesktopHostStatus(OpenModel):
    host_id: UUID


class ApplicationOpenCommand(OpenModel):
    project_id: str
    deployment_id: str
    host_id: UUID
    request_id: UUID
    issued_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    target: ApplicationOpenTarget


class ApplicationOpenReceipt(OpenModel):
    request_id: UUID
    target: ApplicationOpenTarget
    state: Literal["pending", "opening", "opened", "failed"]
    detail: str | None = None


class DesktopOpenBatch(OpenModel):
    items: tuple[ApplicationOpenReceipt, ...]


class DesktopOpenAcknowledgement(OpenModel):
    lease: DesktopHostLease
    request_id: UUID
    opened: bool
    detail: str | None = None
