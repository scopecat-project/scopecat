"""Durable outcomes for proposed parameter changes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.run_outcome import utc_now
from scopecat.records.config import ConfigContentHash
from scopecat.records.parameter import ParameterAtomValue, StoredParameterValue


class ParameterChangeApprovalRecord(BaseModel):
    """The one immutable operator approval for a parameter proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    proposal_id: str
    actor: str
    note: str = ""
    approved_at: datetime = Field(default_factory=utc_now)

    @field_validator("run_id", "proposal_id", "actor")
    @classmethod
    def validate_non_empty_identity(cls, value: str) -> str:
        if not value.strip():
            msg = "parameter change approval identity fields must be non-empty"
            raise ValueError(msg)
        return value


class ParameterCellEdit(BaseModel):
    """Exact scientific cell scope retained with a proposal's original base."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: Mapping[str, ParameterAtomValue]
    field: str
    before: ParameterAtomValue | None = None
    after: ParameterAtomValue | None = None
    change_kind: Literal["physical", "representation", "added", "removed"]

    @field_validator("key")
    @classmethod
    def freeze_key(
        cls, value: Mapping[str, ParameterAtomValue]
    ) -> Mapping[str, ParameterAtomValue]:
        return FrozenMapping(value.items())

    @field_serializer("key")
    def serialize_key(
        self, value: Mapping[str, ParameterAtomValue]
    ) -> dict[str, object]:
        return {
            name: item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for name, item in value.items()
        }


class ParameterValueDelta(BaseModel):
    """Durable before/after state for one proposed parameter change.

    The source config identifies the authoritative base; ``before`` verifies that
    base while ``after`` is the proposed value used to resolve a candidate.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    parameter_id: str
    before: StoredParameterValue
    after: StoredParameterValue
    # None means legacy or atomic value; () is authoritative no changed keyed cells.
    cells: tuple[ParameterCellEdit, ...] | None = None

    @model_validator(mode="after")
    def validate_values(self) -> ParameterValueDelta:
        if not self.parameter_id:
            msg = "parameter delta id must be non-empty"
            raise ValueError(msg)
        if self.before.id != self.parameter_id or self.after.id != self.parameter_id:
            msg = "parameter delta before/after ids must match parameter_id"
            raise ValueError(msg)
        if self.before == self.after:
            msg = f"parameter delta {self.parameter_id!r} must change its value"
            raise ValueError(msg)
        return self


class ParameterChangeProposal(BaseModel):
    """Immutable parameter changes proposed against one source config."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    id: str
    source_run_id: str
    analysis_record_id: str
    base_config_id: str
    base_config_content_hash: ConfigContentHash
    reason: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_output_ids: tuple[str, ...] = ()
    deltas: tuple[ParameterValueDelta, ...] = Field(min_length=1)
    proposed_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "source_run_id",
        "analysis_record_id",
        "base_config_id",
        "reason",
    )
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            msg = "parameter change proposal string fields must be non-empty"
            raise ValueError(msg)
        return value

    @field_validator("evidence_output_ids")
    @classmethod
    def validate_evidence_output_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("parameter change proposal evidence ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("parameter change proposal evidence ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_deltas(self) -> ParameterChangeProposal:
        seen: set[str] = set()
        for delta in self.deltas:
            if delta.parameter_id in seen:
                msg = f"duplicate parameter delta: {delta.parameter_id}"
                raise ValueError(msg)
            seen.add(delta.parameter_id)
        return self
