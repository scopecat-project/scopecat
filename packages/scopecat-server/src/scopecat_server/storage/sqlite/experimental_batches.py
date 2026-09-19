"""Read and rename batch labels without rewriting retained event identities."""

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast

from scopecat.records.experimental_batch import (
    ExperimentalBatch,
    ExperimentalBatchEdit,
    ExperimentalBatchPage,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


def _batch(row: sqlite3.Row) -> ExperimentalBatch:
    return ExperimentalBatch.model_validate(
        {
            "id": row["batch_id"],
            **{
                key: row[key]
                for key in (
                    "name",
                    "description",
                    "revision",
                    "created_at",
                    "updated_at",
                )
            },
        }
    )


class ExperimentalBatchStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get(self, batch_id: str) -> ExperimentalBatch:
        with self.database.read_connection() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM experimental_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone(),
            )
        if row is None:
            raise BackendNotFound("experimental batch not found")
        return _batch(row)

    def list(
        self, *, limit: int = 100, before: int | None = None
    ) -> ExperimentalBatchPage:
        with self.database.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT rowid AS sequence,* FROM experimental_batches "
                    "WHERE (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?",
                    (before, before, limit + 1),
                ).fetchall(),
            )
        return ExperimentalBatchPage(
            items=tuple(_batch(row) for row in rows[:limit]),
            next_cursor=cast("int", rows[limit - 1]["sequence"])
            if len(rows) > limit
            else None,
        )

    def save(self, batch_id: str, edit: ExperimentalBatchEdit) -> ExperimentalBatch:
        now = datetime.now(UTC).isoformat()
        with self.database.write_transaction() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT * FROM experimental_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone(),
            )
            if row is None:
                if edit.expected_revision != 0:
                    raise BackendConflict(
                        "experimental batch no longer matches selected revision"
                    )
                connection.execute(
                    "INSERT INTO experimental_batches VALUES (?,?,?,?,?,?)",
                    (batch_id, edit.name, edit.description, 1, now, now),
                )
            else:
                if row["name"] == edit.name and row["description"] == edit.description:
                    return _batch(row)
                if row["revision"] != edit.expected_revision:
                    raise BackendConflict(
                        "experimental batch changed; reload before editing"
                    )
                connection.execute(
                    "UPDATE experimental_batches SET name=?,description=?,"
                    "revision=revision+1,updated_at=? WHERE batch_id=?",
                    (edit.name, edit.description, now, batch_id),
                )
            result = cast(
                "sqlite3.Row",
                connection.execute(
                    "SELECT * FROM experimental_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone(),
            )
            return _batch(result)


def require_batch(connection: sqlite3.Connection, batch_id: str | None) -> None:
    if (
        batch_id is not None
        and connection.execute(
            "SELECT 1 FROM experimental_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        is None
    ):
        raise BackendNotFound("experimental batch not found")


def require_batches(
    connection: sqlite3.Connection, batch_ids: Iterable[str | None]
) -> None:
    for batch_id in batch_ids:
        require_batch(connection, batch_id)
