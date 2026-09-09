"""Verified directory snapshots of stopped projects; no project code is executed."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Generator, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from importlib.metadata import distributions
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict
from scopecat.project import Project, load_project
from scopecat.records.sample import SampleRevision
from scopecat.records.sample_artifact import is_owned_sample_artifact_uri

from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.project_store import (
    inspect_project_schema,
    require_current_schema,
)

_DATABASE = Path(".scopecat/control.sqlite3")
_OBJECTS = Path(".scopecat/objects")
_EXCLUDED = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
    }
)
_SOURCE_BOUNDARY = (
    "Project-local files only. Environments, caches, Git internals, symlinks, "
    "and external SDK/source installations are not archived. Reinstall dependencies "
    "from retained artifacts and the recorded versions before running project code."
)


class SnapshotError(RuntimeError):
    """Snapshot creation, verification, or restoration failed."""


class SnapshotManifest(BaseModel):
    """Portable inventory; hashes detect damage, not an untrusted publisher."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format_version: Literal[1]
    created_at: str
    schema_version: int
    python_version: str
    packages: dict[str, str]
    source_boundary: str = _SOURCE_BOUNDARY
    files: dict[str, str]


def create_snapshot(project: Project, destination: Path) -> SnapshotManifest:
    """Capture a stopped project under its existing process and SQLite locks."""
    destination = _fresh_destination(destination)
    if destination.is_relative_to(project.root):
        raise SnapshotError("snapshot destination must be outside the source project")
    database = project.root / _DATABASE
    if database.parent.is_symlink() or database.is_symlink():
        raise SnapshotError("snapshot requires project-local database state")
    if not database.is_file():
        raise SnapshotError(f"project has no database: {database}")
    try:
        with _stopped_store(project.root) as source, _stage(destination) as staged:
            captured = staged / "project"
            captured.mkdir()
            for relative in _files(project.root, source=True):
                _copy(project.root / relative, captured / relative)
            objects = captured / _OBJECTS
            objects.mkdir(parents=True)
            for relative in _files(project.root / _OBJECTS):
                # Unpublished object-store temporary files are not immutable content.
                if relative.name.endswith(".tmp") and relative.name.startswith("."):
                    continue
                _copy(project.root / _OBJECTS / relative, objects / relative)
            # SQLite's copy primitive is used only after stopped-project ownership
            # is acquired. It folds a retained WAL into a standalone destination
            # database without checkpointing or changing the source database.
            with closing(sqlite3.connect(captured / _DATABASE)) as target:
                source.backup(target)
                target.execute("PRAGMA journal_mode = DELETE")
            schema = _verify_store(captured)
            manifest = SnapshotManifest(
                format_version=1,
                created_at=datetime.now(UTC).isoformat(),
                schema_version=schema,
                python_version=sys.version,
                packages={
                    distribution.metadata["Name"]: distribution.version
                    for distribution in distributions()
                },
                files={
                    relative.as_posix(): _digest(captured / relative)
                    for relative in _files(captured)
                },
            )
            (staged / "manifest.json").write_text(
                manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            staged.rename(destination)
            return manifest
    except (OSError, sqlite3.Error) as error:
        raise SnapshotError(f"cannot create snapshot: {error}") from error


def verify_snapshot(snapshot: Path) -> SnapshotManifest:
    """Check the inventory, supported schema, SQLite integrity, and all objects."""
    try:
        manifest = SnapshotManifest.model_validate_json(
            (snapshot / "manifest.json").read_text(encoding="utf-8")
        )
        project = snapshot / "project"
        actual = {relative.as_posix() for relative in _files(project)}
        if actual != set(manifest.files):
            raise SnapshotError("snapshot file inventory does not match manifest")
        for name, digest in manifest.files.items():
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in name
                or relative.as_posix() != name
                or not _allowed_path(relative)
            ):
                raise SnapshotError(f"unsupported snapshot path: {name}")
            if _digest(project / name) != digest:
                raise SnapshotError(f"snapshot checksum mismatch: {name}")
        load_project(project / "scopecat.toml")
        schema = _verify_store(project)
        if schema != manifest.schema_version:
            raise SnapshotError("snapshot schema does not match manifest")
        return manifest
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SnapshotError(f"cannot verify snapshot: {error}") from error


