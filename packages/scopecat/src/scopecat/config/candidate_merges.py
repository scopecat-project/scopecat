"""Deterministic common-base composition of parameter proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.config.parameter_updates import parameter_cell_edits
from scopecat.config.validation import (
    ParameterValueValidationError,
    validate_parameter_representation,
)
from scopecat.kernel.content_identity import canonical_json
from scopecat.kernel.errors import CheckFailed, Conflict
from scopecat.kernel.problems import Problem, ProblemPhase, model_location, problem
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_identity import scalar_identity, scalar_values_equal
from scopecat.kernel.value_types import Scalar, Table
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    StoredParameterValue,
    TableParameterValue,
)
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterValueDelta,
)

MAX_COMMON_BASE_PROPOSALS = 200


@dataclass(frozen=True, slots=True)
class CommonBaseCandidateMergeResult:
    """One fully materialized candidate and its canonical combined deltas."""

    config: ConfigProfileSnapshot
    deltas: tuple[ParameterValueDelta, ...]

    @property
    def content_hash(self) -> str:
        return config_content_hash(self.config)


def merge_common_base_parameter_proposals(
    proposals: Sequence[ParameterChangeProposal],
    *,
    base_config: ConfigProfileSnapshot,
    candidate_id: str,
) -> CommonBaseCandidateMergeResult:
    """Compose whole-value deltas through catalog-aware three-way cell merge.

    Each proposal is one branch from the same authoritative base. Scalars and
    tables without a primary key remain atomic values. A keyed table is merged
    by semantic primary-key identity and then by non-key cell. Proposal order
    cannot affect the resulting snapshot or delta order.
    """

    if not 1 <= len(proposals) <= MAX_COMMON_BASE_PROPOSALS:
        raise _merge_check(
            "parameter_merge.proposal_count",
            "common-base merge requires between 1 and "
            f"{MAX_COMMON_BASE_PROPOSALS} proposals",
            path=("proposals",),
        )
    if not candidate_id.strip():
        raise _merge_check(
            "parameter_merge.candidate_id_empty",
            "merged candidate id must be non-empty",
            path=("candidate_id",),
        )

    canonical = tuple(sorted(proposals, key=lambda item: (item.source_run_id, item.id)))
    proposal_keys = tuple((item.source_run_id, item.id) for item in canonical)
    if len(proposal_keys) != len(set(proposal_keys)):
        raise _merge_check(
            "parameter_merge.duplicate_proposal",
            "common-base merge proposal identities must be unique",
            path=("proposals",),
        )

    base_hash = config_content_hash(base_config)
    base_values = {value.id: value for value in base_config.parameter_snapshot.values}
    definitions = {
        definition.id: definition
        for definition in base_config.parameter_catalog.definitions
    }
    branches_by_parameter: dict[str, list[StoredParameterValue]] = {}
    for proposal_index, proposal in enumerate(canonical):
        _validate_proposal_base(
            proposal,
            proposal_index=proposal_index,
            base_config=base_config,
            base_hash=base_hash,
        )
        for delta in proposal.deltas:
            definition = definitions.get(delta.parameter_id)
            base_value = base_values.get(delta.parameter_id)
            if definition is None or base_value is None:
                raise _merge_check(
                    "parameter_merge.unknown_parameter",
                    f"proposal references unknown parameter {delta.parameter_id!r}",
                    path=("proposals", proposal_index, "deltas", delta.parameter_id),
                    details={"parameter_id": delta.parameter_id},
                )
            normalized_base = _validate_parameter_representation(definition, base_value)
            normalized_before = _validate_parameter_representation(
                definition, delta.before
            )
            normalized_after = _validate_parameter_representation(
                definition, delta.after
            )
            if not _parameter_values_equal(
                normalized_before,
                normalized_base,
                definition=definition,
            ):
                raise _merge_conflict(
                    "parameter_merge.delta_base_mismatch",
                    "proposal delta before value does not match the common base",
                    parameter_id=delta.parameter_id,
                    details={
                        "source_run_id": proposal.source_run_id,
                        "proposal_id": proposal.id,
                    },
                )
            if _parameter_values_equal(
                normalized_after,
                normalized_base,
                definition=definition,
            ):
                # Exact cell no-ops (including keyed-table row reorder) do not
                # participate in composition. Quantity representation edits do.
                continue
            branches_by_parameter.setdefault(delta.parameter_id, []).append(
                normalized_after
            )

    merged_by_id: dict[str, StoredParameterValue] = {}
    deltas: list[ParameterValueDelta] = []
    for parameter_id in sorted(branches_by_parameter):
        definition = definitions[parameter_id]
        base_value = _validate_parameter_representation(
            definition,
            base_values[parameter_id],
        )
        merged = _merge_parameter_value(
            definition,
            base=base_value,
            branches=tuple(branches_by_parameter[parameter_id]),
        )
        if _parameter_values_equal(merged, base_value, definition=definition):
            continue
        merged_by_id[parameter_id] = merged
        deltas.append(
            ParameterValueDelta(
                parameter_id=parameter_id,
                before=base_values[parameter_id],
                after=merged,
                cells=parameter_cell_edits(
                    definition, base_values[parameter_id], merged
                ),
            )
        )

    if not deltas:
        raise _merge_check(
            "parameter_merge.no_effective_changes",
            "common-base proposals contain no effective parameter changes",
            path=("proposals",),
        )

    candidate_snapshot = ParameterSnapshot(
        id=f"{candidate_id}.parameters",
        values=tuple(
            merged_by_id.get(value.id, value)
            for value in base_config.parameter_snapshot.values
        ),
    )
    problems = validate_parameter_snapshot(
        base_config.parameter_catalog,
        candidate_snapshot,
    )
    if problems:
        raise CheckFailed(problems)
    return CommonBaseCandidateMergeResult(
        config=base_config.model_copy(
            update={
                "id": candidate_id,
                "parameter_snapshot": candidate_snapshot,
            },
            deep=True,
        ),
        deltas=tuple(deltas),
    )


def _validate_proposal_base(
    proposal: ParameterChangeProposal,
    *,
    proposal_index: int,
    base_config: ConfigProfileSnapshot,
    base_hash: str,
) -> None:
    if (
        proposal.base_config_id == base_config.id
        and proposal.base_config_content_hash == base_hash
    ):
        return
    raise _merge_conflict(
        "parameter_merge.proposal_base_mismatch",
        "parameter proposal was derived from a different common base",
        parameter_id=None,
        path=("proposals", proposal_index),
        details={
            "source_run_id": proposal.source_run_id,
            "proposal_id": proposal.id,
            "expected_config_id": base_config.id,
            "actual_config_id": proposal.base_config_id,
            "expected_content_hash": base_hash,
            "actual_content_hash": proposal.base_config_content_hash,
        },
    )


def _validate_parameter_representation(
    definition: ParameterDefinition,
    value: StoredParameterValue,
) -> StoredParameterValue:
    try:
        return validate_parameter_representation(definition, value)
    except ParameterValueValidationError as error:
        raise _merge_check(
            "parameter_merge.invalid_parameter_value",
            f"parameter proposal value is invalid: {error}",
            path=("parameters", definition.id),
            details={"parameter_id": definition.id},
        ) from error


def _merge_parameter_value(
    definition: ParameterDefinition,
    *,
    base: StoredParameterValue,
    branches: tuple[StoredParameterValue, ...],
) -> StoredParameterValue:
    value_type = definition.value_type
    if isinstance(value_type, Scalar):
        assert isinstance(base, ScalarParameterValue)
        selected = tuple(cast("ScalarParameterValue", item) for item in branches)
        if not _all_representations_equal(
            tuple(item.value for item in selected),
        ):
            raise _merge_conflict(
                "parameter_merge.atomic_value_conflict",
                "scalar branches changed the same parameter differently",
                parameter_id=definition.id,
            )
        return _canonical_model(selected)

    assert isinstance(value_type, Table)
    assert isinstance(base, TableParameterValue)
    tables = tuple(cast("TableParameterValue", item) for item in branches)
    if not value_type.primary_key:
        if not all(
            _table_values_equal(item, tables[0], table_type=value_type)
            for item in tables[1:]
        ):
            raise _merge_conflict(
                "parameter_merge.atomic_table_conflict",
                "table without a primary key was changed differently",
                parameter_id=definition.id,
            )
        return _canonical_model(tables)
    return _merge_keyed_table(
        parameter_id=definition.id,
        table_type=value_type,
        base=base,
        branches=tables,
    )


def _merge_keyed_table(
    *,
    parameter_id: str,
    table_type: Table,
    base: TableParameterValue,
    branches: tuple[TableParameterValue, ...],
) -> TableParameterValue:
    columns = tuple(column.id for column in table_type.columns)
    non_key_columns = tuple(
        column for column in columns if column not in table_type.primary_key
    )
    base_rows = _rows_by_key(
        parameter_id=parameter_id,
        table_type=table_type,
        value=base,
    )
    branch_rows = tuple(
        _rows_by_key(
            parameter_id=parameter_id,
            table_type=table_type,
            value=branch,
        )
        for branch in branches
    )

    merged_rows: list[dict[str, ParameterAtomValue]] = []
    for base_key, base_row in base_rows.items():
        deleted = False
        edits: list[Mapping[str, ParameterAtomValue]] = []
        for rows in branch_rows:
            row = rows.get(base_key)
            if row is None:
                deleted = True
            elif not _rows_equal(row, base_row, columns=columns):
                edits.append(row)
        if deleted and edits:
            raise _merge_conflict(
                "parameter_merge.row_delete_edit_conflict",
                "one branch deleted a row that another branch edited",
                parameter_id=parameter_id,
                details={"primary_key": _key_details(base_row, table_type)},
            )
        if deleted:
            continue
        selected = dict(base_row)
        for column in non_key_columns:
            changed = tuple(
                row[column]
                for row in edits
                if not _atoms_equal(row[column], base_row[column])
            )
            if not changed:
                continue
            if not _all_representations_equal(changed):
                equivalent = all(
                    scalar_values_equal(changed[0], item) for item in changed[1:]
                )
                raise _merge_conflict(
                    "parameter_merge.table_cell_representation_conflict"
                    if equivalent
                    else "parameter_merge.table_cell_conflict",
                    "branches proposed physically equivalent but different cell "
                    "representations; choose explicitly"
                    if equivalent
                    else "branches changed the same table cell physically differently",
                    parameter_id=parameter_id,
                    details={
                        "primary_key": _key_details(base_row, table_type),
                        "column_id": column,
                        "base": _atom_wire(base_row[column]),
                        "changes": [_atom_wire(item) for item in changed],
                        "change_kind": "representation" if equivalent else "physical",
                    },
                )
            selected[column] = _canonical_atom(changed)
        merged_rows.append(_ordered_row(selected, columns=columns))

    inserted_keys: set[tuple[tuple[object, ...], ...]] = set()
    for rows in branch_rows:
        inserted_keys.update(rows)
    inserted_keys.difference_update(base_rows)
    for key in sorted(inserted_keys, key=_key_sort_token):
        inserted = tuple(rows[key] for rows in branch_rows if key in rows)
        first = inserted[0]
        if not all(_rows_equal(row, first, columns=columns) for row in inserted[1:]):
            raise _merge_conflict(
                "parameter_merge.row_insert_conflict",
                "branches inserted different rows with the same primary key",
                parameter_id=parameter_id,
                details={"primary_key": _key_details(first, table_type)},
            )
        selected_row = min(inserted, key=_row_sort_token)
        merged_rows.append(_ordered_row(selected_row, columns=columns))

    return TableParameterValue(id=parameter_id, rows=tuple(merged_rows))


def _rows_by_key(
    *,
    parameter_id: str,
    table_type: Table,
    value: TableParameterValue,
) -> dict[tuple[tuple[object, ...], ...], Mapping[str, ParameterAtomValue]]:
    selected: dict[
        tuple[tuple[object, ...], ...], Mapping[str, ParameterAtomValue]
    ] = {}
    for row in value.rows:
        key = tuple(scalar_identity(row[column]) for column in table_type.primary_key)
        if key in selected:
            raise _merge_check(
                "parameter_merge.duplicate_primary_key",
                "parameter table contains duplicate semantic primary keys",
                path=("parameters", parameter_id, "rows"),
                details={"parameter_id": parameter_id},
            )
        selected[key] = row
    return selected


def _parameter_values_equal(
    left: StoredParameterValue,
    right: StoredParameterValue,
    *,
    definition: ParameterDefinition,
) -> bool:
    value_type = definition.value_type
    if isinstance(value_type, Scalar):
        return (
            isinstance(left, ScalarParameterValue)
            and isinstance(right, ScalarParameterValue)
            and _atoms_equal(left.value, right.value)
        )
    return (
        isinstance(left, TableParameterValue)
        and isinstance(right, TableParameterValue)
        and _table_values_equal(left, right, table_type=value_type)
    )


def _table_values_equal(
    left: TableParameterValue,
    right: TableParameterValue,
    *,
    table_type: Table,
) -> bool:
    columns = tuple(column.id for column in table_type.columns)
    if not table_type.primary_key:
        return len(left.rows) == len(right.rows) and all(
            _rows_equal(left_row, right_row, columns=columns)
            for left_row, right_row in zip(left.rows, right.rows, strict=True)
        )
    left_rows = _rows_by_key(
        parameter_id=left.id,
        table_type=table_type,
        value=left,
    )
    right_rows = _rows_by_key(
        parameter_id=right.id,
        table_type=table_type,
        value=right,
    )
    return left_rows.keys() == right_rows.keys() and all(
        _rows_equal(left_rows[key], right_rows[key], columns=columns)
        for key in left_rows
    )


def _rows_equal(
    left: Mapping[str, ParameterAtomValue],
    right: Mapping[str, ParameterAtomValue],
    *,
    columns: tuple[str, ...],
) -> bool:
    return all(_atoms_equal(left[column], right[column]) for column in columns)


def _all_representations_equal(values: tuple[ParameterAtomValue, ...]) -> bool:
    first = values[0]
    return all(_atoms_equal(first, item) for item in values[1:])


def _canonical_atom(values: tuple[ParameterAtomValue, ...]) -> ParameterAtomValue:
    return min(values, key=_atom_sort_token)


def _canonical_model[ModelT: BaseModel](values: tuple[ModelT, ...]) -> ModelT:
    return min(values, key=lambda item: canonical_json(item.model_dump(mode="json")))


def _atom_wire(value: ParameterAtomValue) -> object:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def _atoms_equal(left: ParameterAtomValue, right: ParameterAtomValue) -> bool:
    # Physical equivalence does not erase an explicitly proposed representation.
    if isinstance(left, Quantity) or isinstance(right, Quantity):
        return left == right
    return scalar_values_equal(left, right)


def _atom_sort_token(value: ParameterAtomValue) -> str:
    return canonical_json(_atom_wire(value))


def _key_sort_token(key: tuple[tuple[object, ...], ...]) -> str:
    return canonical_json(cast("object", key))


def _row_sort_token(row: Mapping[str, ParameterAtomValue]) -> str:
    return canonical_json(
        {
            column: (
                value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            )
            for column, value in row.items()
        }
    )


def _ordered_row(
    row: Mapping[str, ParameterAtomValue],
    *,
    columns: tuple[str, ...],
) -> dict[str, ParameterAtomValue]:
    return {column: row[column] for column in columns}


def _key_details(
    row: Mapping[str, ParameterAtomValue],
    table_type: Table,
) -> dict[str, object]:
    details: dict[str, object] = {}
    for column in table_type.primary_key:
        value = row[column]
        details[column] = (
            value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        )
    return details


def _merge_check(
    code: str,
    message: str,
    *,
    path: tuple[str | int, ...],
    details: dict[str, object] | None = None,
) -> CheckFailed:
    return CheckFailed(
        [
            problem(
                code,
                message,
                phase=ProblemPhase.CONFIGURATION,
                location=model_location("merged_candidate_config", *path),
                details=details,
            )
        ]
    )


def _merge_conflict(
    code: str,
    message: str,
    *,
    parameter_id: str | None,
    path: tuple[str | int, ...] | None = None,
    details: dict[str, object] | None = None,
) -> Conflict:
    selected_path = (
        path
        if path is not None
        else ("parameters", parameter_id if parameter_id is not None else "base")
    )
    return Conflict(
        [
            Problem(
                code=code,
                phase=ProblemPhase.CONFIGURATION,
                message=message,
                location=model_location("merged_candidate_config", *selected_path),
                details={} if details is None else details,
            )
        ]
    )


__all__ = [
    "MAX_COMMON_BASE_PROPOSALS",
    "CommonBaseCandidateMergeResult",
    "merge_common_base_parameter_proposals",
]
