"""Preview explicit table edits without rewriting saved configurations or runs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.compiler.frontend.parameter_contract_validation import (
    validate_parameter_contracts,
)
from scopecat.config.contexts import (
    context_value_origins,
    missing_context_values,
    validate_context_config,
)
from scopecat.config.validation import coerce_parameter_table_cell
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import Problem
from scopecat.kernel.quantity import Quantity as QuantityValue
from scopecat.kernel.units import compatible_units
from scopecat.kernel.value_identity import scalar_values_equal
from scopecat.kernel.value_types import Float, Int, Quantity, Scalar, Table, TableColumn
from scopecat.program.parameters import ParameterContract
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.config_context import (
    ConfigCellRef,
    ConfigContextRef,
    ConfigValueOrigin,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    StoredParameterValue,
    TableParameterValue,
)
from scopecat.records.parameter_structure import (
    AddParameterColumn,
    AddParameterScalar,
    AddParameterTable,
    ChangeParameterColumn,
    ChangeParameterKey,
    ParameterStructureEdit,
    ParameterStructureOrigin,
    RenameParameterColumn,
    StructureValueDecision,
)


class StructureConsumer(BaseModel):
    """Only explicitly supplied dependencies are assessed, including analysis."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    contracts: tuple[ParameterContract, ...]


class StructureConsumerImpact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    problems: tuple[Problem, ...]


class ParameterStructurePlan(BaseModel):
    """An ordered declaration against an exact saved snapshot and catalog shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    base: ConfigContextRef
    structure_version: Sha256ContentHash
    edits: tuple[ParameterStructureEdit, ...] = Field(min_length=1, max_length=64)
    consumers: tuple[StructureConsumer, ...] = ()


class StructureColumnImpact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parameter_id: str
    column_id: str | None = None
    kind: Literal[
        "scalar_added", "table_added", "added", "renamed", "type_changed", "key_changed"
    ]
    affected_rows: int
    missing_rows: tuple[int, ...] = ()
    consumer_action: str


class StructureCellMapping(BaseModel):
    """Current location and immediate pre-edit location are distinct."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    parameter_id: str
    row_index: int
    column_id: str
    source_column_id: str | None
    evidence: StructureValueDecision | None = None


class ParameterStructurePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    config: ConfigProfileSnapshot = Field(repr=False)
    origin: ParameterStructureOrigin = Field(repr=False)
    impacts: tuple[StructureColumnImpact, ...]
    missing_values: tuple[str, ...]
    cell_mappings: tuple[StructureCellMapping, ...] = Field(repr=False)
    consumers: tuple[StructureConsumerImpact, ...]
    consumer_scope: str = (
        "Affected parameter identities are reported. Arbitrary compiler and analysis "
        "code is not enumerated; preview each dependent experiment before use."
    )


def parameter_structure_version(catalog: ParameterCatalog) -> Sha256ContentHash:
    """Structure content identity, deliberately separate from database versions."""
    return sha256_json_hash(
        [
            {
                "id": definition.id,
                "value_type": definition.model_dump(mode="json")["value_type"],
            }
            for definition in catalog.definitions
        ]
    )


