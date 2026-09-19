"""Collection metadata and transactionally allocated immutable run addresses."""

import sqlite3
from datetime import UTC, datetime
from typing import cast

from scopecat.records.record_collection import (
    RecordCollection,
    RecordCollectionEdit,
    RecordCollectionPage,
    RunAddress,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


def _collection(row: sqlite3.Row) -> RecordCollection:
    return RecordCollection.model_validate(
        {
            "id": row["collection_id"],
            **{
                key: row[key]
                for key in (
                    "name",
                    "description",
                    "revision",
                    "created_at",
                    "updated_at",
                    "is_default",
                )
            },
        }
    )


def address_in_transaction(
    connection: sqlite3.Connection, run_id: str
) -> RunAddress | None:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT * FROM run_addresses WHERE run_id=?",
            (run_id,),
        ).fetchone(),
    )
    return None if row is None else RunAddress.model_validate(dict(row))


def allocate_address(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    sequence: int,
    collection_id: str | None,
) -> RunAddress:
    if collection_id is None:
        row = cast(
            "sqlite3.Row",
            connection.execute(
                "SELECT * FROM record_collections WHERE is_default=1"
            ).fetchone(),
        )
    else:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT * FROM record_collections WHERE collection_id=?",
                (collection_id,),
            ).fetchone(),
        )
        if row is None:
            raise BackendNotFound("record collection not found")
    collection = _collection(row)
    if collection.is_default:
        number = sequence
    else:
        number = cast(
            "int",
            connection.execute(
                "SELECT COALESCE(MAX(number),0)+1 FROM run_addresses "
                "WHERE collection_id=?",
                (collection.id,),
            ).fetchone()[0],
        )
    connection.execute(
        "INSERT INTO run_addresses VALUES (?,?,?)", (run_id, collection.id, number)
    )
    return RunAddress(collection_id=collection.id, number=number, run_id=run_id)


class RecordCollectionStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get(self, collection_id: str) -> RecordCollection:
        with self.database.read_connection() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM record_collections WHERE collection_id=?",
                    (collection_id,),
                ).fetchone(),
            )
        if row is None:
            raise BackendNotFound("record collection not found")
        return _collection(row)

    def list(
        self, *, limit: int = 100, before: int | None = None
    ) -> RecordCollectionPage:
        with self.database.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT rowid AS sequence,* FROM record_collections "
                    "WHERE (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?",
                    (before, before, limit + 1),
                ).fetchall(),
            )
        return RecordCollectionPage(
            items=tuple(_collection(row) for row in rows[:limit]),
            next_cursor=cast("int", rows[limit - 1]["sequence"])
            if len(rows) > limit
            else None,
        )

    def save(self, collection_id: str, edit: RecordCollectionEdit) -> RecordCollection:
        now = datetime.now(UTC).isoformat()
        with self.database.write_transaction() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM record_collections WHERE collection_id=?",
                    (collection_id,),
                ).fetchone(),
            )
            if row is None:
                if edit.expected_revision != 0:
                    raise BackendConflict(
                        "record collection no longer matches selected revision"
                    )
                connection.execute(
                    "INSERT INTO record_collections VALUES (?,?,?,?,?,?,0)",
                    (collection_id, edit.name, edit.description, 1, now, now),
                )
            else:
                if row["name"] == edit.name and row["description"] == edit.description:
                    return _collection(row)
                if row["revision"] != edit.expected_revision:
                    raise BackendConflict(
                        "record collection changed; reload before editing"
                    )
                connection.execute(
                    "UPDATE record_collections SET name=?,description=?,"
                    "revision=revision+1,updated_at=? WHERE collection_id=?",
                    (edit.name, edit.description, now, collection_id),
                )
            result = cast(
                "sqlite3.Row",
                connection.execute(
                    "SELECT * FROM record_collections WHERE collection_id=?",
                    (collection_id,),
                ).fetchone(),
            )
            return _collection(result)

    def resolve(self, collection_id: str, number: int) -> RunAddress:
        with self.database.read_connection() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM run_addresses WHERE collection_id=? AND number=?",
                    (collection_id, number),
                ).fetchone(),
            )
        if row is None:
            raise BackendNotFound("run number not found in this record collection")
        return RunAddress.model_validate(dict(row))
