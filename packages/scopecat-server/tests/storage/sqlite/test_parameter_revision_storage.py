"""Parameter revisions retain exact shared setup content, not active setup."""

import json
from pathlib import Path

import pytest
from scopecat.config.registry.records import (
    ConfigRegistryEntry,
    DirectConfigRegistrySource,
)
from scopecat.config.registry.service import (
    ConfigRevision,
    DirectConfigRevisionSource,
    save_config_revision,
)
from scopecat.kernel.errors import DataIntegrityError
from scopecat.project import load_project
from scopecat.records.config import config_content_hash
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import SQLiteTestRunRepository

from scopecat_server.snapshots import create_snapshot, restore_snapshot
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.parameter_branches import ParameterBranchRepository
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def _store(root: Path) -> SQLiteConfigRegistryStore:
    state = root / ".scopecat"
    sqlite = SQLiteDatabase(state / "control.sqlite3")
    SQLiteProjectStore(sqlite, state / "objects").bootstrap()
    return SQLiteConfigRegistryStore(
        sqlite, runs=SQLiteTestRunRepository(sqlite, state / "objects")
    )


def test_parameters_share_exact_setup_and_survive_backup_restore(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    store = _store(root)
    first = load_config()
    second = first.model_copy(deep=True)
    second.parameter_snapshot = second.parameter_snapshot.model_copy(
        update={"id": "second-parameter-version"}
    )
    third = second.model_copy(deep=True)
    third.system.topology.entities[0] = third.system.topology.entities[0].model_copy(
        update={"metadata": {"description": "New setup metadata"}}
    )
    configs = (first, second, third)
    refs: list[str] = []
    for index, config in enumerate(configs):
        saved = save_config_revision(
            revision=ConfigRevision(
                source=DirectConfigRevisionSource(config),
                entry_id=f"parameters-{index}",
                actor="maintainer",
            ),
            unit_of_work=store.write_unit_of_work,
        )
        refs.append(saved.entry.config_ref)
    with store.sqlite.read_transaction() as connection:
        rows = connection.execute(
            "SELECT parameters_json, setup_content_hash "
            "FROM config_registry_entries ORDER BY entry_id"
        ).fetchall()
        assert len(rows) == 3
        assert rows[0][1] == rows[1][1] != rows[2][1]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM configuration_setup_contents"
            ).fetchone()[0]
            == 2
        )
        assert set(json.loads(rows[0][0])) == {
            "id",
            "system_id",
            "catalog",
            "parameters",
        }
    with store.read_unit_of_work() as work:
        assert work.setups.read_current() is None
        assert work.setups.list_revisions() == ()
        for ref, config in zip(refs, configs, strict=True):
            assert config_content_hash(
                work.registry.read_config(ref)
            ) == config_content_hash(config)
    store.sqlite.close()
    create_snapshot(load_project(root / "scopecat.toml"), tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    restored = _store(tmp_path / "restored")
    with restored.read_unit_of_work() as work:
        for ref, config in zip(refs, configs, strict=True):
            assert config_content_hash(
                work.registry.read_config(ref)
            ) == config_content_hash(config)
    restored.sqlite.close()


def test_standalone_parameters_survive_backup_without_any_setup(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    store = _store(root)
    config = load_config()
    revision = ParameterRevision(
        id="author-values",
        catalog=config.parameter_catalog,
        parameters=config.parameter_snapshot,
        content_hash=parameter_revision_hash(
            config.parameter_catalog, config.parameter_snapshot
        ),
        actor="author",
    )
    with store.sqlite.write_transaction() as connection:
        assert ParameterRevisionRepository(connection).save(revision) == revision
        branch = ParameterBranch(
            name="daily", generation=1, revision=revision.ref, actor="author"
        )
        ParameterBranchRepository(connection).append(branch, "test-intent")
    store.sqlite.close()
    create_snapshot(load_project(root / "scopecat.toml"), tmp_path / "backup")
    restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    restored = _store(tmp_path / "restored")
    with restored.sqlite.read_connection() as connection:
        assert ParameterRevisionRepository(connection).get(revision.id) == revision
        assert ParameterBranchRepository(connection).get("daily") == branch
    with restored.read_unit_of_work() as work:
        assert not work.registry.list_entries()
        assert not work.setups.list_revisions()
        assert work.setups.read_current() is None
    restored.sqlite.close()


def test_damaged_shared_setup_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = save_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(load_config()),
            entry_id="parameters",
            actor="maintainer",
        ),
        unit_of_work=store.write_unit_of_work,
    )
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "UPDATE configuration_setup_contents "
            "SET setup_json = json_set(setup_json, "
            "'$.topology.entities[0].id', 'changed')"
        )
    with (
        store.read_unit_of_work() as work,
        pytest.raises(DataIntegrityError, match="retained setup content"),
    ):
        work.registry.read_config(saved.entry.config_ref)
    store.sqlite.close()


def test_parameter_and_setup_content_roll_back_together(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = load_config()
    with pytest.raises(RuntimeError, match="abort"), store.write_unit_of_work() as work:
        work.registry.commit_revision(
            entry=ConfigRegistryEntry(
                id="parameters",
                config_ref=work.registry.config_ref("parameters"),
                content_hash=config_content_hash(config),
                source=DirectConfigRegistrySource(),
                actor="maintainer",
                note="",
            ),
            config=config,
        )
        raise RuntimeError("abort")
    with store.sqlite.read_transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM config_registry_entries"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM configuration_setup_contents"
            ).fetchone()[0]
            == 0
        )
    store.sqlite.close()
