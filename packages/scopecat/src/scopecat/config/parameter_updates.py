"""Canonical parameter update intents and snapshot materialization."""

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

from scopecat.config.validation import (
    coerce_parameter_table_cell,
    validate_parameter_representation,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_identity import scalar_identity, scalar_values_equal
from scopecat.kernel.value_types import Scalar, Table
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    StoredParameterValue,
    TableParameterValue,
)
from scopecat.records.parameter_change import ParameterCellEdit, ParameterValueDelta

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


def _serialize_parameter_atoms(
    values: Mapping[str, ParameterAtomValue],
) -> dict[str, object]:
    return {
        name: (
            value.model_dump(mode="json")
            if isinstance(value, Quantity | EntityRef)
            else value
        )
        for name, value in values.items()
    }


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
    ) -> dict[str, object]:
        return _serialize_parameter_atoms(value)


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
    ) -> list[dict[str, object]]:
        return [_serialize_parameter_atoms(row) for row in value]


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
    ) -> dict[str, object]:
        return _serialize_parameter_atoms(value)


type ParameterUpdate = Annotated[
    ReplaceParameter | UpdateParameterRows | InsertParameterRows | DeleteParameterRows,
    Field(discriminator="kind"),
]


def replace_scalar_parameter(
    parameter_id: str,
    value: ParameterAtomValue,
) -> ReplaceParameter:
    """Build a scalar replacement update from a closed durable atom."""

    return ReplaceParameter(value=ScalarParameterValue(id=parameter_id, value=value))


def replace_table_parameter(
    parameter_id: str,
    rows: Sequence[Mapping[str, ParameterAtomValue]],
) -> ReplaceParameter:
    """Build a complete table replacement update."""

    return ReplaceParameter(
        value=TableParameterValue(id=parameter_id, rows=tuple(rows))
    )


def update_parameter_rows(
    parameter_id: str,
    *,
    key: Mapping[str, ParameterAtomValue],
    values: Mapping[str, ParameterAtomValue],
) -> UpdateParameterRows:
    """Build a keyed row update.

    Parameter atoms are already immutable; catalog-dependent checks happen when
    an analysis materializes the update.
    """

    return UpdateParameterRows(
        parameter_id=parameter_id,
        key=key,
        values=values,
    )


def insert_parameter_rows(
    parameter_id: str,
    rows: Sequence[Mapping[str, ParameterAtomValue]],
) -> InsertParameterRows:
    """Build a table-row insertion update."""

    return InsertParameterRows(
        parameter_id=parameter_id,
        rows=rows,
    )


def delete_parameter_rows(
    parameter_id: str,
    *,
    key: Mapping[str, ParameterAtomValue],
) -> DeleteParameterRows:
    """Build a keyed row deletion update."""

    return DeleteParameterRows(
        parameter_id=parameter_id,
        key=key,
    )


def materialize_parameter_updates(
    *,
    catalog: ParameterCatalog,
    base: ParameterSnapshot,
    updates: Sequence[ParameterUpdate],
    candidate_id: str,
) -> tuple[ParameterSnapshot, tuple[ParameterValueDelta, ...]]:
    """Apply transient intents and return one authoritative candidate + delta."""

    if not updates:
        msg = "parameter change proposal requires at least one update"
        raise ValueError(msg)
    definitions = {item.id: item for item in catalog.definitions}
    original = {value.id: value for value in base.values}
    selected = dict(original)
    order = [value.id for value in base.values]
    touched: list[str] = []
    for update in updates:
        parameter_id = update.parameter_id
        definition = catalog.get(parameter_id)
        if definition is None:
            msg = f"parameter {parameter_id!r} is not defined in the catalog"
            raise ValueError(msg)
        current = selected.get(parameter_id)
        if current is None:
            msg = f"parameter {parameter_id!r} is missing from the base snapshot"
            raise ValueError(msg)
        if parameter_id not in touched:
            touched.append(parameter_id)
        if isinstance(update, ReplaceParameter):
            replacement = update.value.model_copy(deep=True)
            _require_matching_shape(
                parameter_id=parameter_id,
                expected=definition.value_type,
                value=replacement,
            )
            selected[parameter_id] = replacement
            continue
        if not isinstance(definition.value_type, Table) or not isinstance(
            current, TableParameterValue
        ):
            msg = f"parameter {parameter_id!r} is not table-shaped"
            raise ValueError(msg)
        selected[parameter_id] = _apply_table_update(
            current=current,
            table_type=definition.value_type,
            update=update,
        )

    for parameter_id in touched:
        definition = catalog.get(parameter_id)
        assert definition is not None
        selected[parameter_id] = validate_parameter_representation(
            definition, selected[parameter_id]
        )
    candidate = ParameterSnapshot(
        id=candidate_id,
        values=tuple(selected[value_id] for value_id in order),
    )
    deltas = tuple(
        ParameterValueDelta(
            parameter_id=parameter_id,
            before=original[parameter_id],
            after=selected[parameter_id],
            cells=parameter_cell_edits(
                definitions[parameter_id],
                original[parameter_id],
                selected[parameter_id],
            ),
        )
        for parameter_id in touched
        if original[parameter_id] != selected[parameter_id]
    )
    if not deltas:
        msg = "parameter change proposal does not change the base snapshot"
        raise ValueError(msg)
    return candidate, deltas


