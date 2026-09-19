from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path
from typing import cast

import pytest
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.registry.records import (
    ConfigActivationOperation,
    ConfigPublishOperation,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    config_activation_intent_hash,
)
from scopecat.config.registry.service import (
    ConfigRegistryMutationResult,
    ConfigRegistryUnitOfWorkFactory,
    ConfigRevision,
    DirectConfigRevisionSource,
    activate_config_registry_entry,
    load_active_config_registry_snapshot,
    load_config_registry_entry_snapshot,
    load_config_registry_page,
    publish_config_revision,
    resolve_config_registry_config_source,
)
from scopecat.daemon.wire import (
    ConfigActivationReceipt,
    ConfigPublishCommand,
    ConfigPublishReceipt,
)
from scopecat.daemon.wire import (
    DirectConfigRevisionSource as WireDirectConfigRevisionSource,
)
from scopecat.kernel.errors import Conflict, StorageError
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.run import ConfigRegistryRunConfigSource, RunSnapshot
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat_testkit.config_registry import load_config_registry_config
from scopecat_testkit.paths import CORE_FIXTURE_DIR
from scopecat_testkit.server.runtime import SQLiteTestRunRepository

from scopecat_server.storage.sqlite.config_operations import SQLiteConfigOperationStore
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


# Test-only storage observations. Product callers use the exact snapshot/page
# services instead of these former convenience wrappers.
def current_config_registry_generation(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> int:
    with unit_of_work() as work:
        return work.registry.current_generation()


def list_config_registry_entries(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> list[ConfigRegistryEntry]:
    with unit_of_work() as work:
        return list(work.registry.list_entries())


def load_active_config_registry_activation(
    *, unit_of_work: ConfigRegistryUnitOfWorkFactory
) -> ConfigRegistryActivationRecord:
    return load_active_config_registry_snapshot(unit_of_work=unit_of_work).activation


def _publish_direct_revision(
    *,
    config: ConfigProfileSnapshot,
    unit_of_work: ConfigRegistryUnitOfWorkFactory,
    entry_id: str,
    actor: str,
    expected_generation: int | None = None,
    note: str = "",
) -> ConfigRegistryMutationResult:
    generation = (
        current_config_registry_generation(unit_of_work=unit_of_work)
        if expected_generation is None
        else expected_generation
    )
    return publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(config),
            entry_id=entry_id,
            actor=actor,
            note=note,
        ),
        unit_of_work=unit_of_work,
        expected_generation=generation,
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

    first = _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="contract-entry",
        actor="contract",
        note="same request",
    ).entry
    repeated = _publish_direct_revision(
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
    _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="contract-conflict",
        actor="first",
    )

    with pytest.raises(Conflict) as captured:
        _publish_direct_revision(
            config=config,
            unit_of_work=unit_of_work,
            entry_id="contract-conflict",
            actor="different",
        )
    assert captured.value.problems[0].code == "config_registry.duplicate_entry"


def test_activation_uses_generation_cas_and_resolves_source(
    tmp_path: Path,
) -> None:
    unit_of_work = _store(tmp_path).write_unit_of_work
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    result = _publish_direct_revision(
        config=config,
        unit_of_work=unit_of_work,
        entry_id="contract-active",
        actor="contract",
        expected_generation=0,
    )
    entry = result.entry
    activation = result.activation
    assert activation is not None

    assert activation.generation == 1
    assert current_config_registry_generation(unit_of_work=unit_of_work) == 1
    assert (
        load_active_config_registry_activation(unit_of_work=unit_of_work) == activation
    )
    resolved, source = resolve_config_registry_config_source(
        selector="active",
        unit_of_work=unit_of_work,
    )
    assert resolved == config
    assert isinstance(source, ConfigRegistryRunConfigSource)
    assert source.entry_id == entry.id

    with pytest.raises(Conflict) as captured:
        _publish_direct_revision(
            config=config,
            unit_of_work=unit_of_work,
            entry_id="stale-generation",
            actor="contract",
            expected_generation=2,
        )
    assert captured.value.problems[0].code == "config_registry.conflict"

    with pytest.raises(Conflict) as repeated:
        activate_config_registry_entry(
            entry_id=entry.id,
            unit_of_work=unit_of_work,
            actor="contract",
            expected_generation=0,
        )
    assert repeated.value.problems[0].code == "config_registry.conflict"


def test_activation_operation_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    operations = SQLiteConfigOperationStore(store.sqlite)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    result = _publish_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="operation-entry",
        actor="contract",
        expected_generation=0,
    )
    activation = result.activation
    assert activation is not None
    operation = ConfigActivationOperation(
        operation_id="activation:round-trip",
        intent_hash=config_activation_intent_hash(
            entry_id=result.entry.id,
            expected_generation=1,
            actor="operator",
            note="already active",
        ),
        entry_id=result.entry.id,
        expected_generation=1,
        actor="operator",
        note="already active",
        activation_generation=activation.generation,
    )
    receipt = ConfigActivationReceipt(
        operation=operation,
        activation=activation,
    )

    assert operations.find(operation.operation_id) is None
    with store.sqlite.write_transaction() as connection:
        operations.commit_in_transaction(connection, receipt)
        assert (
            operations.find_in_transaction(
                connection,
                operation.operation_id,
            )
            == receipt
        )

    assert operations.find(operation.operation_id) == receipt


