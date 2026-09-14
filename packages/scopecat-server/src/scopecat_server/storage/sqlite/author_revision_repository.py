"""Immutable source manifests and an atomic active author revision pointer."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from scopecat.records.author_revision import (
    AuthorPreparation,
    AuthorRevisionBundle,
    AuthorRevisionRef,
    AuthorRevisionState,
)

if TYPE_CHECKING:
    from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


class AuthorRevisionConflict(ValueError):
    """Another refresh published first; inspect the active revision and retry."""


class AuthorRevisionRepository:
    def __init__(self, store: SQLiteProjectStore) -> None:
        self.store = store

    def state(self) -> AuthorRevisionState:
        with self.store.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT generation, content_hash FROM author_revision_active "
                    "WHERE singleton = 1"
                )
            )
        if row is None:
            return AuthorRevisionState(enabled=True)
        return AuthorRevisionState(
            enabled=True,
            generation=cast("int", row["generation"]),
            active=AuthorRevisionRef(content_hash=cast("str", row["content_hash"])),
        )

    def get(self, ref: AuthorRevisionRef) -> AuthorRevisionBundle:
        with self.store.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT bundle_digest FROM author_revisions WHERE content_hash = ?",
                    (ref.content_hash,),
                )
            )
        if row is None:
            raise KeyError(ref.content_hash)
        bundle = AuthorRevisionBundle.model_validate_json(
            self.store.objects.read(cast("str", row["bundle_digest"]))
        )
        if bundle.manifest.ref != ref:
            raise ValueError("author revision manifest identity mismatch")
        return bundle

    def publish(
        self,
        bundle: AuthorRevisionBundle,
        *,
        expected_generation: int,
        operation: AuthorPreparation | None = None,
    ) -> AuthorRevisionState:
        digest = self.store.objects.put(bundle.model_dump_json().encode()).digest
        ref = bundle.manifest.ref
        with self.store.sqlite.write_transaction() as connection:
            row = _one(
                connection.execute(
                    "SELECT generation FROM author_revision_active WHERE singleton = 1"
                )
            )
            generation = 0 if row is None else cast("int", row["generation"])
            if generation != expected_generation:
                raise AuthorRevisionConflict(
                    "author revision changed during refresh; inspect and retry"
                )
            connection.execute(
                "INSERT OR IGNORE INTO author_revisions VALUES (?, ?)",
                (ref.content_hash, digest),
            )
            connection.execute(
                "INSERT INTO author_revision_active VALUES (1, ?, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET "
                "generation = excluded.generation, "
                "content_hash = excluded.content_hash",
                (generation + 1, ref.content_hash),
            )
            result = AuthorRevisionState(
                enabled=True, generation=generation + 1, active=ref
            )
            if operation is not None:
                completed = operation.model_copy(
                    update={
                        "status": "succeeded",
                        "phase": "published",
                        "result": result,
                        "updated_at": datetime.now(UTC),
                    }
                )
                connection.execute(
                    "UPDATE author_preparations SET record_json = ? "
                    "WHERE operation_id = ?",
                    (completed.model_dump_json(), completed.operation_id),
                )
        return result

    def preparation(self, operation_id: str) -> AuthorPreparation:
        with self.store.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT record_json FROM author_preparations "
                    "WHERE operation_id = ?",
                    (operation_id,),
                )
            )
        if row is None:
            raise KeyError(operation_id)
        return AuthorPreparation.model_validate_json(cast("str", row["record_json"]))

    def preparations(
        self, *, pending_only: bool = False
    ) -> tuple[AuthorPreparation, ...]:
        query = (
            "SELECT record_json FROM author_preparations "
            "WHERE json_extract(record_json, '$.status') "
            "IN ('queued','running','cancelling') ORDER BY rowid DESC"
            if pending_only
            else "SELECT record_json FROM author_preparations "
            "ORDER BY rowid DESC LIMIT 100"
        )
        with self.store.sqlite.read_connection() as connection:
            rows = cast("list[sqlite3.Row]", connection.execute(query).fetchall())
        return tuple(
            AuthorPreparation.model_validate_json(cast("str", row["record_json"]))
            for row in rows
        )

    def latest_preparation(self, generation: int) -> AuthorPreparation | None:
        with self.store.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT record_json FROM author_preparations "
                    "WHERE json_extract(record_json, '$.expected_generation') = ? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (generation,),
                )
            )
        return (
            None
            if row is None
            else AuthorPreparation.model_validate_json(cast("str", row["record_json"]))
        )

    def save_preparation(self, operation: AuthorPreparation) -> None:
        with self.store.sqlite.write_transaction() as connection:
            connection.execute(
                "INSERT INTO author_preparations VALUES (?, ?) "
                "ON CONFLICT(operation_id) "
                "DO UPDATE SET record_json = excluded.record_json",
                (operation.operation_id, operation.model_dump_json()),
            )


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())
