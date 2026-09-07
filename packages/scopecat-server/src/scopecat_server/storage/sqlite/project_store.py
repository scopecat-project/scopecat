"""Physical SQLite project-store ownership and bootstrap."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import cast

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.schema import (
    PROJECT_SCHEMA_SQL,
    PROJECT_SCHEMA_VERSION,
)


class ProjectStoreError(RuntimeError):
    """The SQLite project store could not be opened or initialized."""


class SchemaVersionError(ProjectStoreError):
    """The database belongs to an unsupported project-store schema."""


class SQLiteProjectStore:
    """Own the one database and object directory used by a project."""

    def __init__(
        self,
        database: SQLiteDatabase,
        objects: str | Path,
    ) -> None:
        self.sqlite = database
        self.database = database.path
        self.objects = ImmutableObjectStore(objects)

    def bootstrap(self) -> None:
        """Create the current store, refusing implicit schema migration."""

        try:
            # Reject old stores before creating directories or changing their
            # journal mode. Opening a project never implicitly upgrades it.
            if self.database.exists():
                inspect_project_schema(self.database)
            self.database.parent.mkdir(parents=True, exist_ok=True)
            self.objects.bootstrap()
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                if _has_project_schema(connection):
                    self._require_current_version(connection)
                elif _has_application_tables(connection):
                    raise SchemaVersionError(
                        "project store predates the current schema boundary; "
                        + _VERSION_GUIDANCE
                    )
                else:
                    connection.executescript(PROJECT_SCHEMA_SQL)
                    self._require_current_version(connection)
        except SchemaVersionError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ProjectStoreError("failed to bootstrap project store") from error

    def schema_version(self) -> int:
        """Return the supported project-store version or reject the database."""

        try:
            with self.sqlite.read_connection() as connection:
                return self._require_current_version(connection)
        except SchemaVersionError:
            raise
        except sqlite3.Error as error:
            raise ProjectStoreError("failed to inspect project store") from error

    def close(self) -> None:
        """Checkpoint and close the shared SQLite database."""

        self.sqlite.close()

    def _require_current_version(self, connection: sqlite3.Connection) -> int:
        return require_current_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        return self.sqlite.connect()


def _has_project_schema(connection: sqlite3.Connection) -> bool:
    row = _one(
        connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'project_schema'
            """
        )
    )
    return row is not None


_VERSION_GUIDANCE = (
    "Preserve the original project and snapshot. Use its pinned Scopecat reader; "
    "migrate a separate copy only when a tested migration is available, or start "
    "a new project while retaining the old snapshot. "
    "No automatic migration is performed."
)


def require_current_schema(connection: sqlite3.Connection) -> int:
    """Inspect an existing store without writing or applying a migration."""
    if not _has_project_schema(connection):
        raise SchemaVersionError(
            "project store predates the current schema boundary; " + _VERSION_GUIDANCE
        )
    row = _one(
        connection.execute("SELECT version FROM project_schema WHERE singleton = 1")
    )
    version = None if row is None else cast("int", row["version"])
    if version != PROJECT_SCHEMA_VERSION:
        raise SchemaVersionError(
            "unsupported project-store schema version: "
            f"{version}; expected {PROJECT_SCHEMA_VERSION}. " + _VERSION_GUIDANCE
        )
    return version


def _check_existing_schema(connection: sqlite3.Connection) -> int | None:
    if _has_project_schema(connection) or _has_application_tables(connection):
        return require_current_schema(connection)
    return None


def inspect_project_schema(database: Path) -> int | None:
    """Reject unsupported data without creating SQLite sidecars in the source.

    Immutable reads cannot see WAL commits. Inspect a private copy when a WAL
    or rollback journal remains; SQLite may recover only that copy. Normally a
    stopped database has neither and requires no file copy.
    """
    sidecars = [
        path
        for suffix in ("-wal", "-journal")
        if (path := database.with_name(database.name + suffix)).exists()
    ]
    if sidecars:
        with tempfile.TemporaryDirectory(prefix="scopecat-schema-") as temporary:
            copied = Path(temporary) / database.name
            shutil.copyfile(database, copied)
            for path in sidecars:
                shutil.copyfile(path, copied.with_name(path.name))
            with closing(sqlite3.connect(copied)) as connection:
                connection.row_factory = sqlite3.Row
                return _check_existing_schema(connection)
    with closing(
        sqlite3.connect(f"{database.resolve().as_uri()}?immutable=1", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        return _check_existing_schema(connection)


def _has_application_tables(connection: sqlite3.Connection) -> bool:
    row = _one(
        connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            LIMIT 1
            """
        )
    )
    return row is not None


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


__all__ = [
    "ProjectStoreError",
    "SQLiteProjectStore",
    "SchemaVersionError",
    "inspect_project_schema",
    "require_current_schema",
]
