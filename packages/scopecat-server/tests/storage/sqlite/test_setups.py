from __future__ import annotations

from pathlib import Path

import pytest
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.project import load_project
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.scientific_scope import setup_content_hash
from scopecat.records.setup import ExecutableSetupSnapshot, SetupRevision
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import SQLiteTestRunRepository

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
    return SetupRevision(
        id=name, content_hash=setup.content_hash, setup=setup, actor="maintainer"
    )


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


def test_setup_activation_is_independent_and_replays_original_receipt(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    revision = _revision("setup-a")
    intent = sha256_json_hash({"operation": "a"})
    with store.write_unit_of_work() as work:
        assert work.setups.read_current() is None
        saved = work.setups.save_revision(revision)
        assert work.setups.save_revision(_revision("setup-a")) == saved
        first = work.setups.activate(
            revision=saved.ref,
            expected_generation=0,
            operation_id="activate-a",
            intent_hash=intent,
            actor="maintainer",
            note="first",
        )
        second = work.setups.save_revision(_revision("setup-b"))
        work.setups.activate(
            revision=second.ref,
            expected_generation=1,
            operation_id="activate-b",
            intent_hash=sha256_json_hash({"operation": "b"}),
            actor="maintainer",
            note="",
        )
        replay = work.setups.activate(
            revision=saved.ref,
            expected_generation=0,
            operation_id="activate-a",
            intent_hash=intent,
            actor="maintainer",
            note="first",
        )
        assert replay == first
        assert work.registry.current_generation() == 0
        assert work.registry.list_entries() == ()
        current = work.setups.read_current()
        assert current is not None
        assert current.activation.generation == 2
        assert current.revision == second
    with store.write_unit_of_work() as work:
        with pytest.raises(ValueError, match="different intent"):
            work.setups.activate(
                revision=revision.ref,
                expected_generation=0,
                operation_id="activate-a",
                intent_hash=sha256_json_hash({"changed_declaration": True}),
                actor="maintainer",
                note="first",
            )
        with pytest.raises(ValueError, match="generation changed"):
            work.setups.activate(
                revision=revision.ref,
                expected_generation=0,
                operation_id="stale",
                intent_hash=intent,
                actor="maintainer",
                note="first",
            )
        with pytest.raises(ValueError, match="different content or provenance"):
            work.setups.save_revision(revision.model_copy(update={"actor": "other"}))


def test_setup_transaction_rollback_and_snapshot_restore(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    store = _store(root)
    revision = _revision("setup-a")
    intent = sha256_json_hash({"operation": "a"})
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
        active = work.setups.activate(
            revision=revision.ref,
            expected_generation=0,
            operation_id="activate-a",
            intent_hash=intent,
            actor="maintainer",
            note="",
        )
        receipt = work.setups.read_activation_operation("activate-a")
    project = load_project(root / "scopecat.toml")
    create_snapshot(project, tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    restored = _store(tmp_path / "restored")
    with restored.read_unit_of_work() as work:
        assert work.setups.read_revision(revision.id) == revision
        assert work.setups.read_current() == active
        assert work.setups.read_activation_operation("activate-a") == receipt


def test_initial_config_publication_bootstraps_both_owners_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat.config.registry.service import (
        ConfigRevision,
        DirectConfigRevisionSource,
        publish_config_revision,
    )

    from scopecat_server.storage.sqlite.config_registry import (
        SQLiteConfigRegistryRepository,
    )

    store = _store(tmp_path)
    revision = ConfigRevision(
        source=DirectConfigRevisionSource(load_config()),
        entry_id="initial",
        actor="maintainer",
    )

    def abort_activation(
        self: SQLiteConfigRegistryRepository,
        *,
        expected_generation: int,
        record: object,
    ) -> None:
        raise RuntimeError("abort config activation")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            SQLiteConfigRegistryRepository, "commit_activation", abort_activation
        )
        with pytest.raises(RuntimeError, match="abort config activation"):
            publish_config_revision(
                revision=revision,
                unit_of_work=store.write_unit_of_work,
                expected_generation=0,
            )
    with store.read_unit_of_work() as work:
        assert work.setups.list_revisions() == ()
        assert work.setups.read_current() is None
        assert work.registry.list_entries() == ()
        assert work.registry.current_generation() == 0
    published = publish_config_revision(
        revision=revision, unit_of_work=store.write_unit_of_work, expected_generation=0
    )
    with store.read_unit_of_work() as work:
        setup = work.setups.read_current()
        assert setup is not None
        assert setup.revision.setup.execution_content_hash == setup_content_hash(
            load_config()
        )
        assert setup.activation.generation == 1
        assert published.activation is not None
        assert (
            work.registry.current_generation() == published.activation.generation == 1
        )
    publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(
                load_config().model_copy(update={"id": "second"})
            ),
            entry_id="second",
            actor="maintainer",
        ),
        unit_of_work=store.write_unit_of_work,
        expected_generation=1,
    )
    with store.read_unit_of_work() as work:
        assert work.setups.read_current() == setup
        assert work.registry.current_generation() == 2


def test_initial_config_does_not_repair_partial_setup_state(tmp_path: Path) -> None:
    from scopecat.config.registry.service import (
        ConfigRevision,
        DirectConfigRevisionSource,
        publish_config_revision,
    )

    store = _store(tmp_path)
    with store.write_unit_of_work() as work:
        work.setups.save_revision(_revision("saved-without-activation"))
    with pytest.raises(ValueError, match="no initialized setup authority"):
        publish_config_revision(
            revision=ConfigRevision(
                source=DirectConfigRevisionSource(load_config()),
                entry_id="initial",
                actor="maintainer",
            ),
            unit_of_work=store.write_unit_of_work,
            expected_generation=0,
        )
    with store.read_unit_of_work() as work:
        assert len(work.setups.list_revisions()) == 1
        assert work.setups.read_current() is None
        assert work.registry.list_entries() == ()


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
    revision = SetupRevision(
        id="software",
        content_hash=setup.content_hash,
        setup=setup,
        actor="maintainer",
    )
    with store.write_unit_of_work() as work:
        work.setups.save_revision(revision)
    reopened = _store(tmp_path)
    with reopened.write_unit_of_work() as work:
        retained = work.setups.read_revision("software")
    assert retained == revision
    assert retained.setup.scenario == scenario
    assert retained.setup.compose(config).system.scenario == scenario
