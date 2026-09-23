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


def test_bootstrap_creates_the_complete_project_store_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")

    store.bootstrap()
    store.bootstrap()

    assert store.schema_version() == 90
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
        "project_analysis_contents",
        "project_analysis_repository_refs",
        "procedure_runs",
        "calibration_check_requests",
        "procedure_step_attempts",
        "procedure_leases",
        "procedure_schedules",
        "config_registry_entries",
        "config_registry_activations",
        "config_operations",
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
    }
    assert not any(name.startswith("calibration_") for name in triggers)


@pytest.mark.parametrize("version", (0, 87, 99))
def test_bootstrap_refuses_a_noncurrent_project_schema(
    tmp_path: Path,
    version: int,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    store.bootstrap()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE project_schema SET version = ?", (version,))
        before = tuple(connection.iterdump())

    with pytest.raises(SchemaVersionError, match=f"version: {version}"):
        store.bootstrap()

    with sqlite3.connect(database) as connection:
        assert tuple(connection.iterdump()) == before


def test_bootstrap_refuses_v39_before_config_publish_step_boundary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 39)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 39"):
        store.bootstrap()


def test_bootstrap_refuses_v40_before_procedure_schedule_boundary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 40)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 40"):
        store.bootstrap()


def test_bootstrap_refuses_v41_before_calibration_cohort_boundary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 41)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 41"):
        store.bootstrap()


def test_bootstrap_refuses_v42_before_calibration_publication_boundary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 42)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 42"):
        store.bootstrap()


def test_bootstrap_refuses_v43_before_automatic_publication_boundary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 43)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 43"):
        store.bootstrap()


def test_bootstrap_refuses_v44_with_unrecoverable_procedure_waiting_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 44)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 44"):
        store.bootstrap()


def test_bootstrap_refuses_v45_without_dedicated_calibration_receipts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 45)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 45"):
        store.bootstrap()


def test_bootstrap_refuses_v46_with_cohort_planner_shadow_columns(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 46)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 46"):
        store.bootstrap()


def test_bootstrap_refuses_v47_with_duplicate_calibration_query_projections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 47)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 47"):
        store.bootstrap()


def test_bootstrap_refuses_v52_without_execution_segments(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
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
            "INSERT INTO project_schema(singleton, version) VALUES (1, 52)"
        )

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(
        SchemaVersionError,
        match="version: 52; expected 90",
    ):
        store.bootstrap()


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
            writer.execute("UPDATE project_schema SET version = 99")
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
        assert store.schema_version() == 90
        with pytest.raises(SchemaVersionError, match="version: 99"):
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
                == 90
            )
            assert copies == []
        finally:
            second.sqlite.close()
    finally:
        first.sqlite.close()
