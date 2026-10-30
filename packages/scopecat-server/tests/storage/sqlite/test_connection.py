from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import cast
from unittest.mock import Mock, patch

import pytest

from scopecat_server.storage.sqlite.connection import (
    SQLiteDatabase,
    connect,
)


def test_connect_applies_shared_sqlite_policy(tmp_path: Path) -> None:
    with closing(
        connect(tmp_path / "project.sqlite3", busy_timeout_seconds=0.125)
    ) as connection:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 125


def test_concurrent_writers_both_commit(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "project.sqlite3")
    with database.write_transaction() as connection:
        connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")

    first_entered = Event()
    release_first = Event()
    second_started = Event()

    def hold_first_writer() -> None:
        with database.write_transaction() as connection:
            connection.execute("INSERT INTO values_table VALUES ('first')")
            first_entered.set()
            assert release_first.wait(timeout=5)

    def enter_second_writer() -> None:
        second_started.set()
        with database.write_transaction() as connection:
            connection.execute("INSERT INTO values_table VALUES ('second')")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(hold_first_writer)
        assert first_entered.wait(timeout=5)
        second = executor.submit(enter_second_writer)
        assert second_started.wait(timeout=5)
        release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)

    with closing(database.connect()) as connection:
        rows = connection.execute(
            "SELECT value FROM values_table ORDER BY value"
        ).fetchall()
    assert [row["value"] for row in rows] == ["first", "second"]


def test_read_snapshot_does_not_reserve_the_writer(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "project.sqlite3")
    with closing(database.connect()) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    with database.write_transaction() as connection:
        connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")

    with database.read_transaction() as reader:
        assert reader.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0
        with database.write_transaction() as writer:
            writer.execute("INSERT INTO values_table VALUES ('committed')")
        assert reader.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0

    with closing(database.connect()) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 1
        )


def test_database_reuses_writer_and_reader_connections(tmp_path: Path) -> None:
    with patch(
        "scopecat_server.storage.sqlite.connection.connect",
        wraps=connect,
    ) as open_connection:
        database = SQLiteDatabase(tmp_path / "project.sqlite3")
        with database.write_transaction() as connection:
            connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
        with database.write_transaction() as connection:
            connection.execute("INSERT INTO values_table VALUES ('stored')")
        for _ in range(2):
            with database.read_connection() as connection:
                assert (
                    connection.execute("SELECT value FROM values_table").fetchone()[0]
                    == "stored"
                )
        database.close()

    assert open_connection.call_count == 2


def test_close_checkpoints_and_removes_wal_files(tmp_path: Path) -> None:
    path = tmp_path / "project.sqlite3"
    database = SQLiteDatabase(path)
    with closing(database.connect()) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    with database.write_transaction() as connection:
        connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
        connection.execute("INSERT INTO values_table VALUES ('stored')")

    wal = path.with_name(f"{path.name}-wal")
    assert wal.is_file()

    database.close()

    assert not wal.exists()
    with closing(connect(path)) as connection:
        assert connection.execute("SELECT value FROM values_table").fetchone()[0] == (
            "stored"
        )


def test_busy_close_preserves_committed_wal_for_reopen_and_backup(
    tmp_path: Path,
) -> None:
    """An incomplete TRUNCATE is not a rollback of committed WAL contents."""
    path = tmp_path / "busy.sqlite3"
    database = SQLiteDatabase(path, busy_timeout_seconds=0)
    with database.initialization_connection() as writer:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("CREATE TABLE retained (value TEXT NOT NULL)")
        writer.execute("INSERT INTO retained VALUES ('before')")
    with closing(connect(path)) as external_reader:
        external_reader.execute("BEGIN")
        assert (
            external_reader.execute("SELECT value FROM retained").fetchall()[0][0]
            == "before"
        )
        with database.write_transaction() as writer:
            writer.execute("INSERT INTO retained VALUES ('after')")
        results: list[tuple[int, ...]] = []
        real_writer = database._writer
        assert real_writer is not None
        proxy = Mock(wraps=real_writer)

        def execute_checkpoint(statement: str) -> Mock:
            cursor = real_writer.execute(statement)
            result = cursor.fetchone()
            results.append(tuple(result))
            cursor.close()
            return Mock(fetchone=Mock(return_value=result))

        proxy.execute.side_effect = execute_checkpoint
        database._writer = proxy
        database.close()
        assert len(results) == 1
        assert results[0][0] == 1
        assert results[0][1] > results[0][2]
        proxy.close.assert_called_once_with()
        assert Path(str(path) + "-wal").exists()
        with (
            closing(connect(path)) as reopened,
            closing(sqlite3.connect(tmp_path / "backup.sqlite3")) as backup,
        ):
            assert [
                row[0]
                for row in reopened.execute("SELECT value FROM retained ORDER BY rowid")
            ] == ["before", "after"]
            assert reopened.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            reopened.backup(backup)
            assert backup.execute(
                "SELECT value FROM retained ORDER BY rowid"
            ).fetchall() == [("before",), ("after",)]
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        database.close()
        proxy.close.assert_called_once_with()


