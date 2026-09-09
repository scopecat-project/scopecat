"""Plan revision identity, hiding and optimistic edits require no runtime process."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from scopecat.kernel.quantity import Quantity
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experiment_plan import (
    ExperimentPlanDefinition,
    ExperimentPlanSave,
)
from scopecat.records.plan_ref import PlanAnalysisSource, PlanConfigRef

from scopecat_server.errors import BackendConflict
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def test_plan_revision_copy_hide_and_exact_reopen(tmp_path: Path) -> None:
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    definition = ExperimentPlanDefinition(
        experiment="signal",
        version="1",
        definition_hash="sha256:" + "1" * 64,
        configuration=PlanConfigRef(
            entry_id="config-1", content_hash="sha256:" + "2" * 64
        ),
        control_edits={
            "frequency": ControlEdit(mode="fixed", value=Quantity(4.8, "GHz"))
        },
        source=PlanAnalysisSource(
            run_id="source-run",
            analysis_id="fit-1",
            publication_hash="sha256:" + "3" * 64,
        ),
    )
    repository = ExperimentPlanRepository(store)
    first = repository.save(
        ExperimentPlanSave(name="Signal study", saved_by="alice", definition=definition)
    )
    assert repository.get(first.ref) == first
    second = repository.save(
        ExperimentPlanSave(
            name="Signal revised",
            saved_by="bob",
            definition=definition.model_copy(update={"inputs": {"offset": 0.1}}),
            previous=first.ref,
        )
    )
    with pytest.raises(BackendConflict, match="edited or deleted"):
        repository.save(
            ExperimentPlanSave(
                name="stale",
                saved_by="alice",
                definition=definition,
                previous=first.ref,
            )
        )
    copied = repository.save(
        ExperimentPlanSave(
            name="Independent copy",
            saved_by="carol",
            definition=definition,
            copied_from=first.ref,
        )
    )
    assert copied.ref.plan_id != first.ref.plan_id
    assert copied.copied_from == first.ref
    repository.hide(second.ref)
    assert repository.list().items == (copied,)
    assert repository.get(first.ref).definition.source == definition.source
    assert repository.get(second.ref) == second
    assert len(repository.list(plan_id=first.ref.plan_id).items) == 2
    store.close()
    reopened = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    reopened.bootstrap()
    assert ExperimentPlanRepository(reopened).get(first.ref) == first
    reopened.close()


def test_plan_content_rejects_execution_permission() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExperimentPlanDefinition.model_validate(
            {
                "experiment": "signal",
                "version": "1",
                "definition_hash": "sha256:" + "1" * 64,
                "configuration": {
                    "entry_id": "config-1",
                    "content_hash": "sha256:" + "2" * 64,
                },
                "request_key": "cannot-persist-this",
            }
        )


def test_schema_64_source_bytes_remain_unchanged(tmp_path: Path) -> None:
    import sqlite3

    from scopecat_server.storage.sqlite.project_store import SchemaVersionError

    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE project_schema("
            "singleton INTEGER PRIMARY KEY, version INTEGER)"
        )
        connection.execute("INSERT INTO project_schema VALUES (1,64)")
        connection.execute("CREATE TABLE retained(value TEXT)")
        connection.execute("INSERT INTO retained VALUES ('original')")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    store = SQLiteProjectStore(SQLiteDatabase(database), tmp_path / "objects")
    with pytest.raises(SchemaVersionError, match="version: 64; expected 65"):
        store.bootstrap()
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_snapshot_retains_hidden_plan_object_and_hash(tmp_path: Path) -> None:
    from scopecat.project import load_project

    from scopecat_server.snapshots import create_snapshot, restore_snapshot

    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    state = root / ".scopecat"
    store = SQLiteProjectStore(
        SQLiteDatabase(state / "control.sqlite3"), state / "objects"
    )
    store.bootstrap()
    repository = ExperimentPlanRepository(store)
    saved = repository.save(
        ExperimentPlanSave(
            name="Retained plan",
            saved_by="alice",
            definition=ExperimentPlanDefinition(
                experiment="signal",
                version="1",
                definition_hash="sha256:" + "1" * 64,
                configuration=PlanConfigRef(
                    entry_id="config-1", content_hash="sha256:" + "2" * 64
                ),
            ),
        )
    )
    repository.hide(saved.ref)
    store.close()
    snapshot = tmp_path / "snapshot"
    create_snapshot(load_project(root / "scopecat.toml"), snapshot)
    restored = tmp_path / "restored"
    restore_snapshot(snapshot, restored)
    restored_store = SQLiteProjectStore(
        SQLiteDatabase(restored / ".scopecat/control.sqlite3"),
        restored / ".scopecat/objects",
    )
    restored_store.bootstrap()
    assert ExperimentPlanRepository(restored_store).get(saved.ref) == saved
    assert ExperimentPlanRepository(restored_store).list().items == ()
    restored_store.close()
