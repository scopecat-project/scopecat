from __future__ import annotations

import sqlite3
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

    assert store.schema_version() == 64
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
        calibration_cohort_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(calibration_cohorts)")
        }
        calibration_member_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(calibration_cohort_members)"
            )
        }
        calibration_member_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(calibration_cohort_members)"
            )
        }
        calibration_publication_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(calibration_success_publications)"
            )
        }
        calibration_publication_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(calibration_success_publications)"
            )
        }
        finalization_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(calibration_cohort_finalizations)"
            )
        }
        ready_queue_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(calibration_publication_ready_queue)"
            )
        }
        ready_queue_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(calibration_publication_ready_queue)"
            )
        }
        finalization_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(calibration_cohort_finalizations)"
            )
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
        "procedure_step_attempts",
        "procedure_leases",
        "procedure_schedules",
        "calibration_cohorts",
        "calibration_cohort_members",
        "calibration_success_publications",
        "calibration_cohort_finalizations",
        "calibration_publication_ready_queue",
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
    assert calibration_cohort_columns == {
        "sequence",
        "cohort_id",
        "fanout_scope",
        "cohort_json",
    }
    assert calibration_member_columns == {
        "sequence",
        "cohort_id",
        "member_index",
        "member_id",
        "calibration_key",
        "procedure_run_id",
        "closure_status",
        "closed_at",
        "member_json",
    }
    assert {
        "calibration_cohort_members_key_sequence",
        "calibration_cohort_members_success_key_sequence",
    } <= calibration_member_indexes
    assert {
        "procedure_run_id",
        "operation_id",
        "result_input_fingerprint",
        "result_freshness_fingerprint",
        "result_registry_generation",
        "publication_json",
    } <= calibration_publication_columns
    assert (
        "calibration_success_publications_operation" in calibration_publication_indexes
    )
    assert {
        "state",
        "revision",
        "attempt_count",
        "available_at",
        "superseded_by_generation",
        "publication_operation_id",
    } <= finalization_columns
    assert ready_queue_columns == {"sequence", "cohort_id", "enqueued_at"}
    assert ready_queue_indexes == {
        "sqlite_autoindex_calibration_publication_ready_queue_1"
    }
    assert "calibration_cohort_finalizations_ready_capability" in finalization_indexes
    assert "calibration_cohort_members_sync_terminal_closure" in triggers
    assert "calibration_publication_sync_terminal_success" in triggers
    assert "calibration_publication_sync_terminal_failure" in triggers


@pytest.mark.parametrize("version", (0, 99))
def test_bootstrap_refuses_a_noncurrent_project_schema(
    tmp_path: Path,
    version: int,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    store.bootstrap()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE project_schema SET version = ?", (version,))

    with pytest.raises(SchemaVersionError, match=f"version: {version}"):
        store.bootstrap()


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
        match="version: 52; expected 64",
    ):
        store.bootstrap()


def test_bootstrap_refuses_tables_without_a_project_schema(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE old_state (value TEXT)")

    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="Preserve the original project"):
        store.bootstrap()
