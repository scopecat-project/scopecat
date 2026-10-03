from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path
from typing import cast

import pytest
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.registry.records import (
    ConfigRegistryEntry,
)
from scopecat.config.registry.service import (
    ConfigRegistryMutationResult,
    ConfigRegistryUnitOfWorkFactory,
    ConfigRevision,
    DirectConfigRevisionSource,
    load_config_registry_entry_snapshot,
    load_config_registry_page,
    save_config_revision,
)
from scopecat.kernel.errors import Conflict, StorageError
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.run import RunSnapshot
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat_testkit.config_registry import (
    initialize_setup,
    load_config_registry_config,
)
from scopecat_testkit.paths import CORE_FIXTURE_DIR
from scopecat_testkit.server.runtime import SQLiteTestRunRepository

from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


# Test-only storage observations. Product callers use the exact snapshot/page
# services instead of these former convenience wrappers.
def list_config_registry_entries(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> list[ConfigRegistryEntry]:
    with unit_of_work() as work:
        return list(work.registry.list_entries())


def _save_direct_revision(
    *,
    config: ConfigProfileSnapshot,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    entry_id: str,
    actor: str,
    note: str = "",
) -> ConfigRegistryMutationResult:
    return save_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(config),
            entry_id=entry_id,
            actor=actor,
            note=note,
        ),
        unit_of_work=unit_of_work,
    )


def _store(tmp_path: Path) -> SQLiteConfigRegistryStore:
    database = tmp_path / "control.sqlite3"
    sqlite = SQLiteDatabase(database)
    SQLiteProjectStore(sqlite, tmp_path / "objects").bootstrap()
    runs = SQLiteTestRunRepository(sqlite, tmp_path / "objects")
    return SQLiteConfigRegistryStore(sqlite, runs=runs)


def test_publish_is_idempotent_and_round_trips(tmp_path: Path) -> None:
    unit_of_work = _store(tmp_path).write_unit_of_work
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")

    initialize_setup(config, unit_of_work=unit_of_work)
    first = _save_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="contract-entry",
        actor="contract",
        note="same request",
    ).entry
    repeated = _save_direct_revision(
        config=config.model_copy(deep=True),
        unit_of_work=unit_of_work,
        entry_id="contract-entry",
        actor="contract",
        note="same request",
    ).entry

    assert repeated == first
    assert (
        load_config_registry_config(
            entry_id=first.id,
            unit_of_work=unit_of_work,
        )
        == config
    )
    assert list_config_registry_entries(unit_of_work=unit_of_work) == [first]


def test_duplicate_identity_rejects_different_request(tmp_path: Path) -> None:
    unit_of_work = _store(tmp_path).write_unit_of_work
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    initialize_setup(config, unit_of_work=unit_of_work)
    _save_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="contract-conflict",
        actor="first",
    )

    with pytest.raises(Conflict) as captured:
        _save_direct_revision(
            config=config,
            unit_of_work=unit_of_work,
            entry_id="contract-conflict",
            actor="different",
        )
    assert captured.value.problems[0].code == "config_registry.duplicate_entry"


def test_registry_and_run_reads_share_one_database(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runs = cast("SQLiteTestRunRepository", store.runs)
    runs.write_snapshot(
        RunSnapshot(
            scientific_binding=ResolvedScientificBinding(
                subject=UnboundSubject(),
                config_content_hash=f"sha256:{'0' * 64}",
                setup_content_hash="sha256:" + "0" * 64,
            ),
            run_id="run-shared",
            config_content_hash=f"sha256:{'0' * 64}",
        )
    )
    runs.write_text(
        "run-shared",
        "records/value.txt",
        "value",
    )
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")

    initialize_setup(config, unit_of_work=store.write_unit_of_work)
    _save_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="shared",
        actor="test",
    )

    with store.write_unit_of_work() as work:
        assert work.runs is store.runs
        assert work.runs.read_text("run-shared", "records/value.txt") == "value\n"


