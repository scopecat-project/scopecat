"""Typed requests and responses for human or AI experiment interpretation."""

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

from scopecat.analysis.facts import (
    ANALYSIS_FACT_SCHEMA_CODEC,
    analysis_fact_structure_hash,
    validate_analysis_fact_json,
)
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.manual_line import ManualLineIntent
from scopecat.records.metadata import JsonMetadata

type _NonEmptyText = Annotated[str, Field(min_length=1)]
type InterpretationActorKind = Literal["human", "ai", "service"]


def _absent_manual_confirmation(value: object) -> bool:
    return value is None


class _InterpretationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class InterpretationRequest(_InterpretationModel):
    """One durable prompt whose response follows a stable structural schema."""

    title: _NonEmptyText
    instructions: _NonEmptyText
    schema_id: _NonEmptyText
    schema_codec: Literal["scopecat.analysis-fact-schema.v1"] = (
        ANALYSIS_FACT_SCHEMA_CODEC
    )
    schema_hash: Sha256ContentHash
    structure: JsonValue
    response_template: JsonValue | None = None
    metadata: JsonMetadata = Field(default_factory=dict)
    manual_line: ManualLineIntent | None = Field(
        default=None, exclude_if=_absent_manual_confirmation
    )

    @field_validator("title", "instructions", "schema_id")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("interpretation request text must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_schema_identity(self) -> InterpretationRequest:
        if self.schema_hash != analysis_fact_structure_hash(self.structure):
            raise ValueError(
                "interpretation schema hash must identify its durable structure"
            )
        if self.response_template is not None:
            try:
                validate_analysis_fact_json(self.response_template, self.structure)
            except TypeError as error:
                raise ValueError(
                    "interpretation response template does not match its schema"
                ) from error
        return self

    @property
    def request_hash(self) -> Sha256ContentHash:
        identity = {
            "codec": "scopecat.interpretation-request.v1",
            "request": self.model_dump(mode="json"),
        }
        return f"sha256:{stable_content_hash(identity)}"


class InterpretationResponse(_InterpretationModel):
    """Immutable structured judgment supplied by one identified actor."""

    actor: _NonEmptyText
    actor_kind: InterpretationActorKind
    value: JsonValue
    note: str = ""
    submitted_at: datetime
    confirmed_runtime_id: _NonEmptyText | None = Field(
        default=None, exclude_if=_absent_manual_confirmation
    )

    @field_validator("actor")
    @classmethod
    def validate_actor(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("interpretation response actor must be non-empty")
        return value

    @field_validator("submitted_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("interpretation response timestamp must be timezone-aware")
        return value


__all__ = [
    "InterpretationActorKind",
    "InterpretationRequest",
    "InterpretationResponse",
]