def preview_parameter_structure(
    config: ConfigProfileSnapshot, plan: ParameterStructurePlan
) -> ParameterStructurePreview:
    if config_content_hash(config) != plan.base.content_hash:
        raise ValueError("base configuration does not match this structure draft")
    if parameter_structure_version(config.parameter_catalog) != plan.structure_version:
        raise ValueError("parameter structure changed since this draft was prepared")
    definitions = {item.id: item for item in config.parameter_catalog.definitions}
    values = {item.id: item for item in config.parameter_snapshot.values}
    impacts: list[StructureColumnImpact] = []
    mappings = {
        (value.id, index, column.id): StructureCellMapping(
            parameter_id=value.id,
            row_index=index,
            column_id=column.id,
            source_column_id=column.id,
        )
        for value in config.parameter_snapshot.values
        if isinstance(value, TableParameterValue)
        for index, _row in enumerate(value.rows)
        for definition in config.parameter_catalog.definitions
        if definition.id == value.id and isinstance(definition.value_type, Table)
        for column in definition.value_type.columns
    }
    for edit in plan.edits:
        if isinstance(edit, AddParameterScalar | AddParameterTable):
            impacts.append(_add_parameter(edit, definitions, values))
            continue
        definition = definitions.get(edit.parameter_id)
        if definition is None or not isinstance(definition.value_type, Table):
            raise ValueError(
                f"{edit.parameter_id}: structural editing requires a declared table"
            )
        table = definition.value_type
        stored = values.get(edit.parameter_id)
        if stored is not None and not isinstance(stored, TableParameterValue):
            raise ValueError(f"{edit.parameter_id}: stored value is not a table")
        rows = [dict(row) for row in stored.rows] if stored is not None else []
        identities = tuple(dict(row) for row in rows)
        columns = {column.id: column for column in table.columns}
        keys = table.primary_key
        column_id: str | None = None
        if isinstance(edit, AddParameterColumn):
            column_id = edit.column.id
            if column_id in columns:
                raise ValueError(
                    f"{edit.parameter_id}.{column_id}: column already exists"
                )
            assert isinstance(edit.column.value_type, Scalar)
            columns[column_id] = TableColumn(column_id, edit.column.value_type)
            _apply_decisions(
                edit.parameter_id,
                table,
                rows,
                columns[column_id],
                edit.values,
                identities,
            )
            kind = "added"
            action = (
                "Existing consumers keep their IDs. New consumers must supply "
                "or handle missing values."
            )
        elif isinstance(edit, RenameParameterColumn):
            column_id = edit.new_id
            if edit.column_id not in columns or edit.new_id in columns:
                raise ValueError(
                    "rename requires an existing source and unused target column ID"
                )
            columns = {
                edit.new_id if name == edit.column_id else name: TableColumn(
                    edit.new_id, column.value_type
                )
                if name == edit.column_id
                else column
                for name, column in columns.items()
            }
            keys = tuple(edit.new_id if key == edit.column_id else key for key in keys)
            for row in rows:
                if edit.column_id in row:
                    row[edit.new_id] = row.pop(edit.column_id)
            kind = "renamed"
            action = (
                f"Update explicit consumers of {edit.column_id} to {edit.new_id}; "
                "no automatic alias is installed."
            )
        elif isinstance(edit, ChangeParameterColumn):
            column_id = edit.column.id
            before = columns.get(column_id)
            if before is None:
                raise ValueError(f"{edit.parameter_id}.{column_id}: unknown column")
            assert isinstance(edit.column.value_type, Scalar)
            target = TableColumn(column_id, edit.column.value_type)
            columns[column_id] = target
            _change_column_values(edit, before, target, rows)
            _apply_decisions(
                edit.parameter_id, table, rows, target, edit.values, identities
            )
            kind = "type_changed"
            action = (
                "Recheck typed experiment, analysis and compiler consumers "
                "against the new type/unit."
            )
        else:
            assert isinstance(edit, ChangeParameterKey)
            keys = edit.columns
            kind = "key_changed"
            action = (
                "Update every lookup contract to the new complete key; "
                "duplicate or unknown keys are rejected."
            )
        _update_cell_mappings(edit, table, rows, identities, mappings)
        updated_table = Table(columns=tuple(columns.values()), primary_key=keys)
        definitions[edit.parameter_id] = definition.model_copy(
            update={"value_type": updated_table}
        )
        if stored is not None:
            values[edit.parameter_id] = TableParameterValue(
                id=stored.id, rows=tuple(rows)
            )
        impacts.append(
            StructureColumnImpact(
                parameter_id=edit.parameter_id,
                column_id=column_id,
                kind=kind,
                affected_rows=len(rows),
                missing_rows=tuple(
                    index
                    for index, row in enumerate(rows)
                    if column_id is not None and column_id not in row
                ),
                consumer_action=action,
            )
        )
    catalog = ParameterCatalog(
        id=config.parameter_catalog.id, definitions=tuple(definitions.values())
    )
    result = config.model_copy(
        update={
            "system": config.system.model_copy(update={"parameter_catalog": catalog}),
            "parameter_snapshot": ParameterSnapshot(
                id=config.parameter_snapshot.id, values=tuple(values.values())
            ),
        }
    )
    validate_context_config(result)
    consumer_impacts: list[StructureConsumerImpact] = []
    for consumer in plan.consumers:
        problems: list[Problem] = []
        for contract in consumer.contracts:
            try:
                validate_parameter_contracts(catalog, (contract,))
            except CheckFailed as error:
                problems.extend(error.problems)
        consumer_impacts.append(
            StructureConsumerImpact(name=consumer.name, problems=tuple(problems))
        )
    return ParameterStructurePreview(
        config=result,
        origin=ParameterStructureOrigin(
            before_version=plan.structure_version,
            after_version=parameter_structure_version(catalog),
            edits=plan.edits,
        ),
        impacts=tuple(impacts),
        missing_values=missing_context_values(result),
        cell_mappings=tuple(mappings.values()),
        consumers=tuple(consumer_impacts),
    )


