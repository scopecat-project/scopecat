"""Immutable target revisions validated against local retained sample content."""

import sqlite3
from datetime import UTC, datetime
from typing import cast

from scopecat.records.sample import SampleRevision
from scopecat.records.scientific_scope import MeasurementTarget
from scopecat.records.target_catalog import (
    TargetCatalogPage,
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevision,
    TargetRevisionDraft,
    TargetRevisionRef,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


class TargetCatalogStore:
    def __init__(self, database: SQLiteDatabase, *, catalog_id: str) -> None:
        self.database = database
        self.catalog_id = catalog_id

    def _require_catalog(self, catalog_id: str) -> None:
        if catalog_id != self.catalog_id:
            raise BackendConflict("target reference belongs to another catalog")

    def list(self, *, limit: int = 100, before: int | None = None) -> TargetCatalogPage:
        with self.database.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT t.rowid AS sequence, r.revision_json "
                    "FROM measurement_targets t JOIN measurement_target_revisions r "
                    "ON r.target_id=t.target_id AND r.revision=t.head_revision "
                    "WHERE (? IS NULL OR t.rowid<?) ORDER BY t.rowid DESC LIMIT ?",
                    (before, before, limit + 1),
                ).fetchall(),
            )
        return TargetCatalogPage(
            items=tuple(_revision(row) for row in rows[:limit]),
            next_cursor=cast("int", rows[limit - 1]["sequence"])
            if len(rows) > limit
            else None,
        )

    def get(self, target_id: str, *, revision: int | None = None) -> TargetRevision:
        with self.database.read_connection() as connection:
            return _get(connection, target_id, revision=revision)

    def resolve(self, ref: TargetRevisionRef) -> TargetRevision:
        self._require_catalog(ref.catalog_id)
        value = self.get(ref.target_id, revision=ref.revision)
        if value.ref != ref:
            raise BackendConflict("target reference does not match retained revision")
        return value

    def create(self, command: TargetCreateCommand) -> TargetRevision:
        self._require_catalog(command.catalog_id)
        with self.database.write_transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM measurement_targets WHERE target_id=?",
                    (command.target_id,),
                ).fetchone()
                is not None
            ):
                raise BackendConflict("target already exists; read its latest revision")
            _validate_members(connection, command.draft.content)
            connection.execute(
                "INSERT INTO measurement_targets VALUES (?,1)", (command.target_id,)
            )
            return self._append(
                connection, command.target_id, revision=1, draft=command.draft
            )

    def revise(self, command: TargetReviseCommand) -> TargetRevision:
        self._require_catalog(command.expected.catalog_id)
        with self.database.write_transaction() as connection:
            current = _get(connection, command.expected.target_id)
            if current.ref != command.expected:
                raise BackendConflict("target changed; reload before revising")
            _validate_members(connection, command.draft.content)
            revision = current.ref.revision + 1
            result = self._append(
                connection,
                current.ref.target_id,
                revision=revision,
                draft=command.draft,
            )
            connection.execute(
                "UPDATE measurement_targets SET head_revision=? WHERE target_id=?",
                (revision, current.ref.target_id),
            )
            return result

    def _append(
        self,
        connection: sqlite3.Connection,
        target_id: str,
        *,
        revision: int,
        draft: TargetRevisionDraft,
    ) -> TargetRevision:
        value = TargetRevision(
            ref=TargetRevisionRef(
                catalog_id=self.catalog_id,
                target_id=target_id,
                revision=revision,
                content_hash=draft.content.content_hash,
            ),
            name=draft.name,
            description=draft.description,
            content=draft.content,
            actor=draft.actor,
            note=draft.note,
            recorded_at=datetime.now(UTC),
        )
        connection.execute(
            "INSERT INTO measurement_target_revisions VALUES (?,?,?)",
            (target_id, revision, value.model_dump_json()),
        )
        return value


def _revision(row: sqlite3.Row) -> TargetRevision:
    return TargetRevision.model_validate_json(cast("str", row["revision_json"]))


def _get(
    connection: sqlite3.Connection, target_id: str, *, revision: int | None = None
) -> TargetRevision:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT r.revision_json FROM measurement_target_revisions r "
            "JOIN measurement_targets t ON r.target_id=t.target_id "
            "WHERE t.target_id=? AND r.revision=COALESCE(?,t.head_revision)",
            (target_id, revision),
        ).fetchone(),
    )
    if row is None:
        raise BackendNotFound("target revision not found")
    return _revision(row)


def _validate_members(
    connection: sqlite3.Connection, target: MeasurementTarget
) -> None:
    entities: dict[str, set[str]] = {}
    for member in target.members:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT revision_json FROM sample_revisions "
                "WHERE sample_id=? AND revision=?",
                (member.sample_id, member.revision),
            ).fetchone(),
        )
        if row is None:
            raise BackendNotFound(
                f"target member {member.id!r} sample revision not found"
            )
        revision = SampleRevision.model_validate_json(cast("str", row["revision_json"]))
        if revision.content_hash != member.content_hash:
            raise BackendConflict(f"target member {member.id!r} sample hash changed")
        topology = revision.content.topology
        entities[member.id] = (
            {entity.id for entity in topology.entities}
            if topology is not None
            else set()
        )
    for link in target.connections:
        for endpoint in link.endpoints:
            if endpoint.entity_id not in entities[endpoint.member_id]:
                raise BackendConflict(
                    f"target connection {link.id!r} references unknown entity "
                    f"{endpoint.member_id}/{endpoint.entity_id}"
                )
