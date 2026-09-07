"""Generic entity references shared by durable and runtime contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from scopecat.kernel.frozen import (
    FrozenMapping,
    freeze_json_mapping,
    thaw_json_value,
)


def _empty_entity_metadata() -> Mapping[str, object]:
    return FrozenMapping()


class EntityRef(BaseModel):
    """Reference to a domain entity without making the domain core vocabulary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    id: str
    kind: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=_empty_entity_metadata)

    @field_validator("metadata", mode="after")
    @classmethod
    def validate_metadata(
        cls,
        value: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Normalize metadata into an immutable finite JSON object."""

        return normalize_entity_metadata(value)

    @field_serializer("metadata")
    def serialize_metadata(self, value: object) -> dict[str, object]:
        """Serialize immutable authoring snapshots as ordinary JSON containers."""

        return cast(
            "dict[str, object]", thaw_json_value(normalize_entity_metadata(value))
        )


def entity_identity(value: EntityRef) -> tuple[str | None, str]:
    """Return the durable entity identity; metadata is descriptive only."""

    return value.kind, value.id


def entity_identity_key(value: EntityRef) -> str:
    """Return one collision-free canonical string for a durable entity identity."""

    return json.dumps(entity_identity(value), ensure_ascii=False, separators=(",", ":"))


def entity_axis_fingerprint(values: Sequence[EntityRef]) -> str:
    """Fingerprint one ordered entity axis independently of descriptive metadata."""

    encoded = json.dumps(
        tuple(entity_identity(value) for value in values),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def same_entity_identity(left: EntityRef, right: EntityRef) -> bool:
    """Compare the complete durable identity while ignoring metadata."""

    return entity_identity(left) == entity_identity(right)


def normalize_entity_metadata(value: object) -> FrozenMapping[str, object]:
    """Return a recursively immutable finite JSON metadata object."""

    if not isinstance(value, Mapping):
        msg = "entity metadata must be a JSON object"
        raise ValueError(msg)
    return freeze_json_mapping(
        cast("Mapping[str, object]", value),
        path="entity metadata",
    )


def entity_ref(entity: EntityRef | str, *, kind: str | None = None) -> EntityRef:
    if isinstance(entity, EntityRef):
        return entity
    return EntityRef(id=entity, kind=kind)
