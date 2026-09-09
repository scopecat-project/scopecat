"""Supported sample attachment delivery, without filesystem URI execution."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from scopecat.records.sample import SampleArtifactRef

MAX_SAMPLE_ARTIFACT_BYTES = 8 * 1024 * 1024
SAMPLE_ARTIFACT_MEDIA_TYPES = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
    "text/plain",
    "application/json",
)


class SampleArtifactDelivery(BaseModel):
    """Resolution of one artifact owned by an exact sample revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: SampleArtifactRef
    status: Literal["stored", "external", "unavailable"]
    url: str | None = None
    reason: str
    repair: str | None = None


class SampleArtifactPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[SampleArtifactDelivery, ...] = ()


def is_owned_sample_artifact_uri(uri: str) -> bool:
    return re.fullmatch(r"sha256:[0-9a-f]{64}", uri) is not None