def apply_parameter_change_deltas(
    *,
    base: ParameterSnapshot,
    deltas: Sequence[ParameterValueDelta],
    candidate_id: str,
) -> ParameterSnapshot:
    """Apply one proposal's durable deltas against its base snapshot."""

    base_values = {value.id: value for value in base.values}
    selected = dict(base_values)
    order = [value.id for value in base.values]
    delta_by_id = {delta.parameter_id: delta for delta in deltas}
    if len(delta_by_id) != len(deltas):
        msg = "candidate proposal contains duplicate parameter deltas"
        raise ValueError(msg)
    changed = set(delta_by_id)
    unknown = changed - set(base_values)
    if unknown:
        msg = "candidate proposal references unknown parameters: " + ", ".join(
            sorted(unknown)
        )
        raise ValueError(msg)
    for parameter_id in changed:
        delta = delta_by_id[parameter_id]
        if delta.before != base_values[parameter_id]:
            msg = (
                "candidate proposal delta before value does not match source "
                f"snapshot: {parameter_id}"
            )
            raise ValueError(msg)
        selected[parameter_id] = delta.after
    return ParameterSnapshot(
        id=candidate_id,
        values=tuple(selected[value_id] for value_id in order),
    )


def _require_matching_shape(
    *,
    parameter_id: str,
    expected: Scalar | Table,
    value: StoredParameterValue,
) -> None:
    matches = (
        isinstance(expected, Scalar) and isinstance(value, ScalarParameterValue)
    ) or (isinstance(expected, Table) and isinstance(value, TableParameterValue))
    if not matches:
        msg = (
            f"parameter {parameter_id!r} replacement shape does not match "
            "its catalog definition"
        )
        raise ValueError(msg)


def _apply_table_update(
    *,
    current: TableParameterValue,
    table_type: Table,
    update: UpdateParameterRows | InsertParameterRows | DeleteParameterRows,
) -> TableParameterValue:
    if isinstance(update, InsertParameterRows):
        return TableParameterValue(
            id=current.id,
            rows=(*current.rows, *update.rows),
        )
    _require_complete_key(
        parameter_id=current.id,
        primary_key=table_type.primary_key,
        key=update.key,
    )
    key = _coerce_table_cells(
        parameter_id=current.id,
        table_type=table_type,
        values=update.key,
        path=("key",),
    )
    matches = [
        index
        for index, row in enumerate(current.rows)
        if _row_matches_key(
            parameter_id=current.id,
            table_type=table_type,
            row=row,
            key=key,
        )
    ]
    if not matches:
        msg = f"parameter table {current.id!r} has no row matching key"
        raise ValueError(msg)
    if len(matches) != 1:
        msg = f"parameter table {current.id!r} key matches multiple rows"
        raise ValueError(msg)
    selected_index = matches[0]
    if isinstance(update, DeleteParameterRows):
        rows = tuple(
            row for index, row in enumerate(current.rows) if index != selected_index
        )
    else:
        changed_key_columns = set(table_type.primary_key) & update.values.keys()
        if changed_key_columns:
            msg = (
                f"parameter table {current.id!r} row updates cannot change primary "
                "key columns: " + ", ".join(sorted(changed_key_columns))
            )
            raise ValueError(msg)
        values = _coerce_table_cells(
            parameter_id=current.id,
            table_type=table_type,
            values=update.values,
            path=("values",),
        )
        values.update(
            {
                name: value
                for name, value in update.values.items()
                if isinstance(value, Quantity)
            }
        )
        rows = tuple(
            (dict(row) | values) if index == selected_index else row
            for index, row in enumerate(current.rows)
        )
    return TableParameterValue(
        id=current.id,
        rows=rows,
    )


