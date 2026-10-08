from __future__ import annotations

import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import (
    SchemaVersionError,
    SQLiteProjectStore,
)
from scopecat_server.storage.sqlite.schema import PROJECT_SCHEMA_VERSION


def test_bootstrap_creates_the_complete_project_store_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")

    store.bootstrap()
    store.bootstrap()

    assert store.schema_version() == 113
    with sqlite3.connect(database) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        instrument_session_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(instrument_sessions)")
        }
        scheduler_run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scheduler_runs)")
        }
        execution_segment_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(run_execution_segments)")
        }
        executor_lease_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(executor_leases)")
        }
        analysis_publication_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(analysis_publications)")
        }
        procedure_run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(procedure_runs)")
        }
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
            )
        }
    assert journal_mode == ("wal",)
    assert {
        "project_schema",
        "scheduler_runs",
        "durable_events",
        "run_execution_segments",
        "execution_measurement_fragments",
        "execution_domain_job_transitions",
        "runs",
        "run_outcomes",
        "run_contents",
        "run_repository_refs",
        "analysis_publications",
        "analysis_follows",
        "analysis_follow_events",
        "project_analysis_contents",
        "project_analysis_repository_refs",
        "procedure_runs",
        "calibration_check_requests",
        "procedure_step_attempts",
        "procedure_leases",
        "decision_drafts",
        "procedure_schedules",
        "config_registry_entries",
    } <= tables
    assert {"renewed_at", "expires_at"} <= instrument_session_columns
    assert "cancellation_requested_at" in scheduler_run_columns
    assert {
        "segment_id",
        "ordinal",
        "run_contract_fingerprint",
        "start_point_count",
        "end_point_count",
        "result",
        "certainty",
    } <= execution_segment_columns
    assert "segment_id" in executor_lease_columns
    assert "record_entry_json" in analysis_publication_columns
    assert "published_at" in analysis_publication_columns
    assert "manifest_json" not in analysis_publication_columns
    assert {
        "definition_id",
        "definition_version",
        "definition_fingerprint",
        "closure_status",
        "closed_at",
    } <= procedure_run_columns
    assert {name for name in tables if name.startswith("calibration_")} == {
        "calibration_check_requests",
        "calibration_tasks",
        "calibration_profiles",
    }
    assert not any(name.startswith("calibration_") for name in triggers)


@pytest.mark.parametrize(
    "version",
    (
        pytest.param(PROJECT_SCHEMA_VERSION - 1, id="previous-format"),
        pytest.param(PROJECT_SCHEMA_VERSION + 1, id="future-format"),
    ),
)
def test_bootstrap_refuses_a_noncurrent_project_schema(
    tmp_path: Path,
    version: int,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    store.bootstrap()
    with store.sqlite.write_transaction() as connection:
        connection.execute("UPDATE project_schema SET version = ?", (version,))
    store.close()
    # Inspect a complete, closed store as a new owner. Rejection must preserve
    # physical files too, not merely an equivalent SQL dump.
    original_paths = set(tmp_path.iterdir())
    original_files = {
        path.name: path.read_bytes() for path in original_paths if path.is_file()
    }
    reopened = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(
        SchemaVersionError,
        match=f"version: {version}; expected {PROJECT_SCHEMA_VERSION}",
    ):
        reopened.bootstrap()

    assert set(tmp_path.iterdir()) == original_paths
    assert {
        path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    } == original_files


def test_bootstrap_rejects_noncurrent_version_before_initializing_missing_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    version = PROJECT_SCHEMA_VERSION - 1
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE project_schema (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO project_schema(singleton, version) VALUES (1, ?)",
            (version,),
        )
        connection.execute("CREATE TABLE retained(value TEXT)")
        connection.execute("INSERT INTO retained VALUES ('original')")
    original = database.read_bytes()
    objects = tmp_path / "objects"
    store = SQLiteProjectStore(SQLiteDatabase(database), objects)
    with pytest.raises(
        SchemaVersionError,
        match=f"version: {version}; expected {PROJECT_SCHEMA_VERSION}",
    ):
        store.bootstrap()
    assert database.read_bytes() == original
    assert set(tmp_path.iterdir()) == {database}


def test_bootstrap_refuses_tables_without_a_project_schema(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE old_state (value TEXT)")

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="Preserve the original project"):
        store.bootstrap()


def test_bootstrap_retains_wal_until_database_shutdown(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "control.sqlite3")
    store = SQLiteProjectStore(database, tmp_path / "objects")
    store.bootstrap()
    wal = tmp_path / "control.sqlite3-wal"
    assert wal.exists()
    with database.write_transaction() as connection:
        connection.execute("CREATE TABLE startup_probe (value TEXT)")
        connection.execute("INSERT INTO startup_probe VALUES ('retained')")
    store.close()
    assert not wal.exists()
    with sqlite3.connect(database.path) as reopened:
        assert reopened.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            reopened.execute("SELECT value FROM startup_probe").fetchone()[0]
            == "retained"
        )


def test_current_schema_read_keeps_one_snapshot_during_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat_server.storage.sqlite import project_store

    database = SQLiteDatabase(tmp_path / "control.sqlite3")
    store = SQLiteProjectStore(database, tmp_path / "objects")
    store.bootstrap()
    has_schema = project_store._has_project_schema
    changed = False

    def change_version() -> None:
        # Another connection commits and checkpoints between the two schema reads.
        with closing(sqlite3.connect(database.path)) as writer:
            writer.execute("UPDATE project_schema SET version = 114")
            writer.commit()
            writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def checkpoint_after_schema_read(connection: sqlite3.Connection) -> bool:
        nonlocal changed
        found = has_schema(connection)
        if not changed:
            changed = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(change_version).result(timeout=5)
        return found

    monkeypatch.setattr(
        project_store, "_has_project_schema", checkpoint_after_schema_read
    )
    try:
        assert store.schema_version() == 113
        with pytest.raises(SchemaVersionError, match="version: 114"):
            store.schema_version()
    finally:
        store.close()


def test_reopening_current_test_store_does_not_copy_disappearing_wal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat_testkit.server.runtime import sqlite_run_repository

    first = sqlite_run_repository(tmp_path)
    copy = shutil.copyfile
    copies: list[Path] = []

    def close_before_copy(source: Path, destination: Path) -> str:
        copies.append(source)
        first.sqlite.close()
        return str(copy(source, destination))

    # Old bootstrap enumerates WAL, then last-connection close removes it before
    # copy. Current-store access must use SQLite, never this offline-copy path.
    monkeypatch.setattr(shutil, "copyfile", close_before_copy)
    try:
        second = sqlite_run_repository(tmp_path)
        try:
            assert (
                SQLiteProjectStore(second.sqlite, tmp_path / "objects").schema_version()
                == 113
            )
            assert copies == []
        finally:
            second.sqlite.close()
    finally:
        first.sqlite.close()