def test_publish_operation_round_trips_exact_receipt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    operations = SQLiteConfigOperationStore(store.sqlite)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    result = _publish_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="publish-operation-entry",
        actor="contract",
        expected_generation=0,
    )
    activation = result.activation
    assert activation is not None
    command = ConfigPublishCommand(
        operation_id="publish:round-trip",
        source=WireDirectConfigRevisionSource(config=config),
        entry_id=result.entry.id,
        actor="contract",
        expected_generation=0,
    )
    operation = ConfigPublishOperation(
        operation_id=command.operation_id,
        intent_hash=command.intent_hash,
        source_intent_hash=command.source_intent_hash,
        entry_id=command.entry_id,
        expected_generation=command.expected_generation,
        actor=command.actor,
        note=command.note,
        activation_generation=activation.generation,
    )
    receipt = ConfigPublishReceipt(
        operation=operation,
        entry=result.entry,
        activation=activation,
    )

    with store.sqlite.write_transaction() as connection:
        operations.commit_in_transaction(connection, receipt)

    assert operations.find(operation.operation_id) == receipt


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

    _publish_direct_revision(
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
    _publish_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="first",
        actor="test",
    )
    _publish_direct_revision(
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


def test_registry_and_activation_pages_use_stable_newest_first_cursors(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    for index in range(1, 4):
        _publish_direct_revision(
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
        activation_head = work.registry.list_activation_page(limit=2, before=None)
        activation_tail = work.registry.list_activation_page(
            limit=2,
            before=activation_head.next_cursor,
        )
        first_activation = work.registry.read_activation(1)

    assert [entry.id for entry in entry_head.items] == ["entry-3", "entry-2"]
    assert entry_head.next_cursor == 2
    assert [entry.id for entry in entry_tail.items] == ["entry-1"]
    assert entry_tail.next_cursor is None
    assert [record.generation for record in activation_head.items] == [3, 2]
    assert activation_head.next_cursor == 2
    assert [record.generation for record in activation_tail.items] == [1]
    assert activation_tail.next_cursor is None
    assert first_activation.entry_id == "entry-1"


def test_aggregate_reads_open_one_unit_of_work(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    _publish_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="active",
        actor="test",
        expected_generation=0,
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
    active = load_active_config_registry_snapshot(unit_of_work=counted_unit_of_work)
    assert opens == 1
    opens = 0
    entry = load_config_registry_entry_snapshot(
        entry_id="active",
        unit_of_work=counted_unit_of_work,
    )
    assert opens == 1
    assert registry.activation == active.activation
    assert active.entry == entry.entry
    assert active.config == entry.config


def test_publish_rolls_back_together(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_initial_activation
            BEFORE INSERT ON config_registry_activations
            BEGIN
                SELECT RAISE(ABORT, 'injected failure');
            END
            """
        )

    with pytest.raises(StorageError):
        _publish_direct_revision(
            config=config,
            unit_of_work=store.write_unit_of_work,
            entry_id="rolled-back",
            actor="test",
            expected_generation=0,
        )

    assert list_config_registry_entries(unit_of_work=store.read_unit_of_work) == []


def test_borrowed_unit_of_work_leaves_transaction_and_connection_owned_by_caller(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    connection = sqlite3.connect(store.database, isolation_level=None)
    connection.row_factory = sqlite3.Row
    with store.borrowed_unit_of_work(connection) as work:
        assert work.registry.list_entries() == ()
    assert not connection.in_transaction

    connection.execute("BEGIN IMMEDIATE")

    _publish_direct_revision(
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


def test_generation_cas_is_shared_across_store_instances(tmp_path: Path) -> None:
    store = _store(tmp_path)
    peer = SQLiteConfigRegistryStore(store.sqlite, runs=store.runs)
    config = load_config_snapshot_document(CORE_FIXTURE_DIR / "config-snapshot.json")
    _publish_direct_revision(
        config=config,
        unit_of_work=store.write_unit_of_work,
        entry_id="first",
        actor="test",
    )
    _publish_direct_revision(
        config=config.model_copy(update={"id": "second-config"}),
        unit_of_work=store.write_unit_of_work,
        entry_id="second",
        actor="test",
    )
    activate_config_registry_entry(
        entry_id="first",
        unit_of_work=store.write_unit_of_work,
        actor="first",
        expected_generation=2,
    )

    with pytest.raises(Conflict) as captured:
        activate_config_registry_entry(
            entry_id="second",
            unit_of_work=peer.write_unit_of_work,
            actor="second",
            expected_generation=2,
        )

    assert captured.value.problems[0].code == "config_registry.conflict"
