"""Explicit development-schema upgrades in verified copies, never in-place."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from scopecat.project import Project
from scopecat.runtime_binding import RUNTIME_BINDING_NAME

from scopecat_server.snapshots import (
    SnapshotError,
    create_snapshot,
    file_digest,
    fresh_destination,
    read_snapshot_database,
    restore_snapshot,
    staged_directory,
    stopped_store,
    verify_store_files,
)
from scopecat_server.storage.sqlite.batch_schema import EXPERIMENTAL_BATCH_TABLES_SQL
from scopecat_server.storage.sqlite.collection_schema import (
    RECORD_COLLECTION_BACKFILL_SQL,
    RECORD_COLLECTION_TABLES_SQL,
)
from scopecat_server.storage.sqlite.project_store import (
    require_current_schema,
    require_schema_version,
)
from scopecat_server.storage.sqlite.research_schema import RESEARCH_TABLES_SQL
from scopecat_server.storage.sqlite.schema import PROJECT_SCHEMA_VERSION

# These are tested development upgrade edges, not a released compatibility baseline.
_MIGRATIONS = {
    68: """
        CREATE TABLE parameter_workspace_heads (
            workspace_id TEXT PRIMARY KEY REFERENCES config_registry_entries(entry_id),
            entry_id TEXT NOT NULL REFERENCES config_registry_entries(entry_id)
        );
    """,
    69: RESEARCH_TABLES_SQL,
    70: RECORD_COLLECTION_TABLES_SQL + RECORD_COLLECTION_BACKFILL_SQL,
    71: EXPERIMENTAL_BATCH_TABLES_SQL,
}


class MigrationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_schema: int
    target_schema: int
    steps: tuple[str, ...]
    compatibility_scope: str = (
        "tested development schemas; no stable baseline designated"
    )


class MigrationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format_version: Literal[1] = 1
    created_at: datetime
    plan: MigrationPlan
    store_identity: str
    original_data_root: str
    deployment_root: str
    table_hashes: dict[str, str]
    snapshot_files: dict[str, str]


def _plan(version: int) -> MigrationPlan:
    steps: list[str] = []
    current = version
    while current < PROJECT_SCHEMA_VERSION:
        if current not in _MIGRATIONS:
            raise SnapshotError(f"no tested migration from schema {current}")
        steps.append(f"{current}->{current + 1}")
        current += 1
    if current != PROJECT_SCHEMA_VERSION:
        raise SnapshotError(f"no downgrade from schema {current}")
    return MigrationPlan(
        source_schema=version, target_schema=current, steps=tuple(steps)
    )


def plan_migration(project: Project) -> MigrationPlan:
    """Inspect a stopped source without importing its scientific code."""
    with stopped_store(project.runtime_binding.data_root) as connection:
        version = require_schema_version(
            connection, supported_versions=(68, 69, 70, 71, 72)
        )
        return _plan(version)


def _table_hashes(
    connection: sqlite3.Connection, names: tuple[str, ...] | None = None
) -> dict[str, str]:
    if names is None:
        names = tuple(
            cast("str", row[0])
            for row in cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') "
                    "AND name != 'project_schema' ORDER BY name"
                ).fetchall(),
            )
        )
    result: dict[str, str] = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        cursor = connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid")  # noqa: S608 - quoted schema-owned identifier
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                [
                    column[0]
                    for column in cast("Sequence[Sequence[object]]", cursor.description)
                ]
            ).encode()
        )
        for row in cast("Iterator[Sequence[str | int | float | bytes | None]]", cursor):
            values = [
                ({"sqlite_blob": value.hex()} if isinstance(value, bytes) else value)
                for value in row
            ]
            digest.update(
                json.dumps(
                    values, ensure_ascii=True, allow_nan=False, separators=(",", ":")
                ).encode()
            )
            digest.update(b"\n")
        result[name] = digest.hexdigest()
    return result


def _upgrade(database: Path, plan: MigrationPlan) -> None:
    """Only staged copies enter this function; interruption cannot affect the source."""
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for version in range(plan.source_schema, plan.target_schema):
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + _MIGRATIONS[version]
                + f"\nUPDATE project_schema SET version={version + 1} "
                "WHERE singleton=1;\nCOMMIT;"
            )
        connection.execute("PRAGMA journal_mode=DELETE")


def migrate_copy(project: Project, destination: Path) -> MigrationReceipt:
    """Retain backup + migrated project together; expose the directory only on success.

    destination/original is a verified restorable snapshot. destination/project is
    the upgraded workspace. Its deployment lock remains shared with the original;
    starting it is a separate explicit operator action.
    """
    destination = fresh_destination(destination)
    binding = project.runtime_binding
    if any(
        destination.is_relative_to(root)
        for root in (project.root, binding.data_root, binding.deployment_root)
    ):
        raise SnapshotError(
            "migration destination must be outside source workspace, "
            "data and deployment"
        )
    try:
        with staged_directory(destination) as staged:
            manifest = create_snapshot(project, staged / "original")
            plan = _plan(manifest.schema_version)
            restore_snapshot(staged / "original", staged / "project")
            database = staged / "project/.scopecat/control.sqlite3"
            with closing(
                read_snapshot_database(database, immutable=True)
            ) as connection:
                before = _table_hashes(connection)
                identity = cast(
                    "str",
                    connection.execute(
                        "SELECT identity FROM project_identity WHERE singleton=1"
                    ).fetchone()[0],
                )
            _upgrade(database, plan)
            verify_store_files(staged / "project")
            with closing(
                read_snapshot_database(database, immutable=True)
            ) as connection:
                after = _table_hashes(connection, tuple(before))
                require_current_schema(connection)
            if before != after:
                raise SnapshotError(
                    "migration changed existing scientific or provenance records"
                )
            for name, digest in manifest.files.items():
                if (
                    name != ".scopecat/control.sqlite3"
                    and file_digest(staged / "project" / name) != digest
                ):
                    raise SnapshotError(f"migration changed retained file: {name}")
            receipt = MigrationReceipt(
                created_at=datetime.now(UTC),
                plan=plan,
                store_identity=identity,
                original_data_root=str(binding.data_root),
                deployment_root=str(binding.deployment_root),
                table_hashes=before,
                snapshot_files=manifest.files,
            )
            history = staged / "project/.scopecat/migrations"
            history.mkdir(exist_ok=True)
            receipt_json = receipt.model_dump_json(indent=2) + "\n"
            receipt_digest = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
            receipt_name = (
                f"{plan.source_schema}-to-{plan.target_schema}-{receipt_digest}.json"
            )
            (history / receipt_name).write_text(receipt_json, encoding="utf-8")
            # JSON quoted strings are also valid TOML basic strings. Use absolute
            # final paths, not staging paths, and preserve attended-bench ownership.
            data_path = json.dumps(
                str(destination / "project/.scopecat"), ensure_ascii=False
            )
            deployment_path = json.dumps(
                str(binding.deployment_root), ensure_ascii=False
            )
            (staged / "project" / RUNTIME_BINDING_NAME).write_text(
                "[runtime]\n"
                f"data_root = {data_path}\n"
                f"deployment_root = {deployment_path}\n",
                encoding="utf-8",
            )
            staged.rename(destination)
            return receipt
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SnapshotError(f"migration copy failed: {error}") from error
