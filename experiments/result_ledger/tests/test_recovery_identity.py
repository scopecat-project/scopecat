"""Unverified recovery inputs must never be opened for writable initialization."""

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from experiments.result_ledger.core import Store
from experiments.result_ledger.demo import InjectedCrash, declaration, execute

ROOT = Path(__file__).resolve().parents[3]


def source_files(directory):
    return {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }


def readonly_facts(path):
    # Test inspection itself must not create journal or shared-memory files.
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        return {
            "schema": connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall(),
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone(),
            "sentinel": connection.execute("SELECT * FROM sentinel").fetchall(),
        }
    finally:
        connection.close()


def invoke_recovery(directory):
    return subprocess.run(  # noqa: S603 -- fixed local CLI and isolated test paths
        [
            sys.executable,
            "-m",
            "experiments.result_ledger",
            "recover",
            "--directory",
            str(directory),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize("conflicting_names", [False, True])
@pytest.mark.parametrize("via_cli", [False, True])
def test_foreign_sqlite_recovery_is_rejected_without_any_source_write(
    tmp_path, conflicting_names, via_cli
):
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO sentinel VALUES(1, 'user data must remain exact')"
        )
        if conflicting_names:
            connection.execute(
                "CREATE TABLE run (id TEXT PRIMARY KEY, foreign_value TEXT)"
            )
            connection.execute("INSERT INTO run VALUES('foreign-run','retained')")
            connection.execute(
                "CREATE TABLE attempts (id INTEGER, external_value BLOB)"
            )
        connection.commit()
    finally:
        connection.close()
    before_files = source_files(tmp_path)
    before_facts = readonly_facts(path)
    if via_cli:
        result = invoke_recovery(tmp_path)
        assert result.returncode == 2
        assert json.loads(result.stderr)["error"].startswith("refusing recovery:")
        assert "Traceback" not in result.stderr
    else:
        with pytest.raises(ValueError, match="refusing recovery"):
            execute(tmp_path, recover=True)
    assert readonly_facts(path) == before_facts
    assert source_files(tmp_path) == before_files
    assert sorted(item.name for item in tmp_path.iterdir()) == ["ledger.sqlite3"]


@pytest.mark.parametrize("via_cli", [False, True])
def test_wrong_experiment_author_identity_never_changes_its_fence_or_files(
    tmp_path, via_cli
):
    path = tmp_path / "ledger.sqlite3"
    with Store(path) as store:
        different = replace(declaration(), run_id="another-scientific-experiment")
        different.initialize(store, commit_mode="staged")
        original_fence = store.acquire("other-owner")
    before = source_files(tmp_path)
    if via_cli:
        result = invoke_recovery(tmp_path)
        assert result.returncode == 2
        assert "author identity" in json.loads(result.stderr)["error"]
    else:
        with pytest.raises(ValueError, match="author identity"):
            execute(tmp_path, recover=True)
    # Compare bytes and directory before even opening a read-only SQLite observer.
    assert source_files(tmp_path) == before
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        assert connection.execute("SELECT fence,owner FROM run").fetchone() == (
            original_fence,
            "other-owner",
        )
    finally:
        connection.close()
    assert source_files(tmp_path) == before


@pytest.mark.parametrize("content", [b"not a sqlite database", b""])
def test_invalid_database_is_a_clear_cli_error_without_mutation(tmp_path, content):
    (tmp_path / "ledger.sqlite3").write_bytes(content)
    before = source_files(tmp_path)
    result = invoke_recovery(tmp_path)
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "error"
    assert "refusing recovery" in result.stderr
    assert "Traceback" not in result.stderr
    assert source_files(tmp_path) == before


def test_valid_interrupted_experiment_still_recovers_its_saved_result(tmp_path):
    with pytest.raises(InjectedCrash):
        execute(tmp_path, recover=False, commit_mode="staged", crash_at="result-save")
    with Store(tmp_path / "ledger.sqlite3", readonly=True) as reader:
        saved = reader.state()["results"][0]
    recovered = execute(tmp_path, recover=True)
    assert recovered["status"] == "completed"
    assert recovered["counts"]["attempts"] == 3
    assert recovered["results"][0] == saved


def test_valid_process_death_wal_is_included_in_readonly_preflight(tmp_path):
    directory = tmp_path / "hard-crash"
    result = subprocess.run(  # noqa: S603 -- fixed local synthetic CLI in isolated path
        [
            sys.executable,
            "-m",
            "experiments.result_ledger",
            "demo",
            "--directory",
            str(directory),
            "--commit-mode",
            "staged",
            "--crash-at",
            "result-save",
            "--hard-crash",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 75, result.stderr
    assert (directory / "ledger.sqlite3-wal").is_file()
    recovered = execute(directory, recover=True)
    assert recovered["status"] == "completed"
    assert recovered["counts"]["attempts"] == 3
    assert recovered["attempts"][0]["physical_status"] == "result_observed"
