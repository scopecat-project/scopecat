from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from scopecat.records.data_cleanup import DataCleanupCommand, DataCleanupSelection

from scopecat_server.errors import BackendConflict
from scopecat_server.services.data_cleanup import DataCleanupService
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.resource_objects import (
    put_resource_object,
    resource_directory,
)


def _run(store: SQLiteProjectStore, identity: str, content: bytes) -> None:
    blob = put_resource_object(store.sqlite, store.objects, "run", identity, content)
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO runs(run_id,created_at,config_content_hash) "
            "VALUES (?, 'now', 'hash')",
            (identity,),
        )
        connection.execute(
            "INSERT INTO scheduler_runs"
            "(submission_id,run_id,state,updated_at,admission_json) "
            "VALUES (?,?,'closed','now','{}')",
            (identity, identity),
        )
        connection.execute(
            "INSERT INTO run_repository_refs "
            "VALUES (?, 'records/analysis/result.json', ?)",
            (identity, blob.digest),
        )


def test_retained_evidence_blocks_deletion_and_explicit_selection_preserves_other_data(
    tmp_path: Path,
) -> None:
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        _run(store, "scan", b"{}")
        _run(store, "analysis", b'{"inputs":[{"run_id":"scan"}]}')
        _run(store, "unrelated", b"{}")
        cleanup = DataCleanupService(store)
        preview = cleanup.preview(DataCleanupSelection(runs=("scan",)))
        assert preview.blockers[0].owner == "run:analysis"
        with pytest.raises(ValueError, match="retaining dependencies"):
            cleanup.execute(DataCleanupCommand(request_key="blocked", preview=preview))
        selection = DataCleanupSelection(runs=("scan", "analysis"))
        operation = cleanup.execute(
            DataCleanupCommand(
                request_key="accepted", preview=cleanup.preview(selection)
            )
        )
        assert operation.state == "complete", operation.error
        assert not resource_directory(store.objects, "run", "scan").exists()
        assert resource_directory(store.objects, "run", "unrelated").exists()
        with store.sqlite.read_transaction() as connection:
            assert (
                connection.execute("SELECT run_id FROM runs").fetchall()[0][0]
                == "unrelated"
            )
        with pytest.raises(BackendConflict, match="cleared"):
            put_resource_object(
                store.sqlite, store.objects, "run", "scan", b"late write"
            )


def test_live_analysis_must_stop_before_its_input_is_cleared(tmp_path: Path) -> None:
    from scopecat.records.analysis_follow import AnalysisFollowRequest
    from scopecat.records.analysis_grouping import AnalysisGrouping
    from scopecat.records.author_revision import (
        AuthorAnalysisRequest,
        AuthorRevisionRef,
    )

    from scopecat_server.storage.sqlite.analysis_follow import AnalysisFollowRepository

    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        _run(store, "scan", b"{}")
        follows = AnalysisFollowRepository(store.sqlite)
        command = AnalysisFollowRequest(
            id="live",
            analysis=AuthorAnalysisRequest(
                workspace_id="authors",
                code_revision=AuthorRevisionRef(content_hash="sha256:" + "0" * 64),
                run_id="scan",
                analysis="authors:fit",
                grouping=AnalysisGrouping(by=(), fitting="frequency"),
            ),
        )
        follows.create(command)
        cleanup = DataCleanupService(store)
        selection = DataCleanupSelection(runs=("scan",))
        assert any(
            blocker.owner == "analysis-follow:live"
            for blocker in cleanup.preview(selection).blockers
        )
        follows.set_state("live", "stopped")
        preview = cleanup.preview(selection)
        assert not preview.blockers
        assert (
            cleanup.execute(
                DataCleanupCommand(request_key="clear", preview=preview)
            ).state
            == "complete"
        )
        with pytest.raises(KeyError):
            follows.get("live")
        with pytest.raises(BackendConflict, match="cleared"):
            follows.create(command)


def test_file_failure_keeps_resumable_receipt_and_record_fence(tmp_path: Path) -> None:
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        _run(store, "scan", b"{}")
        cleanup = DataCleanupService(store)
        command = DataCleanupCommand(
            request_key="clear",
            preview=cleanup.preview(DataCleanupSelection(runs=("scan",))),
        )
        with patch(
            "scopecat_server.services.data_cleanup.shutil.rmtree",
            side_effect=OSError("file in use"),
        ):
            interrupted = cleanup.execute(command)
        assert interrupted.state == "records_removed"
        assert interrupted.error == "file in use"
        restarted = DataCleanupService(store)
        assert restarted.resume(interrupted.id).state == "complete"
        assert restarted.execute(command).state == "complete"


def test_new_retaining_evidence_invalidates_preview_and_late_sql_reference_is_fenced(
    tmp_path: Path,
) -> None:
    with closing(
        SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    ) as store:
        store.bootstrap()
        _run(store, "scan", b"{}")
        cleanup = DataCleanupService(store)
        preview = cleanup.preview(DataCleanupSelection(runs=("scan",)))
        _run(store, "derived", b'{"run_id":"scan"}')
        with pytest.raises(ValueError, match="changed"):
            cleanup.execute(DataCleanupCommand(request_key="stale", preview=preview))
        result = cleanup.execute(
            DataCleanupCommand(
                request_key="both",
                preview=cleanup.preview(DataCleanupSelection(runs=("scan", "derived"))),
            )
        )
        assert result.state == "complete"
        with (
            pytest.raises(BackendConflict, match="cleared"),
            store.sqlite.write_transaction() as connection,
        ):
            connection.execute(
                "INSERT INTO parameter_revisions VALUES (?,?)",
                ("late", '{"provenance":{"run_id":"scan"}}'),
            )
