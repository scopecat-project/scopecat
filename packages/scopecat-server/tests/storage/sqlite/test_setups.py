from __future__ import annotations

from pathlib import Path

import pytest
from scopecat.project import load_project
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.scientific_scope import setup_content_hash
from scopecat.records.setup import ExecutableSetupSnapshot, SetupRevision
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import SQLiteTestRunRepository
from scopecat_testkit.setup_records import retained_setup_revision

from scopecat_server.setup_access import setup_config
from scopecat_server.snapshots import create_snapshot, restore_snapshot
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def _store(root: Path) -> SQLiteConfigRegistryStore:
    state = root / ".scopecat"
    database = SQLiteDatabase(state / "control.sqlite3")
    SQLiteProjectStore(database, state / "objects").bootstrap()
    return SQLiteConfigRegistryStore(
        database, runs=SQLiteTestRunRepository(database, state / "objects")
    )


def _revision(name: str) -> SetupRevision:
    setup = ExecutableSetupSnapshot.from_config(load_config())
    return retained_setup_revision(id=name, setup=setup, actor="maintainer")


def test_setup_payload_preserves_execution_identity_and_explicit_composition() -> None:
    config = load_config()
    setup = ExecutableSetupSnapshot.from_config(config)
    assert setup.execution_content_hash == setup_content_hash(config)
    assert setup.compose(config) == config
    assert "parameter_catalog" not in setup.model_dump()
    assert "parameter_snapshot" not in setup.model_dump()
    other = config.model_copy(update={"id": "another-profile"})
    composed = setup.compose(other)
    assert composed.id == "another-profile"
    assert composed.parameter_catalog == other.parameter_catalog
    assert composed.parameter_snapshot == other.parameter_snapshot


def test_setup_transaction_rollback_and_snapshot_restore(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    store = _store(root)
    revision = _revision("setup-a")
    with (
        pytest.raises(RuntimeError, match="abort"),
        store.write_unit_of_work() as work,
    ):
        work.setups.save_revision(revision)
        raise RuntimeError("abort")
    with store.read_unit_of_work() as work:
        assert work.setups.list_revisions() == ()
    with store.write_unit_of_work() as work:
        work.setups.save_revision(revision)
    project = load_project(root / "scopecat.toml")
    create_snapshot(project, tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    restored = _store(tmp_path / "restored")
    with restored.read_unit_of_work() as work:
        assert work.setups.read_revision(revision.id) == revision


def test_reopened_setup_retains_software_model_inputs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scenario = SoftwareExecutionScenario(
        id="noise",
        label="Noisy protocol",
        model_id="protocol",
        model_version="1",
        seed=19,
        settings={"noise": 0.02},
        capabilities=("capture",),
        limitations=("No physical device",),
    )
    config = load_config()
    config.system.scenario = scenario
    setup = ExecutableSetupSnapshot.from_config(config)
    revision = retained_setup_revision(id="software", setup=setup, actor="maintainer")
    with store.write_unit_of_work() as work:
        work.setups.save_revision(revision)
    reopened = _store(tmp_path)
    with reopened.write_unit_of_work() as work:
        retained = work.setups.read_revision("software")
    assert retained == revision
    assert retained.setup.scenario == scenario
    assert retained.setup.compose(config).system.scenario == scenario
    projected = setup_config(retained)
    assert projected.system.scenario == scenario
    assert setup_content_hash(projected) == retained.setup.execution_content_hash
    assert (
        ExecutableSetupSnapshot.from_config(projected).content_hash
        == retained.setup.content_hash
    )
