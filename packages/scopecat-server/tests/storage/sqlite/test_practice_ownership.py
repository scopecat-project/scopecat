from contextlib import closing
from pathlib import Path

import pytest
from scopecat.records.data_cleanup import DataCleanupSelection
from scopecat.records.practice import PracticeScope
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.setup_records import retained_setup_revision

from scopecat_server.services.data_cleanup import DataCleanupService
from scopecat_server.services.practice_admission import require_practice_setup
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


def test_practice_cannot_borrow_hardware_or_other_scope_and_does_not_pollute_catalog(
    tmp_path: Path,
) -> None:
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        with store.sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            scope = PracticeScope(
                id="one", title="One", lesson="manual-peaks", directory="notes"
            )
            owners.save(scope)
            owners.save(scope.model_copy(update={"id": "two"}))
            setups = SQLiteSetupRepository(connection)
            hardware = retained_setup_revision(
                id="ordinary",
                setup=ExecutableSetupSnapshot.from_config(load_config()),
                actor="test",
            )
            setups.save_revision(hardware)
            owned = hardware.model_copy(update={"id": "practice"})
            setups.save_revision(owned)
            owners.claim("one", "setup", owned.id)
            owners.claim("two", "procedure", "another-task")
            assert [item.id for item in setups.list_revisions()] == ["ordinary"]
            assert setups.read_revision(owned.id) == owned
            with pytest.raises(ValueError, match="physical device"):
                require_practice_setup(connection, owned.id)
            with pytest.raises(ValueError, match="ordinary or another"):
                owners.require_shared(("setup", owned.id), ("run", "ordinary-run"))
            with pytest.raises(ValueError, match="ordinary or another"):
                owners.require_shared(
                    ("setup", owned.id), ("procedure", "another-task")
                )
            owners.save(scope.model_copy(update={"state": "cleaning"}))
            with pytest.raises(ValueError, match="fenced"):
                owners.claim("one", "run", "late")
        preview = DataCleanupService(store).preview(
            DataCleanupSelection(setups=(owned.id,))
        )
        assert any(item.owner == "practice:one" for item in preview.blockers)
