"""Parameter evidence carried by the existing durable domain invocation intent."""

from collections.abc import Iterable, Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from scopecat.kernel.json_types import JsonValue
from scopecat.records.execution import DomainInvocationIntent

from scopecat_quantum.compilation import CompiledRecipeEntry
from scopecat_quantum.recipe_evidence import RecipeInputEvidence

_INTENT_KEY = "scopecat.quantum.parameter_evidence"


class CompiledPointParameterEvidence(BaseModel):
    """Evidence for one target entry at its logical run point ordinal."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    point_ordinal: int = Field(ge=0)
    entry_id: str = Field(min_length=1)
    recipes: tuple[RecipeInputEvidence, ...]


class CompiledParameterEvidenceRecord(BaseModel):
    """Current-format payload; empty recipe evidence does not prove completeness."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["scopecat.quantum.parameter_evidence.v1"] = (
        "scopecat.quantum.parameter_evidence.v1"
    )
    entries: tuple[CompiledPointParameterEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_entries(self) -> CompiledParameterEvidenceRecord:
        keys = [(entry.point_ordinal, entry.entry_id) for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("compiled parameter evidence repeats a point/entry pair")
        return self


def parameter_evidence_intent(
    entries: Iterable[tuple[int, CompiledRecipeEntry]],
    *,
    target_intent: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Attach actual compiled evidence before closing a domain invocation.

    Pass only entries belonging to that invocation. Ordinals must be the run's
    logical point ordinals, not an index within the current execution chunk.
    """
    if _INTENT_KEY in target_intent:
        raise ValueError("target intent already contains compiled parameter evidence")
    record = CompiledParameterEvidenceRecord(
        entries=tuple(
            CompiledPointParameterEvidence(
                point_ordinal=ordinal,
                entry_id=entry.entry.id.value,
                recipes=entry.parameter_evidence,
            )
            for ordinal, entry in entries
        )
    )
    return {
        **target_intent,
        _INTENT_KEY: cast("JsonValue", record.model_dump(mode="json")),
    }


def read_parameter_evidence(
    intent: DomainInvocationIntent,
) -> CompiledParameterEvidenceRecord:
    """Decode recorded evidence without loading author code or resolving queries."""
    if _INTENT_KEY not in intent.target_intent:
        raise ValueError("invocation has no compiled parameter evidence")
    return CompiledParameterEvidenceRecord.model_validate(
        intent.target_intent[_INTENT_KEY]
    )
