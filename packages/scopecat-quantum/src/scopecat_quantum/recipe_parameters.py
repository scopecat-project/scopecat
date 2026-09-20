"""Typed, point-local parameter edits for named gate recipe scopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict
from scopecat.authoring.parameter_fields import stored_parameter_value
from scopecat.authoring.parameter_models import (
    ParameterFieldIdentity,
    parameter_cell_key,
    parameter_definition,
    parameter_fields,
    parameter_table_name,
)
from scopecat.config.parameter_updates import (
    materialize_context_updates,
    update_parameter_rows,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.value_type_compatibility import require_assignable
from scopecat.kernel.value_types import Scalar
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.value_refs import ValueRef
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterCatalog,
    ParameterSnapshot,
)


@dataclass(frozen=True, slots=True)
class RecipeParameter:
    """One typed table cell and its literal or symbolic candidate value."""

    table: str
    column: str
    key: tuple[tuple[str, ParameterAtomValue], ...]
    value_type: Scalar
    value: ParameterAtomValue | ValueRef


@dataclass(frozen=True, slots=True)
class RecipeParameterBinding:
    """Data-only link from a compiler input to a scoped table cell."""

    scope: str
    table: str
    column: str
    key: tuple[tuple[str, ParameterAtomValue], ...]
    value_type: Scalar
    input_id: str


class RecipeParameterEvidence(BaseModel):
    """Resolved point edit, retained alongside the run's baseline provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    base_snapshot_id: str
    base_fingerprint: str
    scope_fingerprint: str
    scope: str
    table: str
    column: str
    key: dict[str, ParameterAtomValue]
    value: ParameterAtomValue


@dataclass(frozen=True, slots=True)
class ResolvedRecipeParameters:
    scoped_parameters: Mapping[str, ParameterSnapshot]
    evidence: tuple[RecipeParameterEvidence, ...]


def recipe_parameter(
    field: ParameterFieldIdentity,
    key: ParameterAtomValue | tuple[ParameterAtomValue, ...],
    value: object,
) -> RecipeParameter:
    """Select a candidate cell with the same field used by parameter_ref.

    Quantity fields accept literal magnitudes in their declared unit. Symbolic
    inputs retain their physical Quantity type, just like parameter_ref.
    """
    value_type = parameter_definition(field).value_type
    assert isinstance(value_type, Scalar)
    if isinstance(value, ValueRef):
        require_assignable(
            value.value_type, value_type, path=("recipe_parameters", field.name)
        )
        selected = value
    else:
        declared = next(
            item for item in parameter_fields(field.owner) if item.name == field.name
        )
        selected = stored_parameter_value(
            value, declared, label=f"{parameter_table_name(field.owner)}.{field.name}"
        )
    if not isinstance(selected, ValueRef):
        selected = cast(
            "ParameterAtomValue",
            coerce_literal(
                value_type, selected, path=("recipe_parameters", field.name)
            ),
        )
    return RecipeParameter(
        parameter_table_name(field.owner),
        field.name,
        tuple(parameter_cell_key(field, key).items()),
        value_type,
        selected,
    )


class _RecipeParameterProgram(Protocol):
    @property
    def recipe_parameter_bindings(self) -> tuple[RecipeParameterBinding, ...]: ...


def recipe_parameter_input_ids(program: _RecipeParameterProgram) -> frozenset[str]:
    """Return exactly the generated inputs owned by recipe bindings."""
    return frozenset(binding.input_id for binding in program.recipe_parameter_bindings)


def resolve_recipe_parameters(
    program: _RecipeParameterProgram,
    compiler_inputs: Mapping[str, object],
    *,
    catalog: ParameterCatalog,
    base: ParameterSnapshot,
) -> ResolvedRecipeParameters:
    """Apply each scope independently to the same frozen baseline.

    Values are validated even when they equal the baseline. Candidate scopes are
    transient compiler inputs, not accepted calibration changes.
    """
    if not program.recipe_parameter_bindings:
        return ResolvedRecipeParameters({}, ())
    values = tuple(
        cast(
            "ParameterAtomValue",
            coerce_literal(
                binding.value_type,
                compiler_inputs[binding.input_id],
                path=(
                    "recipe_parameters",
                    binding.scope,
                    binding.table,
                    binding.column,
                ),
            ),
        )
        for binding in program.recipe_parameter_bindings
    )
    scopes = dict.fromkeys(item.scope for item in program.recipe_parameter_bindings)
    snapshots = {
        scope: materialize_context_updates(
            catalog=catalog,
            base=base,
            updates=tuple(
                update_parameter_rows(
                    binding.table,
                    key=dict(binding.key),
                    values={binding.column: value},
                )
                for binding, value in zip(
                    program.recipe_parameter_bindings, values, strict=True
                )
                if binding.scope == scope
            ),
        )
        for scope in scopes
    }
    base_fingerprint = sha256_json_hash(
        [value.model_dump(mode="json") for value in base.values]
    )
    scope_fingerprints = {
        scope: sha256_json_hash(
            [value.model_dump(mode="json") for value in snapshot.values]
        )
        for scope, snapshot in snapshots.items()
    }
    evidence = tuple(
        RecipeParameterEvidence(
            base_snapshot_id=base.id,
            base_fingerprint=base_fingerprint,
            scope_fingerprint=scope_fingerprints[binding.scope],
            scope=binding.scope,
            table=binding.table,
            column=binding.column,
            key=dict(binding.key),
            value=value,
        )
        for binding, value in zip(
            program.recipe_parameter_bindings, values, strict=True
        )
    )
    return ResolvedRecipeParameters(snapshots, evidence)