def _update_cell_mappings(
    edit: ParameterStructureEdit,
    table: Table,
    rows: list[dict[str, ParameterAtomValue]],
    identities: tuple[dict[str, ParameterAtomValue], ...],
    mappings: dict[tuple[str, int, str], StructureCellMapping],
) -> None:
    for index in range(len(rows)):
        if isinstance(edit, RenameParameterColumn):
            previous = mappings.pop((edit.parameter_id, index, edit.column_id))
            mappings[(edit.parameter_id, index, edit.new_id)] = previous.model_copy(
                update={"column_id": edit.new_id}
            )
        elif isinstance(edit, AddParameterColumn | ChangeParameterColumn):
            address = (edit.parameter_id, index, edit.column.id)
            previous = mappings.get(address)
            decision = next(
                (
                    item
                    for item in edit.values
                    if (
                        item.row_index == index
                        if not table.primary_key
                        else all(
                            scalar_values_equal(identities[index][key], atom)
                            for key, atom in item.key.items()
                        )
                    )
                ),
                None,
            )
            replaced = isinstance(edit, AddParameterColumn) or (
                edit.conversion in {"explicit_values", "unknown"}
                or (edit.conversion == "patch_values" and decision is not None)
            )
            mappings[address] = StructureCellMapping(
                parameter_id=edit.parameter_id,
                row_index=index,
                column_id=edit.column.id,
                source_column_id=None
                if replaced
                else previous.source_column_id
                if previous
                else None,
                evidence=decision
                if replaced
                else previous.evidence
                if previous
                else None,
            )


def _apply_decisions(
    parameter_id: str,
    table: Table,
    rows: list[dict[str, ParameterAtomValue]],
    column: TableColumn,
    decisions: tuple[StructureValueDecision, ...],
    identities: tuple[dict[str, ParameterAtomValue], ...],
) -> None:
    seen: set[int] = set()
    for decision in decisions:
        if table.primary_key:
            if (
                set(decision.key) != set(table.primary_key)
                or decision.row_index is not None
            ):
                raise ValueError(
                    "a value decision must use exactly the existing table key"
                )
            matches = [
                index
                for index, row in enumerate(identities)
                if all(
                    key in row and scalar_values_equal(row[key], value)
                    for key, value in decision.key.items()
                )
            ]
        else:
            if decision.key or decision.row_index is None:
                raise ValueError("a table without a key requires an explicit row index")
            matches = [decision.row_index] if decision.row_index < len(rows) else []
        if len(matches) != 1 or matches[0] in seen:
            raise ValueError(
                "a value decision must identify one existing row exactly once"
            )
        index = matches[0]
        seen.add(index)
        if decision.value is None:
            rows[index].pop(column.id, None)
        else:
            rows[index][column.id] = coerce_parameter_table_cell(
                parameter_id=parameter_id,
                column=column,
                value=decision.value,
                path=(parameter_id, index, column.id),
            )


def _change_column_values(
    edit: ChangeParameterColumn,
    before: TableColumn,
    target: TableColumn,
    rows: list[dict[str, ParameterAtomValue]],
) -> None:
    if edit.conversion in {"unknown", "explicit_values"}:
        for row in rows:
            row.pop(target.id, None)
    elif edit.conversion != "patch_values":
        _convert_rows(edit, before, target, rows)


def _convert_rows(
    edit: ChangeParameterColumn,
    before: TableColumn,
    target: TableColumn,
    rows: list[dict[str, ParameterAtomValue]],
) -> None:
    if edit.values:
        raise ValueError("automatic conversion and explicit values must not be mixed")
    if edit.conversion == "compatible_unit":
        if not isinstance(before.value_type.atom, Quantity) or not isinstance(
            target.value_type.atom, Quantity
        ):
            raise ValueError(
                "compatible-unit conversion requires quantity types on both sides"
            )
        if (
            before.value_type.atom.unit is None
            or target.value_type.atom.unit is None
            or not compatible_units(
                before.value_type.atom.unit, target.value_type.atom.unit
            )
        ):
            raise ValueError(
                "compatible-unit conversion requires compatible quantity units"
            )
    elif not isinstance(before.value_type.atom, Int | Float) or not isinstance(
        target.value_type.atom, Int | Float
    ):
        raise ValueError("lossless numeric conversion requires int or float types")
    for index, row in enumerate(rows):
        value = row.get(target.id)
        if value is None:
            continue
        if edit.conversion == "lossless_numeric" and isinstance(
            target.value_type.atom, Int
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or int(value) != value
            ):
                raise ValueError(
                    f"{edit.parameter_id}[{index}].{target.id}: conversion would lose "
                    "information; provide an explicit value or mark unknown"
                )
            value = int(value)
        if edit.conversion == "compatible_unit" and isinstance(value, int | float):
            assert isinstance(before.value_type.atom, Quantity)
            assert before.value_type.atom.unit is not None
            value = QuantityValue(float(value), before.value_type.atom.unit)
        converted = coerce_parameter_table_cell(
            parameter_id=edit.parameter_id,
            column=target,
            value=value,
            path=(edit.parameter_id, index, target.id),
        )
        if edit.conversion == "lossless_numeric" and converted != value:
            raise ValueError(
                f"{edit.parameter_id}[{index}].{target.id}: "
                "numeric conversion is not lossless"
            )
        row[target.id] = converted


