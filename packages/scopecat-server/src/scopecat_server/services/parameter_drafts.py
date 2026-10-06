"""Recover parameter input independently from immutable scientific revisions."""

import json
import sqlite3
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.daemon.wire import ParameterBranchCommitCommand, ParameterSaveCommand
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Float, Int, Scalar, Table
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.records.parameter import ParameterAtomValue, ParameterSnapshot
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.parameter_update import ReplaceParameter
from scopecat.records.scientific_selection import ParameterConfiguration

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.parameter_drafts import (
    ParameterDraft,
    ParameterDraftAtom,
    ParameterDraftCommit,
    ParameterDraftFrozen,
    ParameterDraftInput,
    ParameterDraftPage,
    ParameterDraftSave,
    ParameterDraftStart,
    ParameterDraftValue,
    ParameterDraftView,
)
from scopecat_server.storage.sqlite import parameter_drafts as drafts
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.parameter_branches import ParameterBranchRepository
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)

from .config import ConfigService


def _raw(value: ParameterAtomValue) -> ParameterDraftAtom:
    if isinstance(value, Quantity):
        return ParameterDraftAtom(text=str(value.value), unit=str(value.unit))
    if isinstance(value, str):
        return ParameterDraftAtom(text=value)
    if isinstance(value, bool):
        return ParameterDraftAtom(text="true" if value else "false")
    if isinstance(value, int | float):
        return ParameterDraftAtom(text=str(value))
    return ParameterDraftAtom(text=value.model_dump_json())


def _initial(base: ParameterRevision, actor: str) -> ParameterDraftInput:
    return ParameterDraftInput(
        name=f"{base.id} (revised)",
        actor=actor,
        values=[
            ParameterDraftValue(id=value.id, shape="scalar", value=_raw(value.value))
            if value.shape == "scalar"
            else ParameterDraftValue(
                id=value.id,
                shape="table",
                rows=[
                    {key: _raw(atom) for key, atom in row.items()} for row in value.rows
                ],
            )
            for value in base.parameters.values
        ],
    )


def _atom(raw: ParameterDraftAtom, scalar: Scalar) -> object:
    atom = scalar.atom
    if isinstance(atom, QuantityType):
        return {"value": float(raw.text), "unit": raw.unit}
    if isinstance(atom, Float):
        return float(raw.text)
    if isinstance(atom, Int):
        number = Decimal(raw.text)
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError("Enter a complete integer")
        limit = sys.get_int_max_str_digits()
        if limit and number.adjusted() >= limit:
            raise ValueError("Integer exceeds the supported text conversion limit")
        return int(number)
    # String input remains literal, while booleans and entities use JSON text.
    from scopecat.kernel.value_types import String

    if isinstance(atom, String):
        return raw.text
    return cast("object", json.loads(raw.text))


def _parameters(
    base: ParameterRevision, input: ParameterDraftInput
) -> ParameterSnapshot:
    definitions = {
        definition.id: definition.value_type for definition in base.catalog.definitions
    }
    values: list[dict[str, object]] = []
    for value in input.values:
        definition = definitions[value.id]
        if (
            value.shape == "scalar"
            and isinstance(definition, Scalar)
            and value.value is not None
            and not value.rows
        ):
            values.append(
                {
                    "id": value.id,
                    "shape": "scalar",
                    "value": _atom(value.value, definition),
                }
            )
        elif (
            value.shape == "table"
            and isinstance(definition, Table)
            and value.value is None
        ):
            columns = {column.id: column.value_type for column in definition.columns}
            values.append(
                {
                    "id": value.id,
                    "shape": "table",
                    "rows": [
                        {key: _atom(atom, columns[key]) for key, atom in row.items()}
                        for row in value.rows
                    ],
                }
            )
        else:
            raise ValueError(f"Complete {value.id} before saving a parameter version")
    return ParameterSnapshot.model_validate(
        {"id": base.parameters.id, "values": values}
    )


