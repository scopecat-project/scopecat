"""Immutable descriptive history; observations never imply current apparatus state."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from typing import cast

from scopecat.records.apparatus_history import (
    MAX_APPARATUS_ATTACHMENT_BYTES,
    ApparatusAttachment,
    ApparatusObjectContent,
    ApparatusObjectCreate,
    ApparatusObjectPage,
    ApparatusObjectRef,
    ApparatusObjectRevise,
    ApparatusObjectRevision,
    ApparatusObservation,
    ApparatusObservationCreate,
    ApparatusObservationPage,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.object_store import (
    ImmutableObjectStore,
)


class ApparatusHistoryStore:
    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        catalog_id: str,
        objects: ImmutableObjectStore,
    ) -> None:
        self.database = database
        self.catalog_id = catalog_id
        self.objects = objects

    def _require_catalog(self, catalog_id: str) -> None:
        if catalog_id != self.catalog_id:
            raise BackendConflict("apparatus reference belongs to another catalog")

    def list_objects(
        self, *, query: str = "", limit: int = 100, before: int | None = None
    ) -> ApparatusObjectPage:
        # instr treats %, _ and backslashes literally, unlike LIKE.
        with self.database.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT o.rowid AS sequence, r.revision_json "
                    "FROM apparatus_objects o JOIN apparatus_object_revisions r "
                    "ON r.object_id=o.object_id AND r.revision=o.head_revision "
                    "WHERE (? IS NULL OR o.rowid<?) AND ("
                    "instr(lower(o.object_id),lower(?))>0 OR "
                    "instr(lower(json_extract(r.revision_json,'$.content.name')),"
                    "lower(?))>0 OR "
                    "EXISTS (SELECT 1 FROM "
                    "json_each(r.revision_json,'$.content.aliases') "
                    "WHERE instr(lower(value),lower(?))>0)) "
                    "ORDER BY o.rowid DESC LIMIT ?",
                    (before, before, query, query, query, limit + 1),
                ).fetchall(),
            )
        return ApparatusObjectPage(
            items=tuple(_revision(row) for row in rows[:limit]),
            next_cursor=_cursor(rows, limit),
        )

    def get_object(
        self, object_id: str, *, revision: int | None = None
    ) -> ApparatusObjectRevision:
        with self.database.read_connection() as connection:
            return _get(connection, object_id, revision)

    def resolve_object(self, ref: ApparatusObjectRef) -> ApparatusObjectRevision:
        with self.database.read_connection() as connection:
            return self._resolve(connection, ref)

    def _resolve(
        self, connection: sqlite3.Connection, ref: ApparatusObjectRef
    ) -> ApparatusObjectRevision:
        self._require_catalog(ref.catalog_id)
        result = _get(connection, ref.object_id, ref.revision)
        if result.ref != ref:
            raise BackendConflict(
                "apparatus reference does not match retained revision"
            )
        return result

    def create_object(self, command: ApparatusObjectCreate) -> ApparatusObjectRevision:
        self._require_catalog(command.catalog_id)
        with self.database.write_transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM apparatus_objects WHERE object_id=?",
                    (command.object_id,),
                ).fetchone()
                is not None
            ):
                raise BackendConflict("apparatus object already exists")
            connection.execute(
                "INSERT INTO apparatus_objects VALUES (?,1)", (command.object_id,)
            )
            return self._append(
                connection, command.object_id, 1, command.content, command.actor
            )

    def revise_object(self, command: ApparatusObjectRevise) -> ApparatusObjectRevision:
        self._require_catalog(command.expected.catalog_id)
        with self.database.write_transaction() as connection:
            current = _get(connection, command.expected.object_id)
            if current.ref != command.expected:
                raise BackendConflict(
                    "apparatus object changed; reload before revising"
                )
            result = self._append(
                connection,
                current.ref.object_id,
                current.ref.revision + 1,
                command.content,
                command.actor,
            )
            connection.execute(
                "UPDATE apparatus_objects SET head_revision=? WHERE object_id=?",
                (result.ref.revision, result.ref.object_id),
            )
            return result

    def _append(
        self,
        connection: sqlite3.Connection,
        object_id: str,
        revision: int,
        content: ApparatusObjectContent,
        actor: str,
    ) -> ApparatusObjectRevision:
        value = ApparatusObjectRevision(
            ref=ApparatusObjectRef(
                catalog_id=self.catalog_id,
                object_id=object_id,
                revision=revision,
                content_hash=content.content_hash,
            ),
            content=content,
            actor=actor,
            recorded_at=datetime.now(UTC),
        )
        connection.execute(
            "INSERT INTO apparatus_object_revisions VALUES (?,?,?)",
            (object_id, revision, value.model_dump_json()),
        )
        return value

    def record_observation(
        self, command: ApparatusObservationCreate
    ) -> ApparatusObservation:
        draft = command.draft
        with self.database.write_transaction() as connection:
            existing = _find_observation(connection, command.observation_id)
            if existing is not None:
                if existing.draft == draft:
                    return existing
                raise BackendConflict(
                    "observation id already records different content"
                )
            self._resolve(connection, draft.subject)
            for run_id in draft.run_ids:
                if (
                    connection.execute(
                        "SELECT 1 FROM runs WHERE run_id=?", (run_id,)
                    ).fetchone()
                    is None
                ):
                    raise BackendNotFound(f"observation run {run_id!r} not found")
            if draft.supersedes is not None:
                prior = _find_observation(connection, draft.supersedes)
                if prior is None:
                    raise BackendNotFound("superseded observation not found")
                if prior.draft.subject.object_id != draft.subject.object_id:
                    raise BackendConflict(
                        "superseded observation belongs to another object"
                    )
            for attachment in draft.attachments:
                self._attachment_bytes(attachment)
            value = ApparatusObservation(
                id=command.observation_id, draft=draft, recorded_at=datetime.now(UTC)
            )
            connection.execute(
                "INSERT INTO apparatus_observations("
                "observation_id,object_id,revision,observation_json) VALUES (?,?,?,?)",
                (
                    value.id,
                    draft.subject.object_id,
                    draft.subject.revision,
                    value.model_dump_json(),
                ),
            )
            connection.executemany(
                "INSERT INTO apparatus_observation_runs VALUES (?,?)",
                [(value.id, run_id) for run_id in dict.fromkeys(draft.run_ids)],
            )
            connection.executemany(
                "INSERT INTO apparatus_observation_attachments VALUES (?,?)",
                [
                    (value.id, digest)
                    for digest in dict.fromkeys(
                        a.content_hash for a in draft.attachments
                    )
                ],
            )
            return value

    def get_observation(self, observation_id: str) -> ApparatusObservation:
        with self.database.read_connection() as connection:
            result = _find_observation(connection, observation_id)
        if result is None:
            raise BackendNotFound("apparatus observation not found")
        return result

    def observations(
        self,
        object_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
        run_id: str | None = None,
    ) -> ApparatusObservationPage:
        with self.database.read_connection() as connection:
            _get(connection, object_id)
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT sequence, observation_json FROM apparatus_observations o "
                    "WHERE object_id=? AND (? IS NULL OR sequence<?) "
                    "AND (? IS NULL OR EXISTS (SELECT 1 "
                    "FROM apparatus_observation_runs r "
                    "WHERE r.observation_id=o.observation_id AND r.run_id=?)) "
                    "ORDER BY sequence DESC LIMIT ?",
                    (object_id, before, before, run_id, run_id, limit + 1),
                ).fetchall(),
            )
        return ApparatusObservationPage(
            items=tuple(_observation(row) for row in rows[:limit]),
            next_cursor=_cursor(rows, limit),
        )

    def import_attachment(self, content: bytes, filename: str) -> ApparatusAttachment:
        if not 1 <= len(content) <= MAX_APPARATUS_ATTACHMENT_BYTES:
            raise BackendConflict(
                "apparatus attachment must contain 1 to 67108864 bytes"
            )
        digest = self.objects.put(content).digest
        return ApparatusAttachment(
            content_hash=digest,
            filename=filename,
            size_bytes=len(content),
        )

    def _attachment_bytes(self, attachment: ApparatusAttachment) -> bytes:
        try:
            with self.objects.path_for(attachment.content_hash).open("rb") as source:
                content = source.read(attachment.size_bytes + 1)
        except OSError as error:
            raise BackendConflict(
                "apparatus attachment is missing or corrupt"
            ) from error
        if len(content) != attachment.size_bytes:
            raise BackendConflict(
                "apparatus attachment size does not match retained bytes"
            )
        if f"sha256:{hashlib.sha256(content).hexdigest()}" != attachment.content_hash:
            raise BackendConflict("apparatus attachment is missing or corrupt")
        return content

    def attachment_content(self, observation_id: str, content_hash: str) -> bytes:
        observation = self.get_observation(observation_id)
        for attachment in observation.draft.attachments:
            if attachment.content_hash == content_hash:
                return self._attachment_bytes(attachment)
        raise BackendNotFound("attachment is not referenced by this observation")


def _cursor(rows: list[sqlite3.Row], limit: int) -> int | None:
    return cast("int", rows[limit - 1]["sequence"]) if len(rows) > limit else None


def _revision(row: sqlite3.Row) -> ApparatusObjectRevision:
    return ApparatusObjectRevision.model_validate_json(
        cast("str", row["revision_json"])
    )


def _observation(row: sqlite3.Row) -> ApparatusObservation:
    return ApparatusObservation.model_validate_json(
        cast("str", row["observation_json"])
    )


def _get(
    connection: sqlite3.Connection, object_id: str, revision: int | None = None
) -> ApparatusObjectRevision:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT r.revision_json FROM apparatus_object_revisions r "
            "JOIN apparatus_objects o ON r.object_id=o.object_id "
            "WHERE o.object_id=? AND r.revision=COALESCE(?,o.head_revision)",
            (object_id, revision),
        ).fetchone(),
    )
    if row is None:
        raise BackendNotFound("apparatus object revision not found")
    return _revision(row)


def _find_observation(
    connection: sqlite3.Connection, observation_id: str
) -> ApparatusObservation | None:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT observation_json FROM apparatus_observations "
            "WHERE observation_id=?",
            (observation_id,),
        ).fetchone(),
    )
    return _observation(row) if row is not None else None
