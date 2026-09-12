"""Explicit parameter-table structure edits and their retained provenance."""

from collections.abc import Mapping
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
from scopecat.kernel.value_types import Scalar, Table
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import ParameterAtomValue, ParameterDefinition


class _StructureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StructureValueDecision(_StructureModel):
    """An explicit value or unknown at an existing row's identity."""

    key: Mapping[str, ParameterAtomValue] = Field(default_factory=dict)
    row_index: int | None = Field(default=None, ge=0)
    value: ParameterAtomValue | None = None
    origin: Literal["unknown", "imported", "estimated", "measured"]
    note: str = Field(min_length=1)
    source_run_id: str | None = None

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

    @model_validator(mode="after")
    def validate_origin(self) -> StructureValueDecision:
        if (self.value is None) != (self.origin == "unknown"):
            raise ValueError(
                "unknown origin requires an absent value; "
                "provided values need an explicit origin"
            )
        if self.origin == "measured" and not self.source_run_id:
            raise ValueError("a measured value requires its source run id")
        return self


class AddParameterScalar(_StructureModel):
    """Declare one scalar with an explicit manually supplied initial value."""

    kind: Literal["add_scalar"] = "add_scalar"
    parameter_id: str = Field(min_length=1)
    value_type: Scalar
    value: ParameterAtomValue


class AddParameterTable(_StructureModel):
    """Declare a keyed table, initially empty; no initializer values are inferred."""

    kind: Literal["add_table"] = "add_table"
    parameter_id: str = Field(min_length=1)
    table: Table

    @field_validator("table")
    @classmethod
    def require_key(cls, table: Table) -> Table:
        if not table.primary_key:
            raise ValueError("a new author table requires a primary key")
        return table


class AddParameterColumn(_StructureModel):
    kind: Literal["add_column"] = "add_column"
    parameter_id: str = Field(min_length=1)
    column: ParameterDefinition
    values: tuple[StructureValueDecision, ...] = ()

    @field_validator("column")
    @classmethod
    def require_scalar(cls, column: ParameterDefinition) -> ParameterDefinition:
        if not isinstance(column.value_type, Scalar):
            raise ValueError("a parameter column must have a scalar type")
        return column


class RenameParameterColumn(_StructureModel):
    """Change a semantic column id explicitly; this is not an alias."""

    kind: Literal["rename_column"] = "rename_column"
    parameter_id: str = Field(min_length=1)
    column_id: str = Field(min_length=1)
    new_id: str = Field(min_length=1)


class ChangeParameterColumn(_StructureModel):
    kind: Literal["change_column"] = "change_column"
    parameter_id: str = Field(min_length=1)
    column: ParameterDefinition
    conversion: Literal[
        "compatible_unit",
        "lossless_numeric",
        "explicit_values",
        "patch_values",
        "unknown",
    ]
    values: tuple[StructureValueDecision, ...] = ()

    @field_validator("column")
    @classmethod
    def require_scalar(cls, column: ParameterDefinition) -> ParameterDefinition:
        if not isinstance(column.value_type, Scalar):
            raise ValueError("a parameter column must have a scalar type")
        return column

    @model_validator(mode="after")
    def require_explicit_policy(self) -> ChangeParameterColumn:
        if self.values and self.conversion not in {"explicit_values", "patch_values"}:
            raise ValueError(
                "value decisions require explicit_values or patch_values conversion"
            )
        return self


class ChangeParameterKey(_StructureModel):
    kind: Literal["change_key"] = "change_key"
    parameter_id: str = Field(min_length=1)
    columns: tuple[str, ...] = Field(min_length=1)


type ParameterStructureEdit = Annotated[
    AddParameterScalar
    | AddParameterTable
    | AddParameterColumn
    | RenameParameterColumn
    | ChangeParameterColumn
    | ChangeParameterKey,
    Field(discriminator="kind"),
]


class ParameterStructureOrigin(_StructureModel):
    """Retain the declaration; old configurations and runs are never rewritten."""

    before_version: Sha256ContentHash
    after_version: Sha256ContentHash
    edits: tuple[ParameterStructureEdit, ...]