def _require_complete_key(
    *,
    parameter_id: str,
    primary_key: tuple[str, ...],
    key: Mapping[str, ParameterAtomValue],
) -> None:
    if not primary_key:
        msg = (
            f"parameter table {parameter_id!r} has no primary key; replace the "
            "complete table instead"
        )
        raise ValueError(msg)
    if set(key) != set(primary_key):
        msg = (
            f"parameter table {parameter_id!r} row key must contain exactly: "
            + ", ".join(primary_key)
        )
        raise ValueError(msg)


def _row_matches_key(
    *,
    parameter_id: str,
    table_type: Table,
    row: Mapping[str, ParameterAtomValue],
    key: Mapping[str, ParameterAtomValue],
) -> bool:
    if not key.keys() <= row.keys():
        return False
    normalized_row_key = _coerce_table_cells(
        parameter_id=parameter_id,
        table_type=table_type,
        values={column_id: row[column_id] for column_id in key},
        path=("current_row_key",),
    )
    return all(
        scalar_values_equal(normalized_row_key[column_id], value)
        for column_id, value in key.items()
    )


def _coerce_table_cells(
    *,
    parameter_id: str,
    table_type: Table,
    values: Mapping[str, ParameterAtomValue],
    path: tuple[str | int, ...],
) -> dict[str, ParameterAtomValue]:
    columns = {column.id: column for column in table_type.columns}
    unknown = sorted(values.keys() - columns.keys())
    if unknown:
        msg = (
            f"parameter table {parameter_id!r} update references unknown columns: "
            + ", ".join(unknown)
        )
        raise ValueError(msg)
    return {
        column_id: coerce_parameter_table_cell(
            parameter_id=parameter_id,
            column=columns[column_id],
            value=value,
            path=(*path, column_id),
        )
        for column_id, value in values.items()
    }


def parameter_cell_edits(
    definition: ParameterDefinition,
    before: StoredParameterValue,
    after: StoredParameterValue,
) -> tuple[ParameterCellEdit, ...] | None:
    """Project a keyed-table delta by semantic key, retaining exact atom values."""
    table = definition.value_type
    if not isinstance(table, Table) or not table.primary_key:
        return None
    assert isinstance(before, TableParameterValue)
    assert isinstance(after, TableParameterValue)

    def rows(
        value: TableParameterValue,
    ) -> dict[tuple[tuple[object, ...], ...], Mapping[str, ParameterAtomValue]]:
        return {
            tuple(scalar_identity(row[key]) for key in table.primary_key): row
            for row in value.rows
        }

    old, new = rows(before), rows(after)
    edits: list[ParameterCellEdit] = []
    for identity in dict.fromkeys((*old, *new)):
        left, right = old.get(identity), new.get(identity)
        source = right if right is not None else left
        assert source is not None
        key = {field: source[field] for field in table.primary_key}
        for column in table.columns:
            field = column.id
            if left is not None and right is not None and left[field] == right[field]:
                continue
            previous = left[field] if left is not None else None
            proposed = right[field] if right is not None else None
            kind = (
                "added"
                if left is None
                else "removed"
                if right is None
                else "representation"
                if scalar_values_equal(previous, proposed)
                else "physical"
            )
            edits.append(
                ParameterCellEdit(
                    key=key,
                    field=field,
                    before=previous,
                    after=proposed,
                    change_kind=kind,
                )
            )
    return tuple(edits)


def materialize_context_updates(
    *,
    catalog: ParameterCatalog,
    base: ParameterSnapshot,
    updates: Sequence[ParameterUpdate],
) -> ParameterSnapshot:
    """Apply explicit trial edits, including filling a previously unknown value.

    Unlike a scientific proposal, this creates no before/after acceptance claim.
    Present-value validation remains the resolver's responsibility.
    """
    selected = {value.id: value for value in base.values}
    for update in updates:
        definition = catalog.get(update.parameter_id)
        if definition is None:
            raise ValueError(f"parameter {update.parameter_id!r} is not defined")
        if isinstance(update, ReplaceParameter):
            _require_matching_shape(
                parameter_id=update.parameter_id,
                expected=definition.value_type,
                value=update.value,
            )
            selected[update.parameter_id] = update.value
        else:
            current = selected.get(update.parameter_id)
            if not isinstance(current, TableParameterValue) or not isinstance(
                definition.value_type, Table
            ):
                raise ValueError(
                    f"parameter {update.parameter_id!r} requires an existing table"
                )
            selected[update.parameter_id] = _apply_table_update(
                current=current,
                table_type=definition.value_type,
                update=update,
            )
    return ParameterSnapshot(id=base.id, values=tuple(selected.values()))
