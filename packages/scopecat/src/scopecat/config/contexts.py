"""Bounded parameter-context resolution over immutable registry snapshots."""

from collections.abc import Mapping

from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.config.parameter_updates import (
    InsertParameterRows,
    ParameterUpdate,
    ReplaceParameter,
    UpdateParameterRows,
    materialize_context_updates,
)
from scopecat.config.profile_validation import validate_config_profile
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.value_types import Table
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ConfigContextRef, ConfigValueOrigin
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)


def validate_context_config(config: ConfigProfileSnapshot) -> None:
    problems = (
        *validate_config_profile(config, include_parameter_values=False),
        *validate_parameter_snapshot(
            config.parameter_catalog, config.parameter_snapshot, allow_missing=True
        ),
    )
    if problems:
        raise CheckFailed(problems)


def apply_context_overrides(
    config: ConfigProfileSnapshot, overrides: tuple[ParameterUpdate, ...]
) -> ConfigProfileSnapshot:
    parameters = materialize_context_updates(
        catalog=config.parameter_catalog,
        base=config.parameter_snapshot,
        updates=overrides,
    )
    resolved = config.model_copy(update={"parameter_snapshot": parameters})
    validate_context_config(resolved)
    return resolved


def context_value_origins(
    config: ConfigProfileSnapshot,
    *,
    base: ParameterSnapshot,
    base_ref: ConfigContextRef,
    selected_ref: ConfigContextRef,
    inherited: tuple[ConfigValueOrigin, ...] = (),
    overrides: tuple[ParameterUpdate, ...] = (),
) -> tuple[ConfigValueOrigin, ...]:
    """Flatten provenance so reading a context never walks a chain of copies."""
    origins: list[ConfigValueOrigin] = []
    for value in config.parameter_snapshot.values:
        before = base.get(value.id)
        if isinstance(value, ScalarParameterValue):
            old_origin = next(
                (
                    item
                    for item in inherited
                    if item.parameter_id == value.id and item.field_id is None
                ),
                None,
            )
            explicit = any(update.parameter_id == value.id for update in overrides)
            changed = before != value or explicit
            origins.append(
                ConfigValueOrigin(
                    parameter_id=value.id,
                    layer="run_override" if overrides else "context",
                    entry=selected_ref,
                )
                if changed
                else old_origin
                or ConfigValueOrigin(
                    parameter_id=value.id, layer="base", entry=base_ref
                )
            )
            continue
        definition = config.parameter_catalog.get(value.id)
        assert definition is not None and isinstance(definition.value_type, Table)
        keys = definition.value_type.primary_key
        before_rows = before.rows if isinstance(before, TableParameterValue) else ()
        for index, row in enumerate(value.rows):
            key = {field: row[field] for field in keys}
            empty: Mapping[str, ParameterAtomValue] = {}
            row_index = None if keys else index
            previous: Mapping[str, ParameterAtomValue] = (
                next(
                    (
                        item
                        for item in before_rows
                        if all(item[field] == atom for field, atom in key.items())
                    ),
                    empty,
                )
                if keys
                else before_rows[index]
                if index < len(before_rows)
                else empty
            )
            for field, atom in row.items():
                old_origin = next(
                    (
                        item
                        for item in inherited
                        if item.parameter_id == value.id
                        and item.key == key
                        and item.field_id == field
                        and item.row_index == row_index
                    ),
                    None,
                )
                explicit = any(
                    update.parameter_id == value.id
                    and (
                        isinstance(update, ReplaceParameter)
                        or (
                            isinstance(update, UpdateParameterRows)
                            and update.key == key
                            and field in update.values
                        )
                        or (
                            isinstance(update, InsertParameterRows)
                            and any(
                                all(
                                    inserted.get(name) == key_value
                                    for name, key_value in key.items()
                                )
                                if keys
                                else index >= len(before_rows) and row == inserted
                                for inserted in update.rows
                            )
                        )
                    )
                    for update in overrides
                )
                changed = field not in previous or previous[field] != atom or explicit
                origins.append(
                    ConfigValueOrigin(
                        parameter_id=value.id,
                        key=key,
                        field_id=field,
                        row_index=row_index,
                        layer="run_override" if overrides else "context",
                        entry=selected_ref,
                    )
                    if changed
                    else old_origin
                    or ConfigValueOrigin(
                        parameter_id=value.id,
                        key=key,
                        field_id=field,
                        row_index=row_index,
                        layer="base",
                        entry=base_ref,
                    )
                )
    # Explicitly declared unknown is provenance too, even without a stored atom.
    for origin in inherited:
        if origin.evidence is None or origin.evidence.origin != "unknown":
            continue
        value = config.parameter_snapshot.get(origin.parameter_id)
        definition = config.parameter_catalog.get(origin.parameter_id)
        if (
            not isinstance(value, TableParameterValue)
            or definition is None
            or not isinstance(definition.value_type, Table)
        ):
            continue
        if not any(
            column.id == origin.field_id for column in definition.value_type.columns
        ):
            continue
        row = next(
            (
                row
                for index, row in enumerate(value.rows)
                if (
                    index == origin.row_index
                    if not definition.value_type.primary_key
                    else all(row.get(key) == atom for key, atom in origin.key.items())
                )
            ),
            None,
        )
        if row is not None and origin.field_id not in row:
            origins.append(origin)
    return tuple(origins)


def missing_context_values(config: ConfigProfileSnapshot) -> tuple[str, ...]:
    """Describe unknown values without confusing them with invalid provided values."""
    missing: list[str] = []
    for definition in config.parameter_catalog.definitions:
        value = config.parameter_snapshot.get(definition.id)
        if value is None:
            missing.append(definition.id)
        elif isinstance(value, TableParameterValue) and isinstance(
            definition.value_type, Table
        ):
            for index, row in enumerate(value.rows):
                for column in definition.value_type.columns:
                    if column.id not in row:
                        missing.append(f"{definition.id}[{index}].{column.id}")
    return tuple(missing)
