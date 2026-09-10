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
from scopecat.config.structure import (
    ParameterStructurePlan,
    ParameterStructurePreview,
    parameter_structure_version,
    preview_parameter_structure,
)
from scopecat.daemon.client import DaemonConflictError
from scopecat.daemon.views import ConfigContextResolution, ConfigEntryView
from scopecat.kernel.errors import Conflict
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.units import compatible_units, convert_linear_value
from scopecat.kernel.value_identity import scalar_identity
from scopecat.kernel.value_types import AtomType, Table
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_validation import coerce_literal
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)
from scopecat.records.parameter_structure import (
    AddParameterColumn,
    AddParameterTable,
    ChangeParameterColumn,
    ChangeParameterKey,
    ParameterStructureEdit,
    RenameParameterColumn,
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
        structure_plan: ParameterStructurePlan | None = None,
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
        self._structure: list[ParameterStructureEdit] = []
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

    def _structure_plan(self) -> ParameterStructurePlan | None:
        if not self._structure:
            return None
        return ParameterStructurePlan(
            base=self.version.context,
            structure_version=parameter_structure_version(
                self._base.config.parameter_catalog
            ),
            edits=tuple(self._structure),
        )

    @property
    def _baseline(self) -> ConfigProfileSnapshot:
        plan = self._structure_plan()
        return (
            preview_parameter_structure(self._base.config, plan).config
            if plan
            else self._base.config
        )

    def structure_diff(self) -> ParameterStructurePreview | None:
        """Review explicit schema changes without saving or changing active defaults."""
        plan = self._structure_plan()
        return preview_parameter_structure(self._base.config, plan) if plan else None

    def _stage_structure(self, edits: Sequence[ParameterStructureEdit]) -> None:
        if self.diff():
            raise ValueError(
                "Save or discard value edits before changing the table structure"
            )
        plan = ParameterStructurePlan(
            base=self.version.context,
            structure_version=parameter_structure_version(
                self._base.config.parameter_catalog
            ),
            edits=(*self._structure, *edits),
        )
        preview = preview_parameter_structure(self._base.config, plan)
        # Old row views must not read a different semantic field/key after a rename.
        for item in edits:
            if item.parameter_id in self._data:
                self._data[item.parameter_id].tokens.clear()
        self._structure.extend(edits)
        self._load(preview.config.parameter_snapshot)

    def declare_table[T](
        self, name: str, row_type: type[T], *, key: str | tuple[str, ...]
    ) -> TypedParameterTable[T]:
        """Declare an empty table or add optional fields to an existing declaration.

        Existing values are never initialized from dataclass defaults. Renames,
        unit conversions and key changes require their explicit operations.
        Review structure_diff(), then save() before running with a changed schema.
        """
        keys = (key,) if isinstance(key, str) else key
        schema = dataclass_table_schema(row_type, primary_key=keys)
        definition = self._baseline.parameter_catalog.get(name)
        edits: list[ParameterStructureEdit] = []
        if definition is None:
            edits.append(AddParameterTable(parameter_id=name, table=schema))
        else:
            if not isinstance(definition.value_type, Table):
                raise ValueError(f"{name}: already declared as a scalar")
            previous = definition.value_type
            if previous.primary_key != keys:
                raise ValueError(
                    f"{name}: key changed from {previous.primary_key} to {keys}; "
                    "use change_key() explicitly"
                )
            old = {c.id: c for c in previous.columns}
            new = {c.id: c for c in schema.columns}
            incompatible = tuple(n for n in old if n not in new or old[n] != new[n])
            if incompatible:
                raise ValueError(
                    f"{name}: incompatible structure change in {incompatible}: "
                    f"before={previous!r}; after={schema!r}. "
                    "Use rename_column() or convert_unit() explicitly; "
                    "no automatic migration is applied."
                )
            optional = {
                f.name for f in dataclass_parameter_fields(row_type) if f.optional
            }
            for column in schema.columns:
                if column.id not in old:
                    if column.id not in optional:
                        raise ValueError(
                            f"{name}.{column.id}: new fields on existing rows must be "
                            "optional; initialize values explicitly"
                        )
                    edits.append(
                        AddParameterColumn(
                            parameter_id=name,
                            column=ParameterDefinition(
                                id=column.id, value_type=column.value_type
                            ),
                        )
                    )
        if edits:
            self._stage_structure(edits)
        return self.table(name, row_type=row_type)

    def rename_column(self, table: str, column: str, new_name: str) -> None:
        self._stage_structure(
            (
                RenameParameterColumn(
                    parameter_id=table, column_id=column, new_id=new_name
                ),
            )
        )

    def change_key(self, table: str, *, key: str | tuple[str, ...]) -> None:
        self._stage_structure(
            (
                ChangeParameterKey(
                    parameter_id=table, columns=(key,) if isinstance(key, str) else key
                ),
            )
        )

    def convert_unit(self, table: str, column: str, unit: str) -> None:
        source = next(c for c in self[table].schema.columns if c.id == column)
        atom = source.value_type.atom
        if not isinstance(atom, QuantityType) or atom.unit is None:
            raise ValueError(
                f"{table}.{column}: unit conversion requires a quantity column"
            )
        target = replace(
            atom,
            unit=unit,
            dimension=None,
            minimum=_bound(atom.minimum, atom.unit, unit),
            maximum=_bound(atom.maximum, atom.unit, unit),
        )
        self._stage_structure(
            (
                ChangeParameterColumn(
                    parameter_id=table,
                    column=ParameterDefinition(
                        id=column, value_type=replace(source.value_type, atom=target)
                    ),
                    conversion="compatible_unit",
                ),
            )
        )

    def scalar(self, name: str) -> ParameterAtomValue:
        return self._scalars[name]

    def set_scalar(self, name: str, value: ParameterAtomValue) -> None:
        if name not in self._scalars:
            raise KeyError(f"unknown scalar parameter {name!r}")
        self._scalars[name] = value

    def copy(self) -> Self:
        """Detach all edits while retaining the same immutable base and connection."""
        copied = type(self)(self._operations, context=self.version)
        copied._structure = list(self._structure)
        copied._load(copied._baseline.parameter_snapshot)
        copied._scalars = dict(self._scalars)
        for name, data in self._data.items():
            copied._data[name].load(tuple(data.rows.values()))
        return copied

    def discard(self) -> None:
        """Discard all edits and restore the last saved/rebased version."""
        self._structure.clear()
        self._load(self._base.config.parameter_snapshot)

    def _load(self, snapshot: ParameterSnapshot) -> None:
        names = {d.id for d in self._baseline.parameter_catalog.definitions}
        for name in set(self._data) - names:
            self._data[name].tokens.clear()
            del self._data[name]
            del self._tables[name]
        self._scalars = {
            value.id: value.value
            for value in snapshot.values
            if isinstance(value, ScalarParameterValue)
        }
        for definition in self._baseline.parameter_catalog.definitions:
            if not isinstance(definition.value_type, Table):
                continue
            if not definition.value_type.primary_key:
                continue
            if snapshot.get(definition.id) is None:
                continue
            data = self._data.setdefault(
                definition.id, _TableData(definition.id, definition.value_type)
            )
            if data.schema != definition.value_type:
                data.tokens.clear()
            data.schema = definition.value_type
            self._tables.setdefault(definition.id, ParameterTable(data))
            stored = snapshot.get(definition.id)
            data.load(stored.rows if isinstance(stored, TableParameterValue) else ())

    def diff(self) -> tuple[ParameterEdit, ...]:
        """Return a detached description without saving or validating the edits."""
        edits: list[ParameterEdit] = []
        for name, value in self._scalars.items():
            old = self._baseline.parameter_snapshot.get(name)
            assert isinstance(old, ScalarParameterValue)
            if not _same_value(old.value, value):
                edits.append(ParameterEdit(name, None, None, old.value, value))
        for name, table in self._data.items():
            old = self._baseline.parameter_snapshot.get(name)
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
            if isinstance(edit.after, Mapping):
                raise ValueError("supply a supported scalar cell value or None")
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
        if self._structure:
            raise ValueError(
                "Parameter structure has unsaved changes: review structure_diff() "
                "and save a named version before running."
            )
        return self._operations.resolve_context(
            self._base.config_source.context, overrides=self._updates()
        )

    def save(self, name: str, *, note: str = "") -> ParameterVersion:
        """Save a new immutable named branch, including an unchanged named copy.

        There is no mutable latest version. Use ``rebase(current=...)`` explicitly
        to incorporate another editor's version before saving. Existing names are
        never overwritten; choose a new name when the registry reports a conflict.
        """
        parameters = apply_context_overrides(
            self._baseline, self._updates()
        ).parameter_snapshot
        sample = self._base.config_source.sample
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
                parameters=parameters,
                structure_plan=self._structure_plan(),
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
        self._structure.clear()
        self._load(self._base.config.parameter_snapshot)
        return self.version

    def rebase(self, *, current: str | ParameterVersion) -> None:
        """Merge independent cells; a conflict leaves the entire buffer unchanged."""
        if self._structure:
            raise ValueError(
                "Save or discard pending structure changes before rebasing"
            )
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
    def __setitem__(
        self, key: RowKey, value: Mapping[str, ParameterAtomValue | None]
    ) -> None:
        row = {field: atom for field, atom in value.items() if atom is not None}
        for field, atom in self._data.key_mapping(key).items():
            if field in row and scalar_identity(
                self._data.key_value(field, row[field])
            ) != scalar_identity(atom):
                raise ValueError(f"{self.name}[{key!r}].{field}: row key cannot change")
            row[field] = atom
        unknown = set(value) - {column.id for column in self.schema.columns}
        if unknown:
            raise KeyError(f"{self.name}[{key!r}]: unknown fields {sorted(unknown)}")
        identity = self._data.identity(key)
        omitted = set(self._data.rows.get(identity, {})) - (
            set(value) | set(self.schema.primary_key)
        )
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


class ParameterRow(MutableMapping[str, ParameterAtomValue | None]):
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
    def __getitem__(self, field: str) -> ParameterAtomValue | None:
        row = self._row()
        if field not in {column.id for column in self._table.schema.columns}:
            raise KeyError(field)
        return row.get(field)

    @override
    def __setitem__(self, field: str, value: ParameterAtomValue | None) -> None:
        row = self._row()
        if field in self._table.schema.primary_key:
            raise ValueError(
                f"{self._table.name}.{field}: delete/insert to change a row key"
            )
        if field not in {column.id for column in self._table.schema.columns}:
            raise KeyError(f"{self._table.name}.{field}: unknown parameter field")
        if value is None:
            row.pop(field, None)
        else:
            row[field] = value

    @override
    def __delitem__(self, field: str) -> None:
        raise ValueError(
            "delete the row or use an explicit schema change; fields are declared"
        )

    @override
    def __iter__(self) -> Iterator[str]:
        self._row()
        return (column.id for column in self._table.schema.columns)

    @override
    def __len__(self) -> int:
        self._row()
        return len(self._table.schema.columns)


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
        if row[field.name] is None:
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
            row[field.name] = None
            return
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
