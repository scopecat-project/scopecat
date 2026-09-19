"""Transaction-local executable setup persistence."""

from __future__ import annotations

import sqlite3
from typing import cast

from scopecat.records.setup import (
    ActiveSetupView,
    SetupActivationOperation,
    SetupActivationRecord,
    SetupRevision,
    SetupRevisionRef,
)


class SQLiteSetupRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def read_revision(self, revision_id: str) -> SetupRevision:
        row = cast(
            "sqlite3.Row | None",
            self._connection.execute(
                "SELECT record_json FROM setup_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(revision_id)
        return SetupRevision.model_validate_json(cast("str", row[0]))

    def list_revisions(self) -> tuple[SetupRevision, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            self._connection.execute(
                "SELECT record_json FROM setup_revisions ORDER BY rowid DESC"
            ).fetchall(),
        )
        return tuple(
            SetupRevision.model_validate_json(cast("str", row[0])) for row in rows
        )

    def read_current(self) -> ActiveSetupView | None:
        row = cast(
            "sqlite3.Row | None",
            self._connection.execute(
                "SELECT record_json FROM setup_activations "
                "ORDER BY generation DESC LIMIT 1"
            ).fetchone(),
        )
        if row is None:
            return None
        activation = SetupActivationRecord.model_validate_json(cast("str", row[0]))
        return ActiveSetupView(
            revision=self.read_revision(activation.revision.revision_id),
            activation=activation,
        )

    def save_revision(self, revision: SetupRevision) -> SetupRevision:
        try:
            existing = self.read_revision(revision.id)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.model_dump(exclude={"recorded_at"}) != revision.model_dump(
                exclude={"recorded_at"}
            ):
                raise ValueError(
                    "setup revision ID already has different content or provenance"
                )
            return existing
        self._connection.execute(
            "INSERT INTO setup_revisions(revision_id, record_json) VALUES (?, ?)",
            (revision.id, revision.model_dump_json()),
        )
        return revision

    def read_activation_operation(
        self, operation_id: str
    ) -> SetupActivationOperation | None:
        row = cast(
            "sqlite3.Row | None",
            self._connection.execute(
                "SELECT record_json FROM setup_activation_operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone(),
        )
        return (
            None
            if row is None
            else SetupActivationOperation.model_validate_json(cast("str", row[0]))
        )

    def activate(
        self,
        *,
        revision: SetupRevisionRef,
        expected_generation: int,
        operation_id: str,
        intent_hash: str,
        actor: str,
        note: str,
    ) -> ActiveSetupView:
        prior = self.read_activation_operation(operation_id)
        if prior is not None:
            if (
                prior.intent_hash != intent_hash
                or prior.revision != revision
                or prior.expected_generation != expected_generation
                or prior.actor != actor
                or prior.note != note
            ):
                raise ValueError("setup activation operation ID has different intent")
            return prior.result
        selected = self.read_revision(revision.revision_id)
        if selected.ref != revision:
            raise ValueError("setup revision does not match exact content")
        current = self.read_current()
        generation = current.activation.generation if current is not None else 0
        if generation != expected_generation:
            raise ValueError("setup activation generation changed")
        activation = SetupActivationRecord(
            generation=generation + 1,
            revision=revision,
            previous_revision=current.revision.ref if current is not None else None,
            actor=actor,
            note=note,
        )
        result = ActiveSetupView(revision=selected, activation=activation)
        operation = SetupActivationOperation(
            operation_id=operation_id,
            intent_hash=intent_hash,
            revision=revision,
            expected_generation=expected_generation,
            actor=actor,
            note=note,
            result=result,
        )
        self._connection.execute(
            "INSERT INTO setup_activations(generation, revision_id, record_json) "
            "VALUES (?, ?, ?)",
            (activation.generation, selected.id, activation.model_dump_json()),
        )
        self._connection.execute(
            "INSERT INTO setup_activation_operations"
            "(operation_id, generation, record_json) "
            "VALUES (?, ?, ?)",
            (operation_id, activation.generation, operation.model_dump_json()),
        )
        return result
