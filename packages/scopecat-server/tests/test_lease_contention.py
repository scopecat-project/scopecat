"""Lease fencing against real SQLite and resource-object writer contention."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    ProcedureWorkerLeaseHeartbeatCommand,
)
from scopecat.automation.worker import ProcedureLeaseLostError, _ProcedureLeaseAuthority

from scopecat_server import BackendConflict
from scopecat_server.services.automation import AutomationService
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.connection import SQLiteBusyError, SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.resource_objects import put_resource_object
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


@pytest.fixture
def lease_store(
    tmp_path: Path,
) -> Generator[
    tuple[SQLiteDatabase, SQLiteAutomationStore, AutomationService, list[datetime]]
]:
    database = SQLiteDatabase(tmp_path / "control.sqlite3", busy_timeout_seconds=0.02)
    SQLiteProjectStore(database, tmp_path / "objects").bootstrap()
    store = SQLiteAutomationStore(database)
    clock = [datetime(2026, 8, 18, tzinfo=UTC)]
    service = AutomationService(
        store,
        runs=SQLiteRunRepository(database, tmp_path / "objects"),
        lease_ttl=timedelta(seconds=30),
        clock=lambda: clock[0],
    )
    try:
        yield database, store, service, clock
    finally:
        database.close()


def _acquire(service: AutomationService):
    run = service.submit(
        ProcedureSubmitCommand(
            request_key="contention",
            definition=ProcedureDefinitionRef(
                id="contention", version="1", fingerprint="sha256:" + "1" * 64
            ),
            intent={},
        )
    ).run
    return service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=run.procedure_run_id,
            worker_id="original",
            expected_run_revision=run.revision,
        )
    )


@pytest.mark.parametrize("operation", ["commit", "object_fsync"])
@pytest.mark.parametrize("after_release", ["live", "expired", "replaced"])
def test_real_writer_busy_does_not_renew_or_override_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, after_release: str
) -> None:
    entered, release, armed = Event(), Event(), Event()

    def pause_once() -> None:
        if armed.is_set():
            armed.clear()
            entered.set()
            assert release.wait(5), "test did not release the writer"

    class PausedCommit(sqlite3.Connection):
        def commit(self) -> None:
            if operation == "commit":
                pause_once()
            super().commit()

    connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: connect(*args, factory=PausedCommit, **kwargs),
    )
    fsync = os.fsync

    def paused_fsync(fd: int) -> None:
        pause_once()
        fsync(fd)

    database = SQLiteDatabase(tmp_path / "control.sqlite3", busy_timeout_seconds=0.02)
    project = SQLiteProjectStore(database, tmp_path / "objects")
    project.bootstrap()
    store = SQLiteAutomationStore(database)
    clock = [datetime(2026, 8, 18, tzinfo=UTC)]
    service = AutomationService(
        store,
        runs=SQLiteRunRepository(database, tmp_path / "objects"),
        lease_ttl=timedelta(seconds=30),
        clock=lambda: clock[0],
    )
    acquired = _acquire(service)
    command = ProcedureWorkerLeaseHeartbeatCommand(
        procedure_run_id=acquired.run.procedure_run_id,
        lease_token=acquired.lease.lease_token,
    )
    with database.read_connection() as connection:
        original = store.read_lease_in_transaction(connection, command.procedure_run_id)
    if operation == "object_fsync":
        monkeypatch.setattr(os, "fsync", paused_fsync)

    def write():
        if operation == "object_fsync":
            return put_resource_object(
                database, project.objects, "run", "r", b"retained"
            )
        with database.write_transaction() as connection:
            connection.execute("CREATE TABLE contention_marker (value TEXT)")
            connection.execute("INSERT INTO contention_marker VALUES ('committed')")
        return None

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            armed.set()
            writer = executor.submit(write)
            try:
                assert entered.wait(2)
                assert not writer.done()
                with pytest.raises(SQLiteBusyError, match="writer is busy"):
                    service.heartbeat_lease(command)
                with database.read_connection() as connection:
                    assert (
                        store.read_lease_in_transaction(
                            connection, command.procedure_run_id
                        )
                        == original
                    )
                assert service.get(command.procedure_run_id) == acquired.run
            finally:
                release.set()
            stored = writer.result(timeout=2)
        if operation == "object_fsync":
            assert stored is not None
            from scopecat_server.storage.sqlite.resource_objects import resource_objects

            assert (
                resource_objects(database, project.objects, "run", "r").read(
                    stored.digest
                )
                == b"retained"
            )
        else:
            with database.read_connection() as connection:
                assert (
                    connection.execute(
                        "SELECT value FROM contention_marker"
                    ).fetchone()[0]
                    == "committed"
                )
        clock[0] = acquired.lease.expires_at - timedelta(seconds=1)
        if after_release != "live":
            clock[0] = acquired.lease.expires_at
        if after_release == "replaced":
            replacement = service.acquire_lease(
                ProcedureWorkerLeaseAcquireCommand(
                    procedure_run_id=command.procedure_run_id,
                    worker_id="replacement",
                    expected_run_revision=acquired.run.revision,
                )
            )
            assert replacement.lease.lease_token != command.lease_token
        with database.read_connection() as connection:
            before = store.read_lease_in_transaction(
                connection, command.procedure_run_id
            )
        if after_release == "live":
            receipt = service.heartbeat_lease(command)
            assert receipt.lease.lease_token == command.lease_token
            assert receipt.run.revision == acquired.run.revision
            assert receipt.lease.expires_at == clock[0] + timedelta(seconds=30)
        else:
            with pytest.raises(
                BackendConflict, match="lease is absent, stale, or expired"
            ):
                service.heartbeat_lease(command)
            with database.read_connection() as connection:
                assert (
                    store.read_lease_in_transaction(
                        connection, command.procedure_run_id
                    )
                    == before
                )
    finally:
        release.set()
        database.close()


def test_committed_renewal_with_lost_response_still_fences_worker(
    lease_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, store, service, clock = lease_store
    acquired = _acquire(service)
    clock[0] += timedelta(seconds=10)
    monkeypatch.setattr(
        "scopecat.automation.worker.datetime", SimpleNamespace(now=lambda _tz: clock[0])
    )
    monkeypatch.setattr("scopecat.automation.worker._heartbeat_delay", lambda _lease: 0)
    control = Mock()

    def lose_response(command):
        service.heartbeat_lease(command)
        raise ConnectionError("renewal response lost after commit")

    control.heartbeat_procedure_worker_lease.side_effect = lose_response
    authority = _ProcedureLeaseAuthority(control, acquired)
    authority._heartbeat_loop()
    assert control.heartbeat_procedure_worker_lease.call_count == 1
    assert authority._lease == acquired.lease
    with database.read_connection() as connection:
        durable = store.read_lease_in_transaction(
            connection, acquired.run.procedure_run_id
        )
    assert durable is not None
    assert durable.renewed_at == clock[0]
    assert durable.expires_at == clock[0] + timedelta(seconds=30)
    assert durable.expires_at > acquired.lease.expires_at
    complete = Mock()
    with pytest.raises(ProcedureLeaseLostError, match="response lost"):
        authority.fenced_call("complete_step", complete)
    complete.assert_not_called()
    assert service.get(acquired.run.procedure_run_id) == acquired.run
