"""Explicit, version-bound parameter exchange through existing mutable tables."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from scopecat.api.parameter_edits import ParameterEdit, RowKey, same_parameter_value
from scopecat.config.validation import coerce_stored_parameter_value
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.json_types import JsonValue
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_identity import scalar_identity
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Table
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterDefinition,
    TableParameterValue,
)

if TYPE_CHECKING:
    import pandas as pd


class ExchangeTable(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def schema(self) -> Table: ...
    @property
    def context(self) -> ConfigContextRef | None: ...
    def __iter__(self) -> Iterator[RowKey]: ...
    def __getitem__(
        self, key: RowKey
    ) -> MutableMapping[str, ParameterAtomValue | None]: ...
    def __setitem__(
        self, key: RowKey, value: Mapping[str, ParameterAtomValue | None]
    ) -> None: ...
    def __delitem__(self, key: RowKey) -> None: ...


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    format: Literal["scopecat.parameter-table.v1"]
    definition: ParameterDefinition
    context: ConfigContextRef | None
    base: str
    rows: list[dict[str, JsonValue]]


type _Row = Mapping[str, ParameterAtomValue]
type _Identity = tuple[tuple[object, ...], ...]


def _normalized(
    table: ExchangeTable, rows: Sequence[Mapping[str, object]]
) -> tuple[_Row, ...]:
    value = TableParameterValue(
        id=table.name, rows=cast("Sequence[Mapping[str, ParameterAtomValue]]", rows)
    )
    normalized = coerce_stored_parameter_value(
        ParameterDefinition(id=table.name, value_type=table.schema),
        value,
        path=(table.name,),
        allow_missing=True,
    )
    assert isinstance(normalized, TableParameterValue)
    return tuple(normalized.rows)


def _rows(table: ExchangeTable) -> tuple[_Row, ...]:
    return _normalized(
        table,
        tuple(
            {name: value for name, value in table[key].items() if value is not None}
            for key in table
        ),
    )


def _key(table: ExchangeTable, row: _Row) -> RowKey:
    parts = tuple(row[name] for name in table.schema.primary_key)
    return parts[0] if len(parts) == 1 else parts


def _identity(table: ExchangeTable, row: _Row) -> _Identity:
    return tuple(scalar_identity(row[name]) for name in table.schema.primary_key)


def _document(table: ExchangeTable) -> _Document:
    stored = _rows(table)
    rows: list[dict[str, JsonValue]] = []
    for row in stored:
        exported: dict[str, JsonValue] = {}
        for column in table.schema.columns:
            value = row.get(column.id)
            if isinstance(value, Quantity):
                atom = column.value_type.atom
                assert isinstance(atom, QuantityType)
                exported[column.id] = (
                    value.to(atom.unit).value
                    if atom.unit
                    else {"value": value.value, "unit": value.unit}
                )
            elif isinstance(value, EntityRef):
                exported[column.id] = cast(
                    "dict[str, JsonValue]", value.model_dump(mode="json")
                )
            else:
                exported[column.id] = value
        rows.append(exported)
    document = _Document(
        format="scopecat.parameter-table.v1",
        definition=ParameterDefinition(id=table.name, value_type=table.schema),
        context=table.context,
        base="",
        rows=rows,
    )
    canonical = json.dumps(
        document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return document.model_copy(
        update={"base": hashlib.sha256(canonical.encode()).hexdigest()}
    )


def export_table_json(table: ExchangeTable) -> str:
    return (
        json.dumps(
            _document(table).model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate JSON field {name!r}")
        result[name] = value
    return result


def preview_table_json(
    table: ExchangeTable, text: str, *, delete_missing: bool = False
) -> ParameterImport:
    payload = cast("object", json.loads(text, object_pairs_hook=_unique_object))
    return _preview(
        table, _Document.model_validate(payload), delete_missing=delete_missing
    )


@dataclass(frozen=True, slots=True)
class ParameterImport:
    """A validated immutable preview. Applying edits only the selected draft."""

    diff: tuple[ParameterEdit, ...]
    _target: ExchangeTable = field(repr=False, compare=False)
    _base: str = field(repr=False)

    def apply(self) -> tuple[ParameterEdit, ...]:
        if _document(self._target).base != self._base:
            raise ValueError(
                "parameter table changed after preview; export and review again"
            )
        for edit in self.diff:
            assert edit.key is not None
            if edit.field is not None:
                self._target[edit.key][edit.field] = cast(
                    "ParameterAtomValue | None", edit.after
                )
            elif edit.after is None:
                del self._target[edit.key]
            else:
                self._target[edit.key] = cast(
                    "Mapping[str, ParameterAtomValue]", edit.after
                )
        return self.diff


def _preview(
    table: ExchangeTable, incoming: _Document, *, delete_missing: bool
) -> ParameterImport:
    current = _document(table)
    if incoming.definition != current.definition:
        raise ValueError(
            "table schema or unit metadata differs; use explicit schema edits"
        )
    if incoming.context != current.context or incoming.base != current.base:
        raise ValueError(
            "exported table base is stale or belongs to another context; "
            "export and review again"
        )
    columns = {column.id for column in table.schema.columns}
    for row in incoming.rows:
        if extra := row.keys() - columns:
            raise ValueError(f"{table.name}: unknown columns {sorted(extra)}")
        if any(row.get(name) is None for name in table.schema.primary_key):
            raise ValueError(
                f"{table.name}: every imported row requires keys "
                f"{table.schema.primary_key}"
            )
    normalized = _normalized(
        table,
        tuple({k: v for k, v in row.items() if v is not None} for row in incoming.rows),
    )
    before = {_identity(table, row): row for row in _rows(table)}
    after = {} if delete_missing else dict(before)
    for supplied, row in zip(incoming.rows, normalized, strict=True):
        identity = _identity(table, row)
        previous = before.get(identity, {})
        merged = dict(previous)
        for name, value in supplied.items():
            if (
                name in table.schema.primary_key
                and name in previous
                and not same_parameter_value(previous[name], row[name])
            ):
                raise ValueError(
                    f"{table.name}.{name}: cannot alter an existing key's metadata"
                )
            if value is None:
                merged.pop(name, None)
            else:
                merged[name] = row[name]
        after[identity] = FrozenMapping(merged.items())
    changes: list[ParameterEdit] = []
    for identity in dict.fromkeys((*before, *after)):
        old, new = before.get(identity), after.get(identity)
        selected = new if new is not None else old
        assert selected is not None
        key = _key(table, selected)
        if old is None or new is None:
            changes.append(ParameterEdit(table.name, key, None, old, new))
        else:
            cells = tuple(
                ParameterEdit(
                    table.name, key, column.id, old.get(column.id), new.get(column.id)
                )
                for column in table.schema.columns
                if not same_parameter_value(old.get(column.id), new.get(column.id))
            )
            if cells:
                changes.extend(cells)
    return ParameterImport(tuple(changes), table, current.base)


def table_dataframe(table: ExchangeTable) -> pd.DataFrame:
    import pandas as pd

    document = _document(table)
    frame = pd.DataFrame(
        document.rows,
        columns=[column.id for column in table.schema.columns],
        dtype=object,
    )
    frame.attrs["scopecat.parameters"] = document.model_dump(
        mode="json", exclude={"rows"}
    )
    return frame


def preview_table_dataframe(
    table: ExchangeTable,
    frame: pd.DataFrame,
    *,
    nan_as_unknown: bool = False,
    delete_missing: bool = False,
) -> ParameterImport:
    import pandas as pd

    metadata = cast("object", frame.attrs.get("scopecat.parameters"))
    if not isinstance(metadata, dict):
        raise ValueError(
            "DataFrame lost parameter metadata; start with table.to_dataframe()"
        )
    columns = cast("list[object]", list(frame.columns))
    if not all(isinstance(column, str) for column in columns) or len(
        set(columns)
    ) != len(columns):
        raise ValueError("DataFrame columns must be distinct parameter field names")
    rows: list[dict[str, object]] = []
    mapping_type: type[dict[str, object]] = dict
    # pandas has an overlapping default-mapping overload.
    records = frame.to_dict(orient="records", into=mapping_type)  # pyright: ignore[reportUnknownMemberType]
    for source in records:
        row: dict[str, object] = {}
        for name, value in source.items():
            if value is pd.NA:
                value = None
            elif isinstance(value, float) and math.isnan(value):
                if not nan_as_unknown:
                    raise ValueError(
                        f"{table.name}.{name}: NaN is ambiguous; "
                        "pass nan_as_unknown=True to clear cells"
                    )
                value = None
            row[name] = value
        rows.append(row)
    payload = {**cast("dict[str, object]", metadata), "rows": rows}
    return _preview(
        table, _Document.model_validate(payload), delete_missing=delete_missing
    )
