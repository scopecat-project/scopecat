"""Only additive 115→116 upgrades on disposable databases."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scopecat_server.storage.sqlite import upgrade_115 as upgrade
from scopecat_server.storage.sqlite.managed_analysis import MANAGED_ANALYSIS_STATEMENTS
from scopecat_server.storage.sqlite.schema import PROJECT_SCHEMA_SQL


def old_store(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(PROJECT_SCHEMA_SQL)
    connection.execute("DROP TABLE managed_analysis_publications")
    connection.execute("UPDATE project_schema SET version=115")
    connection.commit()
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("INSERT INTO author_revisions VALUES('retained','object-hash')")
    connection.commit()
    return connection


def version(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT version FROM project_schema").fetchone()[0]


def test_upgrade_backs_up_wal_preserves_records_and_reenters(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with closing(old_store(database)) as original:
        assert database.with_name(database.name + "-wal").stat().st_size > 0
        backup = upgrade.upgrade_115(database, tmp_path / "backups")
        assert backup is not None
        with closing(sqlite3.connect(backup)) as saved:
            assert version(saved) == 115
            assert saved.execute("SELECT * FROM author_revisions").fetchall() == [
                ("retained", "object-hash")
            ]
            # Recovery uses SQLite's copy primitive and retains the old format/data.
            with closing(sqlite3.connect(tmp_path / "recovered.sqlite3")) as recovered:
                saved.backup(recovered)
                assert version(recovered) == 115
                assert (
                    recovered.execute("SELECT * FROM author_revisions").fetchall()
                    == saved.execute("SELECT * FROM author_revisions").fetchall()
                )
        assert version(original) == 116
        assert original.execute(
            "SELECT COUNT(*) FROM managed_analysis_publications"
        ).fetchone() == (0,)
        assert original.execute("SELECT * FROM author_revisions").fetchall() == [
            ("retained", "object-hash")
        ]
        assert upgrade.upgrade_115(database, tmp_path / "backups") is None
        assert len(list((tmp_path / "backups").iterdir())) == 1


@pytest.mark.parametrize("failure", ["backup", "transaction"])
def test_failure_leaves_115_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    database = tmp_path / "control.sqlite3"
    with closing(old_store(database)) as original:
        with monkeypatch.context() as patch:
            if failure == "backup":

                def fail(*_args: object) -> None:
                    raise OSError("backup failed")

                patch.setattr(upgrade, "_backup", fail)
            else:
                patch.setattr(
                    upgrade,
                    "MANAGED_ANALYSIS_STATEMENTS",
                    (*MANAGED_ANALYSIS_STATEMENTS, "INVALID SQL"),
                )
            with pytest.raises((OSError, sqlite3.OperationalError)):
                upgrade.upgrade_115(database, tmp_path / "backups")
        assert version(original) == 115
        assert (
            original.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name='managed_analysis_publications'"
            ).fetchone()
            is None
        )
        assert original.execute("SELECT * FROM author_revisions").fetchall() == [
            ("retained", "object-hash")
        ]
        assert upgrade.upgrade_115(database, tmp_path / "backups") is not None


def test_writer_and_unknown_version_rejected(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with closing(old_store(database)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            upgrade.upgrade_115(database, tmp_path / "backups")
        writer.rollback()
        writer.execute("UPDATE project_schema SET version=114")
        writer.commit()
        with pytest.raises(ValueError, match="114"):
            upgrade.upgrade_115(database, tmp_path / "backups")
        assert version(writer) == 114
        assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_process_exit_is_atomic_and_retryable(tmp_path: Path, phase: str) -> None:
    import subprocess
    import sys

    database = tmp_path / "control.sqlite3"
    with closing(old_store(database)):
        pass
    script = """
import os, sqlite3, sys
from pathlib import Path
from scopecat_server.storage.sqlite import upgrade_115 as upgrade
original = upgrade._validate
def validate(connection):
    original(connection)
    if (sys.argv[3] == "before_commit" and connection.in_transaction
            and upgrade._version(connection) == 116):
        os._exit(71)
upgrade._validate = validate
upgrade.upgrade_115(Path(sys.argv[1]), Path(sys.argv[2]))
os._exit(72)
"""
    result = subprocess.run(  # noqa: S603 - fixed test code and temporary fixture paths
        [sys.executable, "-c", script, str(database), str(tmp_path / "backups"), phase],
        check=False,
        timeout=15,
    )
    assert result.returncode == (71 if phase == "before_commit" else 72)
    with closing(sqlite3.connect(database)) as connection:
        assert version(connection) == (115 if phase == "before_commit" else 116)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT * FROM author_revisions").fetchall() == [
            ("retained", "object-hash")
        ]
    upgrade.upgrade_115(database, tmp_path / "backups")
    with closing(sqlite3.connect(database)) as connection:
        assert version(connection) == 116


def test_declared_version_is_not_enough_to_upgrade(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with closing(old_store(database)) as connection:
        connection.execute("DROP INDEX analysis_publications_identity")
        connection.commit()
        with pytest.raises(ValueError, match="结构"):
            upgrade.upgrade_115(database, tmp_path / "backups")
        assert version(connection) == 115
        assert not (tmp_path / "backups").exists()
