"""Driver source selection shares the device publication transaction."""

import sqlite3
from typing import cast

from scopecat.records.author_revision import AuthorRevisionBundle
from scopecat.records.driver_source import DriverSourceSelection

from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


class DriverSourceRepository:
    def __init__(self, store: SQLiteProjectStore) -> None:
        self.store = store

    def current(self) -> DriverSourceSelection | None:
        with self.store.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT record_json FROM driver_source_selections "
                    "WHERE operation_id=(SELECT operation_id FROM driver_source_head "
                    "WHERE singleton=1)"
                )
            )
        return (
            None
            if row is None
            else DriverSourceSelection.model_validate_json(cast("str", row[0]))
        )

    def get(self, operation_id: str) -> DriverSourceSelection | None:
        with self.store.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT record_json FROM driver_source_selections "
                    "WHERE operation_id=?",
                    (operation_id,),
                )
            )
        return (
            None
            if row is None
            else DriverSourceSelection.model_validate_json(cast("str", row[0]))
        )

    def bundle(self, selection: DriverSourceSelection) -> AuthorRevisionBundle:
        with self.store.sqlite.read_connection() as connection:
            row = _one(
                connection.execute(
                    "SELECT bundle_digest FROM author_revisions WHERE content_hash=?",
                    (selection.code_revision.content_hash,),
                )
            )
        if row is None:
            raise ValueError("selected driver source snapshot is missing")
        bundle = AuthorRevisionBundle.model_validate_json(
            self.store.objects.read(cast("str", row[0]))
        )
        if bundle.manifest.ref != selection.code_revision:
            raise ValueError(
                "selected driver source identity differs from its snapshot"
            )
        return bundle

    def publish(
        self,
        connection: sqlite3.Connection,
        selection: DriverSourceSelection,
        bundle_digest: str,
    ) -> None:
        row = _one(
            connection.execute(
                "SELECT operation_id FROM driver_source_head WHERE singleton=1"
            )
        )
        previous = None if row is None else cast("str", row[0])
        if previous != selection.request.expected_previous:
            raise ValueError(
                "driver source changed; inspect the active selection and retry"
            )
        connection.execute(
            "INSERT OR IGNORE INTO author_revisions VALUES (?, ?)",
            (selection.code_revision.content_hash, bundle_digest),
        )
        connection.execute(
            "INSERT INTO driver_source_selections VALUES (?, ?)",
            (selection.request.operation_id, selection.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO driver_source_head VALUES (1, ?) "
            "ON CONFLICT(singleton) DO UPDATE SET operation_id=excluded.operation_id",
            (selection.request.operation_id,),
        )
