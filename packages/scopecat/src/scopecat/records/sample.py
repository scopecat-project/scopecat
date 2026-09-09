"""Durable physical sample identities and immutable descriptive revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from scopecat.kernel.content_identity import canonical_json, stable_content_hash
from scopecat.kernel.run_outcome import utc_now
from scopecat.records.config import Topology
from scopecat.records.content import Sha256ContentHash

type _NonEmptyText = Annotated[str, Field(min_length=1)]
type SampleId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
type SampleLifecycleStatus = Literal[
    "received",
    "available",
    "mounted",
    "retired",
    "damaged",
]

_SAMPLE_REVISION_CODEC = "scopecat.sample-revision.v1"
MAX_SAMPLE_REVISION_BYTES = 1_000_000


class _SampleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SampleRelation(_SampleModel):
    """One typed relationship from this sample to another stable sample."""

    kind: _NonEmptyText
    sample_id: SampleId


class SampleArtifactRef(_SampleModel):
    """External design, image, report, or data reference associated with a sample."""

    id: _NonEmptyText
    title: _NonEmptyText
    uri: _NonEmptyText = Field(
        description=(
            "Imported project bytes use sha256:<digest>; absolute HTTP(S) is an "
            "external reference. Other URI forms remain readable but unavailable "
            "until a maintainer imports their bytes."
        )
    )
    media_type: _NonEmptyText | None = None


class SampleGeometryPoint(_SampleModel):
    """One entity position in a bounded two-dimensional sample projection."""

    entity_id: _NonEmptyText
    x: float
    y: float


class SampleGeometry(_SampleModel):
    """Optional domain-neutral geometry used by sample-map projections."""

    kind: Literal["cartesian"] = "cartesian"
    unit: _NonEmptyText | None = None
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    points: tuple[SampleGeometryPoint, ...] = ()

    @field_validator("points")
    @classmethod
    def validate_points(
        cls, value: tuple[SampleGeometryPoint, ...]
    ) -> tuple[SampleGeometryPoint, ...]:
        identities = tuple(point.entity_id for point in value)
        if len(identities) != len(set(identities)):
            raise ValueError("sample geometry entity ids must be unique")
        return value


class SampleRevisionDraft(_SampleModel):
    """Complete user-owned content for the next immutable sample revision."""

    display_name: _NonEmptyText
    status: SampleLifecycleStatus = "available"
    design_ref: _NonEmptyText | None = None
    aliases: tuple[_NonEmptyText, ...] = Field(default=(), max_length=64)
    tags: tuple[_NonEmptyText, ...] = Field(default=(), max_length=64)
    relations: tuple[SampleRelation, ...] = Field(default=(), max_length=128)
    properties: dict[str, JsonValue] = Field(default_factory=dict, max_length=256)
    topology: Topology | None = None
    geometry: SampleGeometry | None = None
    artifacts: tuple[SampleArtifactRef, ...] = Field(default=(), max_length=128)

    @field_validator("aliases", "tags")
    @classmethod
    def validate_unique_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("sample aliases and tags must be unique")
        return value

    @field_validator("relations")
    @classmethod
    def validate_relations(
        cls, value: tuple[SampleRelation, ...]
    ) -> tuple[SampleRelation, ...]:
        identities = tuple((item.kind, item.sample_id) for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("sample relations must be unique")
        return value

    @field_validator("artifacts")
    @classmethod
    def validate_artifacts(
        cls, value: tuple[SampleArtifactRef, ...]
    ) -> tuple[SampleArtifactRef, ...]:
        identities = tuple(item.id for item in value)
        if len(identities) != len(set(identities)):
            raise ValueError("sample artifact ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_geometry(self) -> SampleRevisionDraft:
        size = len(canonical_json(self.model_dump(mode="json")).encode("utf-8"))
        if size > MAX_SAMPLE_REVISION_BYTES:
            raise ValueError(
                f"sample revision must not exceed {MAX_SAMPLE_REVISION_BYTES} bytes"
            )
        if self.geometry is None:
            return self
        if self.topology is None and self.geometry.points:
            raise ValueError("sample geometry points require a sample topology")
        if self.topology is None:
            return self
        entity_ids = {entity.id for entity in self.topology.entities}
        missing = tuple(
            point.entity_id
            for point in self.geometry.points
            if point.entity_id not in entity_ids
        )
        if missing:
            raise ValueError(
                "sample geometry references unknown entities: "
                + ", ".join(dict.fromkeys(missing))
            )
        return self


class SampleRecord(_SampleModel):
    """Stable identity and current immutable revision of one physical sample."""

    id: SampleId
    kind: _NonEmptyText
    created_at: datetime = Field(default_factory=utc_now)
    active_revision: int = Field(ge=1)


class SampleRevision(_SampleModel):
    """One immutable descriptive snapshot of a physical sample."""

    sample_id: SampleId
    revision: int = Field(ge=1)
    content_hash: Sha256ContentHash
    recorded_at: datetime = Field(default_factory=utc_now)
    actor: _NonEmptyText
    note: str = ""
    content: SampleRevisionDraft

    @model_validator(mode="after")
    def validate_content_hash(self) -> SampleRevision:
        expected = sample_revision_content_hash(
            sample_id=self.sample_id,
            content=self.content,
        )
        if self.content_hash != expected:
            raise ValueError("sample revision content hash is inconsistent")
        return self


class SampleSelector(_SampleModel):
    """Operator intent selecting one sample revision for a run role."""

    role: _NonEmptyText = "subject"
    sample_id: SampleId
    revision: int | None = Field(default=None, ge=1)
    context_id: _NonEmptyText | None = None


class SampleBinding(_SampleModel):
    """Exact sample provenance frozen into an accepted run."""

    role: _NonEmptyText
    sample_id: SampleId
    revision: int = Field(ge=1)
    content_hash: Sha256ContentHash
    kind: _NonEmptyText
    display_name: _NonEmptyText
    context_id: _NonEmptyText | None = None

    @property
    def entity_scope(self) -> str:
        """Return the stable physical scope qualifying sample-local entities."""

        return self.sample_id


def sample_revision_content_hash(
    *,
    sample_id: str,
    content: SampleRevisionDraft,
) -> Sha256ContentHash:
    """Identify scientific sample content independently of audit timestamps."""

    digest = stable_content_hash(
        {
            "codec": _SAMPLE_REVISION_CODEC,
            "sample_id": sample_id,
            "content": content.model_dump(mode="json"),
        }
    )
    return f"sha256:{digest}"


__all__ = [
    "MAX_SAMPLE_REVISION_BYTES",
    "SampleArtifactRef",
    "SampleBinding",
    "SampleGeometry",
    "SampleGeometryPoint",
    "SampleId",
    "SampleLifecycleStatus",
    "SampleRecord",
    "SampleRelation",
    "SampleRevision",
    "SampleRevisionDraft",
    "SampleSelector",
    "sample_revision_content_hash",
]
