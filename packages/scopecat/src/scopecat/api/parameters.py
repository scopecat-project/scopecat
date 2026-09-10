"""Dictionary-style edits to an explicitly selected immutable parameter context."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, Self, cast, overload, override

from scopecat.authoring.parameter_dataclasses import (
    DataclassParameterField,
    dataclass_parameter_fields,
    dataclass_table_schema,
)
from scopecat.config.candidate_merges import merge_parameter_branches
from scopecat.config.contexts import apply_context_overrides
from scopecat.config.parameter_updates import (
    ParameterUpdate,
    delete_parameter_rows,
    insert_parameter_rows,
    replace_scalar_parameter,
    update_parameter_rows,
)
from scopecat.config.registry.records import ContextConfigRegistrySource
from scopecat.daemon.client import DaemonConflictError
from scopecat.daemon.views import ConfigContextResolution, ConfigEntryView
from scopecat.kernel.errors import Conflict
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.units import compatible_units, convert_linear_value
from scopecat.kernel.value_identity import scalar_identity
from scopecat.kernel.value_types import AtomType, Table
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_validation import coerce_literal
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)
from scopecat.records.sample import SampleSelector


class ParameterWorkspaceOperations(Protocol):
    """Existing registry operations needed by a parameter editor."""

    def entry(self, entry_id: str) -> ConfigEntryView: ...

    def resolve_context(
        self,
        context: ConfigContextRef,
        *,
        overrides: tuple[ParameterUpdate, ...] = (),
    ) -> ConfigContextResolution: ...

    def save_context(
        self,
        *,
        entry_id: str,
        base: ConfigContextRef,
        sample: SampleSelector,
        working_point_id: str,
        label: str,
        parameters: ParameterSnapshot | None = None,
        note: str = "",
    ) -> ConfigEntryView: ...


type RowKey = ParameterAtomValue | tuple[ParameterAtomValue, ...]
type _Identity = tuple[tuple[object, ...], ...]


def _same_value(left: object, right: object) -> bool:
    # Python's True == 1 must not hide an invalid scalar/cell edit from validation.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


@dataclass(frozen=True, slots=True)
class ParameterVersion:
    """Immutable saved version; the name is an identity, never a latest pointer."""

    context: ConfigContextRef

    @property
    def name(self) -> str:
        return self.context.entry_id


@dataclass(frozen=True, slots=True)
class ParameterEdit:
    """One readable cell/row change; None denotes an absent value in this slice."""

    parameter: str
    key: RowKey | None
    field: str | None
    before: ParameterAtomValue | Mapping[str, ParameterAtomValue] | None
    after: ParameterAtomValue | Mapping[str, ParameterAtomValue] | None


class ParameterWorkspace(Mapping[str, "ParameterTable"]):
    """An isolated edit buffer; reads never alter stored values or their units.

    Tables require declared primary keys. A single key uses a scalar and a
    composite key uses a tuple in schema order. Row views stay live across edits,
    save, rebase and discard; deleting a row permanently invalidates its views.
    Use ``dict(row)`` for a detached row or ``copy()`` for an independent workspace.
    Scalar parameters use ``scalar(name)`` and ``set_scalar(name, value)``.
    """

    def __init__(
        self,
        operations: ParameterWorkspaceOperations,
        *,
        context: str | ParameterVersion,
    ) -> None:
        self._operations = operations
        self._base = self._resolve(context)
        self._tables: dict[str, ParameterTable] = {}
        self._data: dict[str, _TableData] = {}
        self._scalars: dict[str, ParameterAtomValue] = {}
        self._load(self._base.config.parameter_snapshot)

    def _resolve(self, context: str | ParameterVersion) -> ConfigContextResolution:
        if isinstance(context, ParameterVersion):
            ref = context.context
        else:
            entry = self._operations.entry(context)
            if not isinstance(entry.entry.source, ContextConfigRegistrySource):
                raise ValueError(f"{context!r} is not a saved sample/workpoint context")
            ref = ConfigContextRef(
                entry_id=entry.entry.id, content_hash=entry.entry.content_hash
            )
        return self._operations.resolve_context(ref)

    @property
    def version(self) -> ParameterVersion:
        return ParameterVersion(self._base.config_source.context)

    @property
    def sample(self) -> str:
        return self._base.config_source.sample.sample_id

    @property
    def working_point(self) -> str:
        return self._base.config_source.sample.context_id or ""

    @override
    def __getitem__(self, name: str) -> ParameterTable:
        return self._tables[name]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._tables)

    @override
    def __len__(self) -> int:
        return len(self._tables)

    @overload
    def table(self, name: str) -> ParameterTable: ...

    @overload
    def table[T](self, name: str, *, row_type: type[T]) -> TypedParameterTable[T]: ...

    def table[T](
        self, name: str, *, row_type: type[T] | None = None
    ) -> ParameterTable | TypedParameterTable[T]:
        """Select a dictionary table or bind a standard dataclass view."""
        table = self[name]
        return table if row_type is None else TypedParameterTable(table, row_type)

    def scalar(self, name: str) -> ParameterAtomValue:
        return self._scalars[name]

    def set_scalar(self, name: str, value: ParameterAtomValue) -> None:
        if name not in self._scalars:
            raise KeyError(f"unknown scalar parameter {name!r}")
        self._scalars[name] = value

    def copy(self) -> Self:
        """Detach all edits while retaining the same immutable base and connection."""
        copied = type(self)(self._operations, context=self.version)
        copied._scalars = dict(self._scalars)
        for name, data in self._data.items():
            copied._data[name].load(tuple(data.rows.values()))
        return copied

    def discard(self) -> None:
        """Discard all edits and restore the last saved/rebased version."""
        self._load(self._base.config.parameter_snapshot)

    def _load(self, snapshot: ParameterSnapshot) -> None:
        self._scalars = {
            value.id: value.value
            for value in snapshot.values
            if isinstance(value, ScalarParameterValue)
        }
        for definition in self._base.config.parameter_catalog.definitions:
            if not isinstance(definition.value_type, Table):
                continue
            if not definition.value_type.primary_key:
                continue
            if snapshot.get(definition.id) is None:
                continue
            data = self._data.setdefault(
                definition.id, _TableData(definition.id, definition.value_type)
            )
            self._tables.setdefault(definition.id, ParameterTable(data))
            stored = snapshot.get(definition.id)
            data.load(stored.rows if isinstance(stored, TableParameterValue) else ())

    def diff(self) -> tuple[ParameterEdit, ...]:
        """Return a detached description without saving or validating the edits."""
        edits: list[ParameterEdit] = []
        for name, value in self._scalars.items():
            old = self._base.config.parameter_snapshot.get(name)
            assert isinstance(old, ScalarParameterValue)
            if not _same_value(old.value, value):
                edits.append(ParameterEdit(name, None, None, old.value, value))
        for name, table in self._data.items():
            old = self._base.config.parameter_snapshot.get(name)
            rows = old.rows if isinstance(old, TableParameterValue) else ()
            before = {table.identity(table.key(row)): row for row in rows}
            for identity in dict.fromkeys((*before, *table.rows)):
                previous = before.get(identity)
                current = table.rows.get(identity)
                selected = current if current is not None else previous
                assert selected is not None
                key = table.key(selected)
                if previous is None or current is None:
                    edits.append(ParameterEdit(name, key, None, previous, current))
                else:
                    for field in dict.fromkeys((*previous, *current)):
                        if not _same_value(previous.get(field), current.get(field)):
                            edits.append(
                                ParameterEdit(
                                    name,
                                    key,
                                    field,
                                    previous.get(field),
                                    current.get(field),
                                )
                            )
        # Row dictionaries belong to the workspace; detach them from this report.
        return tuple(
            ParameterEdit(
                e.parameter,
                e.key,
                e.field,
                dict(e.before) if isinstance(e.before, Mapping) else e.before,
                dict(e.after) if isinstance(e.after, Mapping) else e.after,
            )
            for e in edits
        )

    def _updates(self) -> tuple[ParameterUpdate, ...]:
        updates: list[ParameterUpdate] = []
        for edit in self.diff():
            try:
                updates.append(self._edit_update(edit))
            except (ValueError, TypeError) as error:
                location = edit.parameter
                if edit.key is not None:
                    location += f"[{edit.key!r}]"
                if edit.field is not None:
                    location += f".{edit.field}"
                raise ValueError(f"{location}: {error}") from error
        return tuple(updates)

    def _edit_update(self, edit: ParameterEdit) -> ParameterUpdate:
        if edit.key is None:
            if edit.after is None or isinstance(edit.after, Mapping):
                raise ValueError("supply a supported scalar value")
            return replace_scalar_parameter(edit.parameter, edit.after)
        table = self._data[edit.parameter]
        key = table.key_mapping(edit.key)
        if edit.field is not None:
            if edit.after is None or isinstance(edit.after, Mapping):
                raise ValueError("supply a supported scalar cell value")
            return update_parameter_rows(
                edit.parameter, key=key, values={edit.field: edit.after}
            )
        if edit.after is None:
            return delete_parameter_rows(edit.parameter, key=key)
        assert isinstance(edit.after, Mapping)
        return insert_parameter_rows(edit.parameter, (edit.after,))

    def preview(self) -> ConfigContextResolution:
        """Validate this buffer without persistence or default activation."""
        return self.freeze()

    def freeze(self) -> ConfigContextResolution:
        """Capture exact unsaved edits and original provenance for a future run."""
        return self._operations.resolve_context(
            self._base.config_source.context, overrides=self._updates()
        )

    def save(self, name: str, *, note: str = "") -> ParameterVersion:
        """Save a new immutable named branch, including an unchanged named copy.

        There is no mutable latest version. Use ``rebase(current=...)`` explicitly
        to incorporate another editor's version before saving. Existing names are
        never overwritten; choose a new name when the registry reports a conflict.
        """
        frozen = self.freeze()
        sample = frozen.config_source.sample
        try:
            saved = self._operations.save_context(
                entry_id=name,
                base=self._base.config_source.context,
                sample=SampleSelector(
                    role=sample.role,
                    sample_id=sample.sample_id,
                    revision=sample.revision,
                    context_id=sample.context_id,
                ),
                working_point_id=self.working_point,
                label=name,
                parameters=frozen.config.parameter_snapshot,
                note=note,
            )
        except (DaemonConflictError, Conflict) as error:
            raise ValueError(
                f"Could not save {name!r}: {error}. Existing versions are immutable; "
                "choose a new name, or reopen/rebase the desired version."
            ) from error
        self._base = self._operations.resolve_context(
            ConfigContextRef(
                entry_id=saved.entry.id, content_hash=saved.entry.content_hash
            )
        )
        self._load(self._base.config.parameter_snapshot)
        return self.version

    def rebase(self, *, current: str | ParameterVersion) -> None:
        """Merge independent cells; a conflict leaves the entire buffer unchanged."""
        selected = self._resolve(current)
        if selected.config_source.sample != self._base.config_source.sample:
            raise ValueError(
                "rebase requires the same exact sample revision and workpoint"
            )
        if selected.config.parameter_catalog != self._base.config.parameter_catalog:
            raise ValueError(
                "parameter schema changed; reopen and review the new table structure"
            )
        local = apply_context_overrides(self._base.config, self._updates())
        merged = merge_parameter_branches(
            base=self._base.config,
            local=local.parameter_snapshot,
            current=selected.config.parameter_snapshot,
        )
        self._base = selected
        self._load(merged)


class _TableData:
    """Private shared storage for dictionary and typed row views."""

    def __init__(self, name: str, schema: Table) -> None:
        self.name = name
        self.schema = schema
        self.rows: dict[_Identity, dict[str, ParameterAtomValue]] = {}
        self.tokens: dict[_Identity, object] = {}

    def key_mapping(self, key: RowKey) -> dict[str, ParameterAtomValue]:
        values = key if isinstance(key, tuple) else (key,)
        if len(values) != len(self.schema.primary_key):
            raise KeyError(f"{self.name} requires keys {self.schema.primary_key}")
        return {
            field: self.key_value(field, value)
            for field, value in zip(self.schema.primary_key, values, strict=True)
        }

    def key_value(self, field: str, value: ParameterAtomValue) -> ParameterAtomValue:
        column = next(column for column in self.schema.columns if column.id == field)
        return cast(
            "ParameterAtomValue",
            coerce_literal(column.value_type, value, path=(self.name, field)),
        )

    def identity(self, key: RowKey) -> _Identity:
        return tuple(scalar_identity(value) for value in self.key_mapping(key).values())

    def key(self, row: Mapping[str, ParameterAtomValue]) -> RowKey:
        values = tuple(row[name] for name in self.schema.primary_key)
        return values[0] if len(values) == 1 else values

    def load(self, rows: Sequence[Mapping[str, ParameterAtomValue]]) -> None:
        rebuilt = {self.identity(self.key(row)): dict(row) for row in rows}
        self.tokens = {key: self.tokens.get(key, object()) for key in rebuilt}
        self.rows = rebuilt


class ParameterTable(MutableMapping[RowKey, "ParameterRow"]):
    """Keyed rows shared by dictionary and future typed adapters."""

    def __init__(self, data: _TableData) -> None:
        self._data = data

    @property
    def name(self) -> str:
        return self._data.name

    @property
    def schema(self) -> Table:
        return self._data.schema

    @override
    def __getitem__(self, key: RowKey) -> ParameterRow:
        identity = self._data.identity(key)
        return ParameterRow(self._data, identity, self._data.tokens[identity])

    @override
    def __setitem__(self, key: RowKey, value: Mapping[str, ParameterAtomValue]) -> None:
        row = dict(value)
        for field, atom in self._data.key_mapping(key).items():
            if field in row and scalar_identity(
                self._data.key_value(field, row[field])
            ) != scalar_identity(atom):
                raise ValueError(f"{self.name}[{key!r}].{field}: row key cannot change")
            row[field] = atom
        unknown = set(row) - {column.id for column in self.schema.columns}
        if unknown:
            raise KeyError(f"{self.name}[{key!r}]: unknown fields {sorted(unknown)}")
        identity = self._data.identity(key)
        omitted = set(self._data.rows.get(identity, {})) - set(row)
        if omitted:
            raise ValueError(
                f"{self.name}[{key!r}]: replacement omits fields {sorted(omitted)}; "
                "supply their values or change the schema explicitly"
            )
        if identity in self._data.rows:
            for field in self.schema.primary_key:
                row[field] = self._data.rows[identity][field]
        self._data.rows[identity] = row
        self._data.tokens.setdefault(identity, object())

    @override
    def __delitem__(self, key: RowKey) -> None:
        identity = self._data.identity(key)
        del self._data.rows[identity]
        del self._data.tokens[identity]

    @override
    def __iter__(self) -> Iterator[RowKey]:
        return (self._data.key(row) for row in self._data.rows.values())

    @override
    def __len__(self) -> int:
        return len(self._data.rows)


class ParameterRow(MutableMapping[str, ParameterAtomValue]):
    """A live keyed row; adapters must read/write this view instead of cached copies."""

    def __init__(self, table: _TableData, identity: _Identity, token: object) -> None:
        self._table = table
        self._identity = identity
        self._token = token

    def _row(self) -> dict[str, ParameterAtomValue]:
        if self._table.tokens.get(self._identity) is not self._token:
            raise KeyError("row was deleted; select the desired row again")
        return self._table.rows[self._identity]

    @override
    def __getitem__(self, field: str) -> ParameterAtomValue:
        return self._row()[field]

    @override
    def __setitem__(self, field: str, value: ParameterAtomValue) -> None:
        row = self._row()
        if field in self._table.schema.primary_key:
            raise ValueError(
                f"{self._table.name}.{field}: delete/insert to change a row key"
            )
        if field not in {column.id for column in self._table.schema.columns}:
            raise KeyError(f"{self._table.name}.{field}: unknown parameter field")
        row[field] = value

    @override
    def __delitem__(self, field: str) -> None:
        raise ValueError(
            "delete the row or use an explicit schema change; fields are declared"
        )

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._row())

    @override
    def __len__(self) -> int:
        return len(self._row())


class TypedParameterTable[T](Mapping["RowKey", T]):
    """Live typed rows; detached new rows use the ordinary dataclass constructor.

    Assignment edits the shared workspace. Save/preview validates the resulting
    values. Reading normalizes quantity numbers without editing stored values.
    A row class should be a data-only, mutable dataclass: constructors and
    post-init hooks are for new rows and are not run when selecting existing ones.
    """

    def __init__(self, table: ParameterTable, row_type: type[T]) -> None:
        self._table = table
        self._row_type = row_type
        self._fields = dataclass_parameter_fields(row_type)
        inferred = dataclass_table_schema(
            row_type, primary_key=table.schema.primary_key
        )
        expected = {field.id: field.value_type for field in table.schema.columns}
        actual = {field.id: field.value_type for field in inferred.columns}
        if expected.keys() != actual.keys():
            raise TypeError(
                f"{table.name}: dataclass columns differ from the table "
                f"(missing {sorted(expected.keys() - actual.keys())}, "
                f"extra {sorted(actual.keys() - expected.keys())}); "
                "change the schema explicitly before binding"
            )
        for name, value_type in actual.items():
            stored = expected[name].atom
            declared = value_type.atom
            if (
                isinstance(stored, QuantityType)
                and isinstance(declared, QuantityType)
                and stored.unit is not None
                and declared.unit is not None
                and compatible_units(stored.unit, declared.unit)
            ):
                minimum = _bound(stored.minimum, stored.unit, declared.unit)
                maximum = _bound(stored.maximum, stored.unit, declared.unit)
                stored = replace(
                    stored,
                    unit=declared.unit,
                    dimension=declared.dimension,
                    minimum=minimum,
                    maximum=maximum,
                )
            if stored != declared:
                raise TypeError(
                    f"{table.name}.{name}: dataclass type/unit/bounds {declared!r} "
                    f"differ from table {expected[name].atom!r}; "
                    "match the declaration or change the schema explicitly"
                )

        properties: dict[str, object] = {
            field.name: _property(field, stored_type=expected[field.name].atom)
            for field in self._fields
        }
        properties["__slots__"] = ("_scopecat_binding",)
        self._live_type = type(f"{row_type.__name__}View", (row_type,), properties)

    @override
    def __getitem__(self, key: RowKey) -> T:
        row = self._table[key]
        # This cast represents a validated runtime subclass, not a schema guess.
        instance = cast("T", object.__new__(self._live_type))
        object.__setattr__(
            instance,
            "_scopecat_binding",
            _RowBinding(row, f"{self._table.name}[{key!r}]"),
        )
        return instance

    @override
    def __iter__(self) -> Iterator[RowKey]:
        return iter(self._table)

    @override
    def __len__(self) -> int:
        return len(self._table)

    def add(self, row: T) -> T:
        """Insert a new row; defaults are supplied only by its normal constructor."""
        if not isinstance(row, self._row_type):
            raise TypeError(f"{self._table.name}: expected {self._row_type.__name__}")
        values: dict[str, ParameterAtomValue] = {}
        for field in self._fields:
            value = cast("object", getattr(row, field.name))
            if value is None and field.optional:
                continue
            values[field.name] = _stored(
                value, field, label=f"{self._table.name}.{field.name}"
            )
        keys = tuple(values[name] for name in self._table.schema.primary_key)
        key = keys[0] if len(keys) == 1 else keys
        if key in self._table:
            raise ValueError(
                f"{self._table.name}[{key!r}]: row already exists; edit it explicitly"
            )
        self._table[key] = values
        return self[key]


def _bound(value: float | None, source: str, target: str) -> float | None:
    if value is None or source == target:
        return value
    converted = convert_linear_value(value, source, target)
    if converted is None:
        raise ValueError(
            f"cannot convert parameter bound from {source!r} to {target!r}"
        )
    return converted


def _stored(
    value: object, field: DataclassParameterField, *, label: str
) -> ParameterAtomValue:
    if value is None:
        raise ValueError(f"{label}: a required parameter cannot be None")
    atom = field.value_type.atom
    if isinstance(atom, QuantityType):
        assert atom.unit is not None
        if isinstance(value, Quantity):
            return value.to(atom.unit)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return Quantity(float(value), atom.unit)
    # Ordinary dataclass assignments are not a runtime validation boundary.
    # The existing workspace validates values when previewing or saving.
    return cast("ParameterAtomValue", value)


@dataclass(frozen=True, slots=True)
class _RowBinding:
    row: ParameterRow
    label: str


def _property(field: DataclassParameterField, *, stored_type: AtomType) -> property:
    def read(instance: object) -> object:
        binding = cast(
            "_RowBinding", object.__getattribute__(instance, "_scopecat_binding")
        )
        row, label = binding.row, f"{binding.label}.{field.name}"
        # Iteration preserves deletion errors; Mapping.__contains__ swallows KeyError.
        if field.name not in tuple(row):
            if field.optional:
                return None
            raise ValueError(
                f"{label}: parameter is unknown; supply a value or declare it Optional"
            )
        value = row[field.name]
        atom = field.value_type.atom
        if isinstance(atom, QuantityType):
            assert atom.unit is not None
            if isinstance(value, Quantity):
                return value.to(atom.unit).value
            if isinstance(value, int | float) and not isinstance(value, bool):
                assert isinstance(stored_type, QuantityType)
                assert stored_type.unit is not None
                return Quantity(float(value), stored_type.unit).to(atom.unit).value
        return value

    def write(instance: object, value: object) -> None:
        binding = cast(
            "_RowBinding", object.__getattribute__(instance, "_scopecat_binding")
        )
        row, label = binding.row, f"{binding.label}.{field.name}"
        if value is None and field.optional:
            if field.name not in tuple(row):
                return
            raise ValueError(
                f"{label}: clearing a stored value is not supported yet; "
                "keep the value or select a context where it is unknown"
            )
        row[field.name] = _stored(value, field, label=label)

    return property(read, write)


__all__ = [
    "ParameterEdit",
    "ParameterRow",
    "ParameterTable",
    "ParameterVersion",
    "ParameterWorkspace",
    "TypedParameterTable",
]
