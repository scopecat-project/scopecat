"""Durable typed parameter edits, independent of configuration materialization."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.quantity import Quantity
from scopecat.records.parameter import ParameterAtomValue, StoredParameterValue

type _ParameterId = Annotated[str, Field(min_length=1)]


def _freeze_parameter_atoms(
    values: Mapping[str, ParameterAtomValue],
) -> FrozenMapping[str, ParameterAtomValue]:
    selected: list[tuple[str, ParameterAtomValue]] = []
    for name, value in values.items():
        number = value.value if isinstance(value, Quantity) else value
        if isinstance(number, float) and not math.isfinite(number):
            raise ValueError("parameter update atoms must be finite")
        selected.append((name, value))
    return FrozenMapping(selected)


class _ParameterUpdateModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class ReplaceParameter(_ParameterUpdateModel):
    """Replace one complete typed parameter value."""

    kind: Literal["replace_parameter"] = "replace_parameter"
    value: StoredParameterValue

    @property
    def parameter_id(self) -> str:
        return self.value.id

    @model_validator(mode="after")
    def validate_parameter_id(self) -> ReplaceParameter:
        if not self.parameter_id:
            raise ValueError("parameter id must be non-empty")
        return self


class UpdateParameterRows(_ParameterUpdateModel):
    """Update one row selected by a table primary key."""

    kind: Literal["update_parameter_rows"] = "update_parameter_rows"
    parameter_id: _ParameterId
    key: Mapping[str, ParameterAtomValue] = Field(min_length=1)
    values: Mapping[str, ParameterAtomValue] = Field(min_length=1)

    @field_validator("key", "values")
    @classmethod
    def freeze_atoms(
        cls,
        value: Mapping[str, ParameterAtomValue],
    ) -> Mapping[str, ParameterAtomValue]:
        return _freeze_parameter_atoms(value)

    @field_serializer("key", "values")
    def serialize_atoms(
        self,
        value: Mapping[str, ParameterAtomValue],
    ) -> dict[str, ParameterAtomValue]:
        return dict(value)


class InsertParameterRows(_ParameterUpdateModel):
    """Append rows to a table-shaped parameter."""

    kind: Literal["insert_parameter_rows"] = "insert_parameter_rows"
    parameter_id: _ParameterId
    rows: Sequence[Mapping[str, ParameterAtomValue]] = Field(min_length=1)

    @field_validator("rows")
    @classmethod
    def freeze_rows(
        cls,
        value: Sequence[Mapping[str, ParameterAtomValue]],
    ) -> Sequence[Mapping[str, ParameterAtomValue]]:
        return tuple(_freeze_parameter_atoms(row) for row in value)

    @field_serializer("rows")
    def serialize_rows(
        self,
        value: Sequence[Mapping[str, ParameterAtomValue]],
    ) -> list[dict[str, ParameterAtomValue]]:
        return [dict(row) for row in value]


class DeleteParameterRows(_ParameterUpdateModel):
    """Delete one row selected by a table primary key."""

    kind: Literal["delete_parameter_rows"] = "delete_parameter_rows"
    parameter_id: _ParameterId
    key: Mapping[str, ParameterAtomValue] = Field(min_length=1)

    @field_validator("key")
    @classmethod
    def freeze_key(
        cls,
        value: Mapping[str, ParameterAtomValue],
    ) -> Mapping[str, ParameterAtomValue]:
        return _freeze_parameter_atoms(value)

    @field_serializer("key")
    def serialize_key(
        self,
        value: Mapping[str, ParameterAtomValue],
    ) -> dict[str, ParameterAtomValue]:
        return dict(value)


type ParameterUpdate = Annotated[
    ReplaceParameter | UpdateParameterRows | InsertParameterRows | DeleteParameterRows,
    Field(discriminator="kind"),
]