def _close_probe_database(tmp_path: Path):
    """Inject only owned connections; the real close coordination is exercised."""
    database = SQLiteDatabase(tmp_path / "probe.sqlite3")
    writer = Mock()
    writer.execute.return_value.fetchone.return_value = (0, 0, 0)
    database._writer = writer
    return database, writer


@pytest.mark.parametrize("stage", ["checkpoint", "writer_close"])
def test_close_waits_for_each_stage_and_serializes_repeated_close(
    tmp_path: Path,
    stage: str,
) -> None:
    database, writer = _close_probe_database(tmp_path)
    entered, release, repeated_started = Event(), Event(), Event()

    def block_stage(*args: object) -> Mock:
        entered.set()
        assert release.wait(timeout=5), "test did not release the close stage"
        return writer.execute.return_value

    if stage == "checkpoint":
        writer.execute.side_effect = block_stage
    else:
        writer.close.side_effect = block_stage

    def repeat_close():
        repeated_started.set()
        database.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(database.close)
        try:
            assert entered.wait(timeout=5)
            second = executor.submit(repeat_close)
            assert repeated_started.wait(timeout=5)
            assert not first.done()
            assert not second.done()
            if stage == "checkpoint":
                writer.close.assert_not_called()
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    database.close()
    writer.execute.assert_called_once_with("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.close.assert_called_once_with()


@pytest.mark.parametrize(
    "stage", ["checkpoint_execute", "checkpoint_fetch", "writer_close"]
)
def test_close_reports_stage_error_releases_writer_and_is_not_retried(
    tmp_path: Path,
    stage: str,
) -> None:
    database, writer = _close_probe_database(tmp_path)
    error = sqlite3.OperationalError(f"injected {stage}")
    if stage == "checkpoint_execute":
        writer.execute.side_effect = error
    elif stage == "checkpoint_fetch":
        writer.execute.return_value.fetchone.side_effect = error
    else:
        writer.close.side_effect = error
    with pytest.raises(sqlite3.OperationalError) as caught:
        database.close()
    assert caught.value is error
    writer.close.assert_called_once_with()
    database.close()
    writer.execute.assert_called_once_with("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.close.assert_called_once_with()
    with pytest.raises(RuntimeError, match="closed"), database.write_transaction():
        pytest.fail("closed database accepted a new writer")


def test_reader_close_error_does_not_leak_remaining_owned_connections(
    tmp_path: Path,
) -> None:
    database, writer = _close_probe_database(tmp_path)
    broken, remaining = Mock(), Mock()
    error = sqlite3.OperationalError("injected reader close")
    broken.close.side_effect = error
    database._readers = [broken, remaining]
    database._idle_readers = [broken, remaining]
    with pytest.raises(sqlite3.OperationalError) as caught:
        database.close()
    assert caught.value is error
    broken.close.assert_called_once_with()
    remaining.close.assert_called_once_with()
    writer.execute.assert_called_once_with("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.close.assert_called_once_with()
    database.close()
    broken.close.assert_called_once_with()
    remaining.close.assert_called_once_with()
    writer.close.assert_called_once_with()


@pytest.mark.parametrize("interrupt_reader", [False, True])
def test_close_preserves_all_cleanup_errors(
    tmp_path: Path,
    interrupt_reader: bool,
) -> None:
    database, writer = _close_probe_database(tmp_path)
    readers = [Mock(), Mock()]
    errors = [
        KeyboardInterrupt("injected interrupt")
        if interrupt_reader
        else sqlite3.OperationalError("first reader close"),
        sqlite3.OperationalError("second reader close"),
        sqlite3.OperationalError("checkpoint"),
        sqlite3.OperationalError("writer close"),
    ]
    database._readers = cast("list[sqlite3.Connection]", readers.copy())
    database._idle_readers = cast("list[sqlite3.Connection]", readers.copy())
    for reader, error in zip(readers, errors[:2], strict=True):
        reader.close.side_effect = error
    writer.execute.side_effect = errors[2]
    writer.close.side_effect = errors[3]
    with pytest.raises(BaseExceptionGroup) as caught:
        database.close()
    assert caught.value.exceptions == tuple(errors)
    assert type(caught.value) is (
        BaseExceptionGroup if interrupt_reader else ExceptionGroup
    )
    database.close()
    for reader in readers:
        reader.close.assert_called_once_with()
    writer.execute.assert_called_once_with("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.close.assert_called_once_with()
