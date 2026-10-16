"""The explicit additive 115→116 edge; callers must own the stopped deployment."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast
from uuid import uuid4

from scopecat_server.storage.sqlite.managed_analysis import MANAGED_ANALYSIS_STATEMENTS


def _version(connection: sqlite3.Connection) -> int:
    row = cast(
        "tuple[int] | None",
        connection.execute(
            "SELECT version FROM project_schema WHERE singleton=1"
        ).fetchone(),
    )
    if row is None:
        raise ValueError("数据库缺少格式版本; 未升级")
    return int(row[0])


def _validate(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("数据库完整性检查失败; 未升级")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("数据库引用检查失败; 未升级")


def _require_shape(connection: sqlite3.Connection, *, previous: bool) -> None:
    from scopecat_server.storage.sqlite.schema import PROJECT_SCHEMA_SQL

    # This one edge is valid only while 116 differs from 115 by the binding table.
    # Compare structure, never infer missing scientific relations or rewrite rows.
    query = (
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(PROJECT_SCHEMA_SQL)
        if previous:
            expected.execute("DROP TABLE managed_analysis_publications")
        if connection.execute(query).fetchall() != expected.execute(query).fetchall():
            raise ValueError("数据库结构与声明格式不符, 未升级")


def _backup(database: Path, destination: Path) -> None:
    # A separate reader sees committed WAL while the caller holds the writer fence.
    with (
        closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as reader,
        closing(sqlite3.connect(destination)) as target,
    ):
        reader.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
        _validate(target)
        if _version(target) != 115:
            raise ValueError("备份格式已改变; 未升级")
    with destination.open("rb+") as stream:
        os.fsync(stream.fileno())


def upgrade_115(database: Path, backup_directory: Path) -> Path | None:
    """Back up and atomically add bindings. Never alter objects or historical facts.

    Deployment, daemon and worker ownership must be held by the caller throughout.
    A completed retry returns None. The backup is an upgrade-time database recovery
    copy, not a replacement for a full snapshot after normal work resumes.
    """
    database = database.resolve()
    with closing(
        sqlite3.connect(
            f"{database.as_uri()}?mode=rw", uri=True, timeout=0, isolation_level=None
        )
    ) as connection:
        connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = _version(connection)
            if version == 116:
                # Do not treat an incomplete or unrelated schema as a successful retry.
                _require_shape(connection, previous=False)
                _validate(connection)
                return None
            if version != 115:
                raise ValueError(f"仅支持 115→116, 当前格式为 {version}; 未升级")
            _require_shape(connection, previous=True)
            _validate(connection)
            backup_directory.mkdir(exist_ok=True)
            backup = backup_directory / f"schema-115-{uuid4().hex}.sqlite3"
            temporary = backup.with_suffix(".tmp")
            try:
                _backup(database, temporary)
                temporary.rename(backup)
                if os.name != "nt":
                    for directory in (backup_directory, backup_directory.parent):
                        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
            finally:
                temporary.unlink(missing_ok=True)
            for statement in MANAGED_ANALYSIS_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "UPDATE project_schema SET version=116 WHERE singleton=1"
            )
            _validate(connection)
            connection.commit()
            return backup
        finally:
            connection.rollback()
