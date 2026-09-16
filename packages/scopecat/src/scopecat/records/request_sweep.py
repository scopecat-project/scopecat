"""Retained request-level scan composition, independent of Python declarations."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Scalar
from scopecat.records.parameter import ParameterAtomValue


class ParameterSweep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    table: str
    column: str
    key: Mapping[str, ParameterAtomValue] = Field(min_length=1)
    value_type: Scalar
    values: tuple[float | Quantity, ...] = Field(min_length=1)

    @field_validator("key")
    @classmethod
    def freeze_key(
        cls, value: Mapping[str, ParameterAtomValue]
    ) -> Mapping[str, ParameterAtomValue]:
        return FrozenMapping(value.items())

    @field_serializer("key")
    def serialize_key(
        self, value: Mapping[str, ParameterAtomValue]
    ) -> dict[str, ParameterAtomValue]:
        return dict(value)