def restore_snapshot(snapshot: Path, destination: Path) -> SnapshotManifest:
    """Restore verified files into a fresh project without running its code."""
    destination = _fresh_destination(destination)
    manifest = verify_snapshot(snapshot)
    try:
        with _stage(destination) as staged:
            for name in manifest.files:
                _copy(snapshot / "project" / name, staged / name)
                if _digest(staged / name) != manifest.files[name]:
                    raise SnapshotError(f"snapshot changed during restore: {name}")
            (staged / _OBJECTS).mkdir(parents=True, exist_ok=True)
            # Preserve the version record with the restored project. Runtime
            # endpoint/lock/GUI management records cannot enter the inventory.
            (staged / ".scopecat/restore-manifest.json").write_text(
                manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            staged.rename(destination)
        return manifest
    except OSError as error:
        raise SnapshotError(f"cannot restore snapshot: {error}") from error


@contextmanager
def _stopped_store(root: Path) -> Generator[sqlite3.Connection]:
    database = root / _DATABASE
    # Inspect before acquiring a lock file or opening any write connection so an
    # unsupported schema never enters the bootstrap/migration path.
    if inspect_project_schema(database) is None:
        raise SnapshotError("project database has not been initialized")
    retained_wal = database.with_name(database.name + "-wal").exists()
    try:
        with (
            FileLock(root / ".scopecat/daemon.lock", timeout=0),
            closing(
                sqlite3.connect(database, timeout=0, isolation_level=None)
            ) as guard,
        ):
            # Keep a pre-existing WAL intact. Otherwise normal close removes
            # the empty sidecars this reservation may create.
            guard.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, retained_wal)
            try:
                guard.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                raise SnapshotError(
                    "project has an active SQLite writer; stop it first"
                ) from error
            try:
                with closing(_read_database(database)) as reader:
                    yield reader
            finally:
                guard.rollback()
    except Timeout as error:
        raise SnapshotError(
            "project has a running daemon; stop it before snapshotting"
        ) from error


def _read_database(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    option = "immutable=1" if immutable else "mode=ro"
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?{option}", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _verify_store(project: Path) -> int:
    with closing(_read_database(project / _DATABASE, immutable=True)) as connection:
        version = require_current_schema(connection)
        integrity = cast(
            "list[sqlite3.Row]", connection.execute("PRAGMA integrity_check").fetchall()
        )
        if [row[0] for row in integrity] != ["ok"]:
            raise SnapshotError("snapshot SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SnapshotError("snapshot SQLite foreign key check failed")
        objects = ImmutableObjectStore(project / _OBJECTS)
        refs = cast(
            "list[sqlite3.Row]",
            connection.execute(
                "SELECT digest FROM run_repository_refs "
                "UNION SELECT digest FROM project_analysis_repository_refs "
                "UNION SELECT bundle_digest AS digest FROM author_revisions"
            ).fetchall(),
        )
        for row in refs:
            objects.verify(cast("str", row["digest"]))
        sample_rows = cast(
            "Iterator[sqlite3.Row]",
            connection.execute("SELECT revision_json FROM sample_revisions"),
        )
        for row in sample_rows:
            revision = SampleRevision.model_validate_json(
                cast("str", row["revision_json"])
            )
            for artifact in revision.content.artifacts:
                if is_owned_sample_artifact_uri(artifact.uri):
                    objects.verify(artifact.uri)
        for relative in _files(objects.root):
            if len(relative.parts) != 2 or len(relative.parts[0]) != 2:
                raise SnapshotError(f"invalid immutable object path: {relative}")
            digest = "sha256:" + "".join(relative.parts)
            objects.verify(digest)
        return version


def _allowed_path(path: PurePosixPath) -> bool:
    if any(part in _EXCLUDED for part in path.parts):
        return False
    if path.parts[0] != ".scopecat":
        return True
    return path == PurePosixPath(_DATABASE) or (
        path.is_relative_to(PurePosixPath(_OBJECTS)) and len(path.parts) == 4
    )


def _files(root: Path, *, source: bool = False) -> Iterator[Path]:
    if root.is_symlink():
        raise SnapshotError(f"snapshot requires a project-local directory: {root}")
    for directory, directories, files in root.walk(on_error=_walk_error):
        if source:
            directories[:] = [
                name
                for name in directories
                if name not in _EXCLUDED
                and not (directory == root and name == ".scopecat")
            ]
            files = [name for name in files if name not in _EXCLUDED]
        for name in sorted(files):
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise SnapshotError(
                    f"snapshot requires regular project-local files: {path}"
                )
            yield path.relative_to(root)


def _walk_error(error: OSError) -> None:
    raise error


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _fresh_destination(destination: Path) -> Path:
    destination = destination.resolve()
    if destination.exists():
        raise SnapshotError(f"destination must be a fresh path: {destination}")
    if not destination.parent.is_dir():
        raise SnapshotError(f"destination parent does not exist: {destination.parent}")
    return destination


@contextmanager
def _stage(destination: Path) -> Generator[Path]:
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}-", dir=destination.parent
    ) as temporary:
        yield Path(temporary)