def test_listing_reads_entry_metadata_without_loading_each_config(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    initialize_setup(config, unit_of_work=store.write_unit_of_work)
    _save_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="first",
        actor="test",
    )
    _save_direct_revision(
        config=config.model_copy(update={"id": "second"}),
        unit_of_work=store.write_unit_of_work,
        entry_id="second",
        actor="test",
    )
    statements: list[str] = []
    connection = sqlite3.connect(store.database, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.set_trace_callback(statements.append)

    entries = list_config_registry_entries(
        unit_of_work=partial(store.borrowed_unit_of_work, connection)
    )

    assert [entry.id for entry in entries] == ["first", "second"]
    entry_reads = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
        and "config_registry_entries" in statement
    ]
    assert len(entry_reads) == 1
    assert "config_json" not in entry_reads[0]
    connection.close()


def test_registry_pages_use_stable_newest_first_cursors(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    initialize_setup(config, unit_of_work=store.write_unit_of_work)
    for index in range(1, 4):
        _save_direct_revision(
            config=config.model_copy(update={"id": f"config-{index}"}),
            unit_of_work=store.write_unit_of_work,
            entry_id=f"entry-{index}",
            actor="test",
        )

    with store.read_unit_of_work() as work:
        entry_head = work.registry.list_entry_page(limit=2, before=None)
        entry_tail = work.registry.list_entry_page(
            limit=2,
            before=entry_head.next_cursor,
        )
    assert [entry.id for entry in entry_head.items] == ["entry-3", "entry-2"]
    assert entry_head.next_cursor == 2
    assert [entry.id for entry in entry_tail.items] == ["entry-1"]
    assert entry_tail.next_cursor is None


def test_aggregate_reads_open_one_unit_of_work(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    initialize_setup(config, unit_of_work=store.write_unit_of_work)
    _save_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="active",
        actor="test",
    )
    opens = 0

    def counted_unit_of_work():
        nonlocal opens
        opens += 1
        return store.write_unit_of_work()

    registry = load_config_registry_page(
        limit=50,
        before=None,
        unit_of_work=counted_unit_of_work,
    )
    assert opens == 1
    opens = 0
    entry = load_config_registry_entry_snapshot(
        entry_id="active",
        unit_of_work=counted_unit_of_work,
    )
    assert opens == 1
    assert registry.entries == (entry.entry,)
    assert entry.config == config


def test_publish_rolls_back_together(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_revision
            BEFORE INSERT ON config_registry_entries
            BEGIN
                SELECT RAISE(ABORT, 'injected failure');
            END
            """
        )

    initialize_setup(config, unit_of_work=store.write_unit_of_work)
    with pytest.raises(StorageError):
        _save_direct_revision(
            config=config,
            unit_of_work=store.write_unit_of_work,
            entry_id="rolled-back",
            actor="test",
        )

    assert list_config_registry_entries(unit_of_work=store.read_unit_of_work) == []


def test_borrowed_unit_of_work_leaves_transaction_and_connection_owned_by_caller(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    initialize_setup(config, unit_of_work=store.write_unit_of_work)
    connection = sqlite3.connect(store.database, isolation_level=None)
    connection.row_factory = sqlite3.Row
    with store.borrowed_unit_of_work(connection) as work:
        assert work.registry.list_entries() == ()
    assert not connection.in_transaction

    connection.execute("BEGIN IMMEDIATE")

    _save_direct_revision(
        config=config,
        unit_of_work=partial(store.borrowed_unit_of_work, connection),
        entry_id="borrowed",
        actor="test",
    )

    assert connection.in_transaction
    assert (
        connection.execute("SELECT COUNT(*) FROM config_registry_entries").fetchone()[0]
        == 1
    )
    connection.rollback()
    assert (
        connection.execute("SELECT COUNT(*) FROM config_registry_entries").fetchone()[0]
        == 0
    )
    connection.close()


def test_borrowed_unit_of_work_only_scopes_registry_access(tmp_path: Path) -> None:
    store = _store(tmp_path)
    connection = sqlite3.connect(store.database, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN IMMEDIATE")
    work = store.borrowed_unit_of_work(connection)

    with pytest.raises(RuntimeError, match="entered twice"), work:
        assert work.registry.list_entries() == ()
        work.__enter__()

    assert connection.in_transaction
    with pytest.raises(RuntimeError, match="has not been entered"):
        _ = work.registry
    connection.rollback()
    connection.close()
