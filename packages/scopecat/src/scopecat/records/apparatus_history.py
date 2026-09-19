"""Descriptive apparatus history, never an assertion of live wiring or calibration."""

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.content import Sha256ContentHash

ApparatusObjectId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]
MAX_APPARATUS_ATTACHMENT_BYTES = 64 * 1024 * 1024
_ShortText = Annotated[str, Field(min_length=1, max_length=200)]
_ConditionText = Annotated[str, Field(max_length=4000)]


class _HistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApparatusObjectContent(_HistoryModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    aliases: tuple[_ShortText, ...] = Field(default=(), max_length=64)
    description: str = Field(default="", max_length=8000)

    @property
    def content_hash(self) -> Sha256ContentHash:
        return sha256_json_hash(
            {"codec": "scopecat.apparatus-object.v1", **self.model_dump(mode="json")}
        )


class ApparatusObjectRef(_HistoryModel):
    catalog_id: str = Field(min_length=1)
    object_id: ApparatusObjectId
    revision: int = Field(ge=1)
    content_hash: Sha256ContentHash


class ApparatusObjectRevision(_HistoryModel):
    ref: ApparatusObjectRef
    content: ApparatusObjectContent
    actor: str = Field(min_length=1, max_length=200)
    recorded_at: datetime


class ApparatusObjectCreate(_HistoryModel):
    catalog_id: str = Field(min_length=1)
    object_id: ApparatusObjectId
    content: ApparatusObjectContent
    actor: str = Field(min_length=1, max_length=200)


class ApparatusObjectRevise(_HistoryModel):
    expected: ApparatusObjectRef
    content: ApparatusObjectContent
    actor: str = Field(min_length=1, max_length=200)


class ApparatusObjectPage(_HistoryModel):
    items: tuple[ApparatusObjectRevision, ...] = ()
    next_cursor: int | None = None


class ApparatusAttachment(_HistoryModel):
    """Owned bytes; the filename is a label and never a server filesystem path."""

    content_hash: Sha256ContentHash
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, le=MAX_APPARATUS_ATTACHMENT_BYTES)


class ApparatusObservationDraft(_HistoryModel):
    subject: ApparatusObjectRef
    title: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=200)
    observed_at: AwareDatetime | None = None
    conditions: dict[_ShortText, _ConditionText] = Field(
        default_factory=dict, max_length=64
    )
    note: str = Field(default="", max_length=32000)
    run_ids: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(
        default=(), max_length=128
    )
    attachments: tuple[ApparatusAttachment, ...] = Field(default=(), max_length=32)
    supersedes: ApparatusObjectId | None = None


class ApparatusObservationCreate(_HistoryModel):
    observation_id: ApparatusObjectId
    draft: ApparatusObservationDraft


class ApparatusObservation(_HistoryModel):
    id: ApparatusObjectId
    draft: ApparatusObservationDraft
    recorded_at: datetime


class ApparatusObservationPage(_HistoryModel):
    items: tuple[ApparatusObservation, ...] = ()
    next_cursor: int | None = None
