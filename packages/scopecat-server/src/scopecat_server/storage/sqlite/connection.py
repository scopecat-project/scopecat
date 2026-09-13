"""Shared SQLite connection and transaction policy."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock

DEFAULT_BUSY_TIMEOUT_SECONDS = 5.0


class SQLiteBusyError(RuntimeError):
    """The project database writer is temporarily owned elsewhere."""


class SQLiteDatabase:
    """Coordinate every connection to one daemon-owned SQLite database."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
    ) -> None:
        self.path = Path(path)
        self.busy_timeout_seconds = busy_timeout_seconds
        self._writer_lock = RLock()
        self._connections_lock = Lock()
        self._writer: sqlite3.Connection | None = None
        self._idle_readers: list[sqlite3.Connection] = []
        self._readers: list[sqlite3.Connection] = []
        self._closed = False

    def connect(self) -> sqlite3.Connection:
        """Open an independent caller-owned connection."""

        return connect(
            self.path,
            busy_timeout_seconds=self.busy_timeout_seconds,
        )

    @contextmanager
    def initialization_connection(self) -> Generator[sqlite3.Connection]:
        """Initialize on the retained writer, before serving concurrent work.

        Journal setup and schema scripts manage their own transactions. Keep
        this connection alive so bootstrap does not close the last WAL client
        and force a checkpoint before the daemon can become healthy.
        """
        with self._writer_lock:
            connection = self._writer_connection()
            try:
                yield connection
            finally:
                connection.rollback()

    @contextmanager
    def read_connection(self) -> Generator[sqlite3.Connection]:
        """Borrow one reusable autocommit connection for a bounded read."""

        connection = self._acquire_reader()
        try:
            yield connection
        finally:
            connection.rollback()
            self._release_reader(connection)

    @contextmanager
    def read_transaction(self) -> Generator[sqlite3.Connection]:
        """Borrow a reusable connection and open one consistent snapshot."""

        with self.read_connection() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                connection.rollback()

    @contextmanager
    def write_transaction(self) -> Generator[sqlite3.Connection]:
        """Serialize the project's single SQLite writer inside the daemon."""

        if not self._writer_lock.acquire(timeout=self.busy_timeout_seconds):
            raise SQLiteBusyError("project database writer is busy")
        try:
            connection = self._writer_connection()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
        except sqlite3.OperationalError as error:
            primary_code = error.sqlite_errorcode & 0xFF
            if primary_code not in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                raise
            raise SQLiteBusyError("project database writer is busy") from error
        finally:
            self._writer_lock.release()

    def close(self) -> None:
        """Checkpoint WAL state and close every daemon-owned connection."""

        with self._writer_lock:
            with self._connections_lock:
                if self._closed:
                    return
                self._closed = True
                readers = tuple(self._readers)
                self._readers.clear()
                self._idle_readers.clear()
                writer = self._writer
                self._writer = None
            for connection in readers:
                connection.close()
            if writer is not None:
                try:
                    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                finally:
                    writer.close()

    def _writer_connection(self) -> sqlite3.Connection:
        with self._connections_lock:
            self._require_open()
            if self._writer is None:
                self._writer = connect(
                    self.path,
                    busy_timeout_seconds=self.busy_timeout_seconds,
                    check_same_thread=False,
                )
            return self._writer

    def _acquire_reader(self) -> sqlite3.Connection:
        with self._connections_lock:
            self._require_open()
            if self._idle_readers:
                return self._idle_readers.pop()
            connection = connect(
                self.path,
                busy_timeout_seconds=self.busy_timeout_seconds,
                check_same_thread=False,
            )
            self._readers.append(connection)
            return connection

    def _release_reader(self, connection: sqlite3.Connection) -> None:
        with self._connections_lock:
            if self._closed:
                connection.close()
                return
            self._idle_readers.append(connection)

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLite database is closed")


def connect(
    database: str | Path,
    *,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a connection with the project-wide SQLite policy."""

    connection = sqlite3.connect(
        database,
        isolation_level=None,
        timeout=busy_timeout_seconds,
        check_same_thread=check_same_thread,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = NORMAL")
    busy_timeout_ms = round(busy_timeout_seconds * 1000)
    connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    return connection


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_SECONDS",
    "SQLiteBusyError",
    "SQLiteDatabase",
    "connect",
]
