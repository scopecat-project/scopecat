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


class AnswerCondition(_InterpretationModel):
    """Equality against one unconditional scalar field of the same answer."""

    field: _NonEmptyText
    value: str | bool | int | float


class ScalarAnswerRule(_InterpretationModel):
    minimum: float | None = None
    maximum: float | None = None
    required_when: AnswerCondition | None = None
    empty_otherwise: bool = False

    @model_validator(mode="after")
    def validate_rule(self) -> ScalarAnswerRule:
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("answer minimum must not exceed maximum")
        if self.empty_otherwise and self.required_when is None:
            raise ValueError("empty_otherwise requires required_when")
        return self


class AnswerContract(_InterpretationModel):
    """Opt-in v1 rules for top-level numeric fields; not a schema language."""

    version: Literal[1] = 1
    fields: dict[str, ScalarAnswerRule]


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

    # Absence must serialize exactly as before: request_hash hashes model_dump.
    answer_contract: AnswerContract | None = Field(
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
        if self.answer_contract is not None:
            structure = self.structure
            if not isinstance(structure, dict) or structure.get("type") != "object":
                raise ValueError("answer contract requires an object schema")
            fields = structure.get("fields")
            if not isinstance(fields, dict):
                raise ValueError("answer contract requires object fields")
            for name, rule in self.answer_contract.fields.items():
                field = fields.get(name)
                nullable = False
                if isinstance(field, dict) and field.get("type") == "union":
                    variants = field.get("variants")
                    if (
                        isinstance(variants, list)
                        and len(variants) == 2
                        and {"type": "null"} in variants
                    ):
                        nullable = True
                        field = next(
                            (v for v in variants if v != {"type": "null"}), None
                        )
                if not isinstance(field, dict) or field.get("type") not in (
                    "float",
                    "int",
                ):
                    raise ValueError("answer rules require numeric scalar fields")
                if rule.required_when is not None:
                    condition = rule.required_when
                    controller = fields.get(condition.field)
                    if condition.field in self.answer_contract.fields:
                        raise ValueError(
                            "answer conditions require an unconditional controller"
                        )
                    if not isinstance(controller, dict) or controller.get(
                        "type"
                    ) not in ("string", "bool", "int", "float", "literal"):
                        raise ValueError(
                            "answer condition requires a scalar controller"
                        )
                    try:
                        validate_analysis_fact_json(condition.value, controller)
                    except TypeError as error:
                        raise ValueError(
                            "answer condition does not match its controller"
                        ) from error
                    if not nullable:
                        raise ValueError("conditional answer fields must be nullable")
        return self

    def normalize_answer(self, value: JsonValue) -> JsonValue:
        """Validate before canonicalizing; never supply omitted fields."""
        validate_analysis_fact_json(value, self.structure)
        if self.answer_contract is None:
            return value
        assert isinstance(value, dict)
        result = dict(value)
        for name, rule in self.answer_contract.fields.items():
            item = result[name]
            condition = rule.required_when
            if condition is not None:
                selected = value[condition.field]
                active = selected == condition.value and isinstance(
                    selected, bool
                ) == isinstance(condition.value, bool)
                if not active and rule.empty_otherwise:
                    result[name] = None
                    continue
                if active and item is None:
                    raise ValueError(
                        f"{name} is required when "
                        f"{condition.field} is {condition.value}"
                    )
            if item is None:
                continue
            assert isinstance(item, (int, float))
            if rule.minimum is not None and item < rule.minimum:
                raise ValueError(f"{name} must be at least {rule.minimum:g}")
            if rule.maximum is not None and item > rule.maximum:
                raise ValueError(f"{name} must be at most {rule.maximum:g}")
        return result

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
    "AnswerCondition",
    "AnswerContract",
    "InterpretationActorKind",
    "InterpretationRequest",
    "InterpretationResponse",
    "ScalarAnswerRule",
]