class ParameterDraftService:
    def __init__(self, database: SQLiteDatabase, config: ConfigService) -> None:
        self._database = database
        self._config = config

    def _head(self, connection: sqlite3.Connection, draft_id: UUID) -> ParameterDraft:
        draft = drafts.head(connection, draft_id)
        if draft is None:
            raise BackendNotFound("Parameter draft was not found")
        return draft

    def _base(
        self, connection: sqlite3.Connection, ref: ParameterRevisionRef
    ) -> ParameterRevision:
        try:
            base = ParameterRevisionRepository(connection).get(ref.revision_id)
        except KeyError as error:
            raise BackendNotFound("Parameter baseline was not found") from error
        if base.ref != ref:
            raise BackendConflict(
                "Parameter baseline changed; retain input and review its source"
            )
        return base

    def _view(
        self, connection: sqlite3.Connection, draft: ParameterDraft
    ) -> ParameterDraftView:
        head = self._head(connection, draft.draft_id)
        changed = False
        if draft.state == "saved" and draft.input.branch:
            try:
                generation = (
                    ParameterBranchRepository(connection)
                    .get(draft.input.branch)
                    .generation
                )
            except KeyError:
                generation = 0
            changed = draft.input.branch_generation != generation
        return ParameterDraftView(
            draft=draft, head_revision=head.revision, branch_changed=changed
        )

    def start(self, command: ParameterDraftStart) -> ParameterDraftView:
        with self._database.write_transaction() as connection:
            base = self._base(connection, command.base)
            replay = drafts.head(connection, command.draft_id)
            if replay is not None:
                if replay.base != command.base:
                    raise BackendConflict("Draft identity belongs to another baseline")
                return self._view(connection, replay)
            if command.copy_from is None:
                active = drafts.active(connection, base.id, command.working_branch)
                if active is not None:
                    return self._view(connection, active)
                input = _initial(base, command.actor)
                if command.working_branch:
                    try:
                        branch = ParameterBranchRepository(connection).get(
                            command.working_branch
                        )
                    except KeyError as error:
                        raise BackendNotFound(
                            "Parameter branch was not found"
                        ) from error
                    if (
                        branch.generation != command.branch_generation
                        or branch.revision != base.ref
                    ):
                        raise BackendConflict(
                            "Branch changed; refresh before opening a work table"
                        )
                    input = input.model_copy(
                        update={
                            "branch": branch.name,
                            "branch_generation": branch.generation,
                        }
                    )
            else:
                if command.working_branch:
                    raise BackendConflict(
                        "An explicit copy is independent of the working branch"
                    )
                source = (
                    drafts.revision(
                        connection, command.copy_from, command.copy_revision
                    )
                    if command.copy_revision is not None
                    else self._head(connection, command.copy_from)
                )
                if source is None:
                    raise BackendNotFound("Parameter draft revision was not found")
                if source.base != command.base:
                    raise BackendConflict("Copy must use the same parameter baseline")
                input = source.input
            draft = drafts.append(
                connection,
                ParameterDraft(
                    draft_id=command.draft_id,
                    revision=0,
                    working_branch=command.working_branch,
                    base=base.ref,
                    input=input,
                    state="saved",
                    created_at=datetime.now(UTC),
                ),
            )
            return self._view(connection, draft)

    def read(self, draft_id: UUID) -> ParameterDraftView:
        with self._database.read_transaction() as connection:
            return self._view(connection, self._head(connection, draft_id))

    def save(self, draft_id: UUID, command: ParameterDraftSave) -> ParameterDraftView:
        with self._database.write_transaction() as connection:
            head = self._head(connection, draft_id)
            if head.working_branch and command.input.branch != head.working_branch:
                raise BackendConflict(
                    "A working table retains its branch; "
                    "create a separate copy to change destinations"
                )
            state = (
                "conflict"
                if head.revision != command.expected_revision or head.state != "saved"
                else ("discarded" if command.discard else "saved")
            )
            saved = drafts.append(
                connection,
                head.model_copy(
                    update={
                        "input": command.input,
                        "state": state,
                        "result": None,
                        "completed_from": None,
                    }
                ),
            )
            return self._view(connection, saved)

    def commit(
        self, draft_id: UUID, command: ParameterDraftCommit
    ) -> ParameterDraftView:
        with self._database.write_transaction() as connection:
            head = self._head(connection, draft_id)
            if (
                head.state == "completed"
                and head.completed_from == command.expected_revision
            ):
                return self._view(connection, head)
            if head.state != "saved" or head.revision != command.expected_revision:
                raise BackendConflict(
                    "Parameter draft changed; review before saving a version"
                )
            base = self._base(connection, head.base)
            if self._view(connection, head).branch_changed:
                raise BackendConflict(
                    "Branch changed; review latest branch head before saving"
                )
            input = head.input
            try:
                save = ParameterSaveCommand(
                    revision_id=input.name.strip(),
                    actor=input.actor,
                    note=input.note,
                    catalog=base.catalog,
                    parameters=_parameters(base, input),
                )
            except (ValueError, KeyError, InvalidOperation) as error:
                raise BackendConflict(
                    f"Complete parameter input before saving: {error}"
                ) from error
            if input.branch:
                assert input.branch_generation is not None
                result = self._config.commit_parameter_branch_in_transaction(
                    connection,
                    ParameterBranchCommitCommand(
                        name=input.branch,
                        expected_generation=input.branch_generation,
                        source=save,
                        actor=input.actor,
                        note=input.note,
                    ),
                ).revision
            else:
                result = self._config.save_parameters_in_transaction(
                    connection, save
                ).ref
            completed = drafts.append(
                connection,
                head.model_copy(
                    update={
                        "state": "completed",
                        "completed_from": head.revision,
                        "result": result,
                    }
                ),
            )
            return self._view(connection, completed)

    def freeze(
        self, draft_id: UUID, command: ParameterDraftCommit
    ) -> ParameterDraftFrozen:
        """Capture working values as run-only overrides without writes."""
        with self._database.read_transaction() as connection:
            head = self._head(connection, draft_id)
            if head.state != "saved" or head.revision != command.expected_revision:
                raise BackendConflict("Working input changed; review before using it")
            if self._view(connection, head).branch_changed:
                raise BackendConflict(
                    "Branch changed; review latest branch head "
                    "before using working input"
                )
            base = self._base(connection, head.base)
            try:
                parameters = _parameters(base, head.input)
                problems = validate_parameter_snapshot(
                    base.catalog, parameters, allow_missing=True
                )
                if problems:
                    raise ValueError(
                        "Parameter values do not match their declared types"
                    )
                before = {value.id: value for value in base.parameters.values}
                after = {value.id: value for value in parameters.values}
                if before.keys() - after.keys():
                    raise ValueError(
                        "Save a parameter checkpoint to mark a whole parameter unknown"
                    )
                overrides = tuple(
                    ReplaceParameter(value=value)
                    for value in parameters.values
                    if before.get(value.id) != value
                )
                configuration = ParameterConfiguration(
                    ref=base.ref, overrides=overrides
                )
            except (ValueError, KeyError, InvalidOperation) as error:
                raise BackendConflict(
                    f"Complete working input before preview: {error}"
                ) from error
            return ParameterDraftFrozen(
                draft_id=draft_id, revision=head.revision, configuration=configuration
            )

    def history(
        self, base_id: str, before: int | None = None, limit: int = 50
    ) -> ParameterDraftPage:
        with self._database.read_transaction() as connection:
            return drafts.history(connection, base_id, before, limit)
