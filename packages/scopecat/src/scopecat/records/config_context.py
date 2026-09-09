"""Exact parameter-context references and frozen per-run provenance."""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.quantity import Quantity
from scopecat.records.config import ConfigContentHash
from scopecat.records.parameter import ParameterAtomValue
from scopecat.records.sample import SampleBinding


class _ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConfigContextRef(_ContextModel):
    """An immutable registry entry; a display name is never an identity."""

    entry_id: str = Field(min_length=1)
    content_hash: ConfigContentHash


class ConfigValueOrigin(_ContextModel):
    """Origin of one scalar or one keyed table cell in an effective snapshot."""

    parameter_id: str
    key: Mapping[str, ParameterAtomValue] = Field(default_factory=dict)
    field_id: str | None = None
    row_index: int | None = Field(default=None, ge=0)
    layer: Literal["base", "context", "run_override"]
    entry: ConfigContextRef

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
            key: atom.model_dump(mode="json")
            if isinstance(atom, Quantity | EntityRef)
            else atom
            for key, atom in value.items()
        }


class ConfigContextMetadata(_ContextModel):
    """A named working point bound to one exact physical sample revision."""

    sample: SampleBinding
    working_point_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    base: ConfigContextRef
    value_origins: tuple[ConfigValueOrigin, ...] = ()


class ContextRunConfigSource(_ContextModel):
    """A context resolved without changing the lab's active configuration."""

    kind: Literal["parameter_context"] = "parameter_context"
    context: ConfigContextRef
    content_hash: ConfigContentHash
    lab_generation: int = Field(ge=1)
    sample: SampleBinding
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)