def mapped_structure_origins(
    before: ConfigProfileSnapshot,
    preview: ParameterStructurePreview,
    *,
    base_ref: ConfigContextRef,
    selected_ref: ConfigContextRef,
    inherited: tuple[ConfigValueOrigin, ...],
) -> tuple[ConfigValueOrigin, ...]:
    """Retain source addresses through rename, key changes, and unit conversion."""
    origins = [
        item
        for item in context_value_origins(
            before,
            base=before.parameter_snapshot,
            base_ref=base_ref,
            selected_ref=selected_ref,
            inherited=inherited,
        )
        if item.field_id is None
    ]
    origins.extend(
        ConfigValueOrigin(parameter_id=value.id, layer="context", entry=selected_ref)
        for value in preview.config.parameter_snapshot.values
        if isinstance(value, ScalarParameterValue)
        and before.parameter_snapshot.get(value.id) is None
    )
    for mapping in preview.cell_mappings:
        definition = preview.config.parameter_catalog.get(mapping.parameter_id)
        old_definition = before.parameter_catalog.get(mapping.parameter_id)
        value = preview.config.parameter_snapshot.get(mapping.parameter_id)
        old_value = before.parameter_snapshot.get(mapping.parameter_id)
        assert definition is not None and isinstance(definition.value_type, Table)
        assert old_definition is not None and isinstance(
            old_definition.value_type, Table
        )
        assert isinstance(value, TableParameterValue)
        assert isinstance(old_value, TableParameterValue)
        row = value.rows[mapping.row_index]
        old_row = old_value.rows[mapping.row_index]
        key = {name: row[name] for name in definition.value_type.primary_key}
        old_key = {
            name: old_row[name] for name in old_definition.value_type.primary_key
        }
        old_index = None if old_key else mapping.row_index
        prior = (
            next(
                (
                    item
                    for item in inherited
                    if item.parameter_id == mapping.parameter_id
                    and item.field_id == mapping.source_column_id
                    and item.key == old_key
                    and item.row_index == old_index
                ),
                None,
            )
            if mapping.source_column_id is not None
            else None
        )
        if (
            prior is not None
            and mapping.source_column_id == mapping.column_id
            and old_key == key
            and old_index == (None if key else mapping.row_index)
            and old_row.get(mapping.column_id) == row.get(mapping.column_id)
        ):
            origins.append(prior)
            continue
        source = prior.source_cell if prior is not None else None
        if source is None and mapping.source_column_id in old_row:
            source = ConfigCellRef(
                entry=prior.entry if prior is not None else base_ref,
                parameter_id=mapping.parameter_id,
                field_id=mapping.source_column_id,
                key=old_key,
                row_index=old_index,
            )
        origins.append(
            ConfigValueOrigin(
                parameter_id=mapping.parameter_id,
                field_id=mapping.column_id,
                key=key,
                row_index=None if key else mapping.row_index,
                layer="context",
                entry=selected_ref,
                source_cell=source,
                evidence=(
                    mapping.evidence
                    or StructureValueDecision(
                        key=key,
                        row_index=None if key else mapping.row_index,
                        origin="unknown",
                        note="Newly declared parameter has no value",
                    )
                )
                if mapping.column_id not in row
                else mapping.evidence
                if mapping.source_column_id is None
                else prior.evidence
                if prior
                else None,
            )
        )
    return tuple(origins)


def _add_parameter(
    edit: AddParameterScalar | AddParameterTable,
    definitions: dict[str, ParameterDefinition],
    values: dict[str, StoredParameterValue],
) -> StructureColumnImpact:
    if edit.parameter_id in definitions:
        raise ValueError(f"{edit.parameter_id}: parameter already exists")
    if isinstance(edit, AddParameterScalar):
        value_type = edit.value_type
        value = ScalarParameterValue(id=edit.parameter_id, value=edit.value)
        kind = "scalar_added"
        action = "Review the explicit manual initial value before saving."
    else:
        value_type = edit.table
        value = TableParameterValue(id=edit.parameter_id, rows=())
        kind = "table_added"
        action = (
            "Add explicitly initialized rows; missing non-key cells remain unknown."
        )
    definitions[edit.parameter_id] = ParameterDefinition(
        id=edit.parameter_id, value_type=value_type
    )
    values[edit.parameter_id] = value
    return StructureColumnImpact(
        parameter_id=edit.parameter_id,
        kind=kind,
        affected_rows=0,
        consumer_action=action,
    )
