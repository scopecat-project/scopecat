"""Development schema upgrades protect source bytes and verify actual restore."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import FileLock
from scopecat.project import load_project
from typer.testing import CliRunner

from scopecat_server.cli import app
from scopecat_server.migrations import migrate_copy, plan_migration
from scopecat_server.snapshots import SnapshotError, restore_snapshot, verify_snapshot
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.project_store import inspect_project_schema


def _legacy(root: Path, version: int = 68):
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    (root / "application.py").write_text('raise AssertionError("must not import")\n')
    state = root / ".scopecat"
    state.mkdir()
    objects = ImmutableObjectStore(state / "objects")
    objects.bootstrap()
    content = objects.put(b"retained scientific array bytes")
    with closing(sqlite3.connect(state / "control.sqlite3")) as connection:
        connection.executescript(
            (Path(__file__).parent / "fixtures/schema-68.sql").read_text()
        )
        connection.execute(
            "INSERT INTO runs(run_id,created_at,config_content_hash) "
            "VALUES ('retained','2026-09-01T00:00:00+00:00','config')"
        )
        connection.execute(
            "INSERT INTO run_repository_refs(run_id,ref,digest) "
            "VALUES ('retained','data',?)",
            (content.digest,),
        )
        if version >= 69:
            connection.execute(
                "CREATE TABLE parameter_workspace_heads("
                "workspace_id TEXT PRIMARY KEY "
                "REFERENCES config_registry_entries(entry_id), "
                "entry_id TEXT NOT NULL REFERENCES config_registry_entries(entry_id))"
            )
            connection.execute("UPDATE project_schema SET version=69")
        if version == 70:
            from scopecat_server.storage.sqlite.research_schema import (
                RESEARCH_TABLES_SQL,
            )

            connection.executescript(RESEARCH_TABLES_SQL)
            connection.execute("UPDATE project_schema SET version=70")
        connection.commit()
    return load_project(root / "scopecat.toml")


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and path.name != "daemon.lock"
    }


@pytest.mark.parametrize("version", [68, 69, 70])
def test_upgrade_and_actual_restore_preserve_source_and_objects(
    tmp_path: Path, version: int
) -> None:
    project = _legacy(tmp_path / "旧数据", version)
    original = _hashes(project.root)
    destination = tmp_path / "升级 副本"
    plan = plan_migration(project)
    assert plan.steps == tuple(f"{v}->{v + 1}" for v in range(version, 71))
    receipt = migrate_copy(project, destination)
    assert receipt.plan == plan
    assert _hashes(project.root) == original
    upgraded = load_project(destination / "project/scopecat.toml")
    assert (
        upgraded.runtime_binding.deployment_root
        == project.runtime_binding.deployment_root
    )
    assert upgraded.runtime_binding.data_root == destination / "project/.scopecat"
    assert (
        inspect_project_schema(upgraded.runtime_binding.data_root / "control.sqlite3")
        == 71
    )
    assert verify_snapshot(destination / "original").schema_version == version
    restored = tmp_path / "restored"
    restore_snapshot(destination / "original", restored)
    assert _hashes(restored / ".scopecat/objects") == _hashes(
        project.runtime_binding.data_root / "objects"
    )
    with closing(sqlite3.connect(restored / ".scopecat/control.sqlite3")) as connection:
        assert connection.execute("SELECT version FROM project_schema").fetchone() == (
            version,
        )
        assert connection.execute("SELECT run_id FROM runs").fetchone() == ("retained",)
    result = CliRunner().invoke(app, ["migration", "plan", str(project.root)])
    assert result.exit_code == 0 and "71" in result.output


def test_failed_or_busy_migration_does_not_publish_or_modify_original(
    tmp_path: Path,
) -> None:
    from scopecat_server import migrations

    project = _legacy(tmp_path / "source")
    original = _hashes(project.root)
    upgrade = migrations._upgrade

    def fail(database: Path, plan: migrations.MigrationPlan) -> None:
        upgrade(database, plan)
        raise OSError("interrupted after schema commit")

    with (
        patch.object(migrations, "_upgrade", fail),
        pytest.raises(SnapshotError, match="interrupted"),
    ):
        migrate_copy(project, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert _hashes(project.root) == original
    with (
        FileLock(project.runtime_binding.data_root / "daemon.lock"),
        pytest.raises(SnapshotError, match="running daemon"),
    ):
        migrate_copy(project, tmp_path / "busy")
    assert not (tmp_path / "busy").exists()
    assert _hashes(project.root) == original


def test_successful_sql_that_changes_existing_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    from scopecat_server import migrations

    project = _legacy(tmp_path / "source")
    original = _hashes(project.root)
    upgrade = migrations._upgrade

    def corrupt(database: Path, plan: migrations.MigrationPlan) -> None:
        upgrade(database, plan)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE runs SET config_content_hash='invented'")
            connection.commit()

    with (
        patch.object(migrations, "_upgrade", corrupt),
        pytest.raises(SnapshotError, match="changed existing"),
    ):
        migrate_copy(project, tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()
    assert _hashes(project.root) == original


def test_legacy_wal_commits_enter_the_verified_upgrade(tmp_path: Path) -> None:
    project = _legacy(tmp_path / "source")
    database = project.runtime_binding.data_root / "control.sqlite3"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO runs(run_id,created_at,config_content_hash) "
            "VALUES ('last-wal','now','config')"
        )
        writer.commit()
        destination = tmp_path / "copied"
        migrate_copy(project, destination)
        with closing(
            sqlite3.connect(destination / "project/.scopecat/control.sqlite3")
        ) as reader:
            assert reader.execute(
                "SELECT run_id FROM runs WHERE run_id='last-wal'"
            ).fetchone() == ("last-wal",)


def test_repeated_current_version_copies_retain_every_migration_receipt(
    tmp_path: Path,
) -> None:
    project = _legacy(tmp_path / "source")
    previous: dict[str, bytes] = {}
    for index in range(3):
        destination = tmp_path / f"copy-{index}"
        migrate_copy(project, destination)
        project = load_project(destination / "project/scopecat.toml")
        history = project.runtime_binding.data_root / "migrations"
        retained = {path.name: path.read_bytes() for path in history.glob("*.json")}
        assert len(retained) == index + 1
        assert all(retained[name] == content for name, content in previous.items())
        previous = retained
