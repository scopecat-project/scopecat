from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import FileLock
from scopecat.project import Project, load_project
from typer.testing import CliRunner

from scopecat_server.cli import app
from scopecat_server.services.project_workers import ProjectProcedureWorkers
from scopecat_server.snapshots import (
    SnapshotError,
    create_snapshot,
    restore_snapshot,
    verify_snapshot,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import (
    SchemaVersionError,
    SQLiteProjectStore,
)


def _project(root: Path) -> Project:
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    (root / "application.py").write_text('raise AssertionError("must not execute")\n')
    state = root / ".scopecat"
    store = SQLiteProjectStore(
        SQLiteDatabase(state / "control.sqlite3"), state / "objects"
    )
    store.bootstrap()
    content = store.objects.put(b"measurement content")
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO runs(run_id, created_at, config_content_hash) "
            "VALUES ('r1', 'now', 'config')"
        )
        connection.execute(
            "INSERT INTO run_repository_refs(run_id, ref, digest) "
            "VALUES ('r1', 'data', ?)",
            (content.digest,),
        )
    store.close()
    return load_project(root / "scopecat.toml")


def _bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_cli_roundtrip_retains_source_objects_and_versions_without_runtime_records(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "source")
    state = project.root / ".scopecat"
    for name in ("daemon.json", "daemon.lock", "daemon.log", "console-procedures.json"):
        (state / name).write_text('{"ready": "active"}')
    for name in (".venv", ".git", "__pycache__"):
        (project.root / name).mkdir()
        (project.root / name / "excluded").write_text("runtime")
    before = _bytes(project.root)
    snapshot = tmp_path / "snapshot"
    restored = tmp_path / "restored"
    runner = CliRunner()
    for command in (
        ["snapshot", "create", str(project.root), str(snapshot)],
        ["snapshot", "verify", str(snapshot)],
        ["snapshot", "restore", str(snapshot), str(restored)],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
    manifest = verify_snapshot(snapshot)
    assert manifest.packages["scopecat-server"] == "0.1.0"
    assert "external SDK" in manifest.source_boundary
    assert set(manifest.files) == {
        "scopecat.toml",
        "application.py",
        ".scopecat/control.sqlite3",
        ".scopecat/objects/"
        + hashlib.sha256(b"measurement content").hexdigest()[:2]
        + "/"
        + hashlib.sha256(b"measurement content").hexdigest()[2:],
    }
    assert (restored / "application.py").read_bytes() == before["application.py"]
    assert (restored / ".scopecat/restore-manifest.json").is_file()
    for name in ("daemon.json", "daemon.lock", "console-procedures.json"):
        assert not (restored / ".scopecat" / name).exists()
    # The supported process lock may truncate its own stale lock file; all data
    # and source files remain untouched.
    assert {
        name: data
        for name, data in _bytes(project.root).items()
        if name != ".scopecat/daemon.lock"
    } == {
        name: data for name, data in before.items() if name != ".scopecat/daemon.lock"
    }


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_referenced_object_rejects_capture_without_touching_source(
    tmp_path: Path, damage: str
) -> None:
    project = _project(tmp_path / "source")
    [obj] = [
        path
        for path in (project.root / ".scopecat/objects").rglob("*")
        if path.is_file()
    ]
    if damage == "missing":
        obj.unlink()
    else:
        obj.write_bytes(b"corrupt")
    before = _bytes(project.root)
    with pytest.raises(SnapshotError, match="cannot create snapshot"):
        create_snapshot(project, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()
    assert {
        name: data
        for name, data in _bytes(project.root).items()
        if name != ".scopecat/daemon.lock"
    } == before


@pytest.mark.parametrize("owner", ["daemon", "sqlite"])
def test_active_writer_is_rejected(tmp_path: Path, owner: str) -> None:
    project = _project(tmp_path / "source")
    database = project.root / ".scopecat/control.sqlite3"
    before = database.read_bytes()
    if owner == "daemon":
        with (
            FileLock(project.root / ".scopecat/daemon.lock"),
            pytest.raises(SnapshotError, match="running daemon"),
        ):
            create_snapshot(project, tmp_path / "snapshot")
    else:
        with closing(sqlite3.connect(database)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with pytest.raises(SnapshotError, match="active SQLite writer"):
                create_snapshot(project, tmp_path / "snapshot")
            writer.rollback()
    assert database.read_bytes() == before
    assert not (tmp_path / "snapshot").exists()


@pytest.mark.parametrize("version", [62, 999])
@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_unknown_schema_rejection_leaves_original_files_unchanged(
    tmp_path: Path, journal: str, version: int
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    state = root / ".scopecat"
    state.mkdir()
    database = state / "control.sqlite3"
    with closing(sqlite3.connect(database, isolation_level=None)) as writer:
        writer.execute(f"PRAGMA journal_mode = {journal}")
        writer.execute(
            "CREATE TABLE project_schema "
            "(singleton INTEGER PRIMARY KEY, version INTEGER)"
        )
        writer.execute("INSERT INTO project_schema VALUES (1, ?)", (version,))
        before = _bytes(root)
        store = SQLiteProjectStore(SQLiteDatabase(database), state / "objects")
        with pytest.raises(SchemaVersionError, match="pinned Scopecat reader"):
            store.bootstrap()
        assert _bytes(root) == before
        with pytest.raises(SchemaVersionError, match=f"version: {version}"):
            create_snapshot(load_project(root / "scopecat.toml"), tmp_path / "snapshot")
        assert _bytes(root) == before
    assert not (state / "objects").exists()
    assert not (tmp_path / "snapshot").exists()


def test_snapshot_includes_committed_retained_wal(tmp_path: Path) -> None:
    project = _project(tmp_path / "source")
    database = project.root / ".scopecat/control.sqlite3"
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        connection.execute("INSERT INTO runs VALUES ('wal-run', 'now', 'config', NULL)")
    before = database.read_bytes()
    wal = database.with_name(database.name + "-wal")
    wal_before = wal.read_bytes()
    create_snapshot(project, tmp_path / "snapshot")
    assert database.read_bytes() == before
    assert wal.read_bytes() == wal_before
    restored = tmp_path / "restored"
    restore_snapshot(tmp_path / "snapshot", restored)
    with closing(sqlite3.connect(restored / ".scopecat/control.sqlite3")) as connection:
        assert connection.execute(
            "SELECT run_id FROM runs WHERE run_id = 'wal-run'"
        ).fetchone() == ("wal-run",)


@pytest.mark.parametrize("state", ["ready", "waiting_for_input", "running"])
def test_restored_gui_work_requires_explicit_dispatch(
    tmp_path: Path, state: str
) -> None:
    project = _project(tmp_path / "source")
    (project.root / ".scopecat/console-procedures.json").write_text('{"p1": "active"}')
    snapshot = tmp_path / "snapshot"
    restored = tmp_path / "restored"
    create_snapshot(project, snapshot)
    restore_snapshot(snapshot, restored)
    states = {"p1": state}
    manager = ProjectProcedureWorkers(lambda: restored, states.__getitem__)
    with patch.object(manager, "_spawn") as spawn:
        manager.tick()
        states["p1"] = "ready"
        manager.tick()
        spawn.assert_not_called()
        manager.dispatch("p1")
        spawn.assert_called_once_with("p1")


@pytest.mark.parametrize("damage", ["checksum", "missing", "runtime", "symlink"])
def test_verify_rejects_damaged_or_unsafe_snapshot(tmp_path: Path, damage: str) -> None:
    project = _project(tmp_path / "source")
    snapshot = tmp_path / "snapshot"
    create_snapshot(project, snapshot)
    captured = snapshot / "project"
    if damage == "checksum":
        (captured / "application.py").write_text("changed")
    elif damage == "missing":
        (captured / "application.py").unlink()
    elif damage == "runtime":
        path = captured / ".scopecat/console-procedures.json"
        path.write_text('{"p1":"active"}')
        manifest = json.loads((snapshot / "manifest.json").read_text())
        manifest["files"][".scopecat/console-procedures.json"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        (snapshot / "manifest.json").write_text(json.dumps(manifest))
    else:
        path = captured / "application.py"
        path.unlink()
        try:
            path.symlink_to(project.root / "application.py")
        except OSError:
            pytest.skip("symlinks require platform permission")
    result = CliRunner().invoke(
        app, ["snapshot", "restore", str(snapshot), str(tmp_path / "restored")]
    )
    assert result.exit_code == 1
    assert not (tmp_path / "restored").exists()


def test_snapshot_and_restore_refuse_existing_destinations(tmp_path: Path) -> None:
    project = _project(tmp_path / "source")
    snapshot = tmp_path / "snapshot"
    create_snapshot(project, snapshot)
    before = _bytes(snapshot)
    with pytest.raises(SnapshotError, match="fresh path"):
        create_snapshot(project, snapshot)
    with pytest.raises(SnapshotError, match="fresh path"):
        restore_snapshot(snapshot, project.root)
    assert _bytes(snapshot) == before


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_verify_checks_object_references_even_with_matching_inventory(
    tmp_path: Path, damage: str
) -> None:
    project = _project(tmp_path / "source")
    snapshot = tmp_path / "snapshot"
    create_snapshot(project, snapshot)
    manifest = verify_snapshot(snapshot)
    [name] = [name for name in manifest.files if name.startswith(".scopecat/objects/")]
    obj = snapshot / "project" / name
    inventory = dict(manifest.files)
    if damage == "missing":
        obj.unlink()
        del inventory[name]
    else:
        obj.write_bytes(b"corrupt")
        inventory[name] = hashlib.sha256(obj.read_bytes()).hexdigest()
    (snapshot / "manifest.json").write_text(
        manifest.model_copy(update={"files": inventory}).model_dump_json()
    )
    result = CliRunner().invoke(app, ["snapshot", "verify", str(snapshot)])
    assert result.exit_code == 1
    assert "cannot verify snapshot" in result.output


@pytest.mark.parametrize("version", [62, 999])
def test_unsupported_snapshot_is_not_modified_or_restored(
    tmp_path: Path, version: int
) -> None:
    project = _project(tmp_path / "source")
    snapshot = tmp_path / "snapshot"
    create_snapshot(project, snapshot)
    manifest = verify_snapshot(snapshot)
    database = snapshot / "project/.scopecat/control.sqlite3"
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        connection.execute("UPDATE project_schema SET version = ?", (version,))
        connection.execute("PRAGMA journal_mode = WAL")
    inventory = dict(manifest.files)
    inventory[".scopecat/control.sqlite3"] = hashlib.sha256(
        database.read_bytes()
    ).hexdigest()
    (snapshot / "manifest.json").write_text(
        manifest.model_copy(
            update={"files": inventory, "schema_version": version}
        ).model_dump_json()
    )
    before = _bytes(snapshot)
    result = CliRunner().invoke(
        app, ["snapshot", "restore", str(snapshot), str(tmp_path / "restored")]
    )
    assert result.exit_code == 1
    assert str(version) in result.output and "pinned Scopecat reader" in result.output
    assert _bytes(snapshot) == before
    assert not (tmp_path / "restored").exists()
