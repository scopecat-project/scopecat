"""Bindings can only be supplied by successful server execution, not fact writers."""

from pathlib import Path

import pytest
from scopecat.records.author_revision import (
    AuthorAnalysisGroupReceipt,
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorRevisionRef,
)

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.managed_analysis import ManagedAnalysisRepository
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def test_exact_binding_and_atomic_rejection(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "db")
    try:
        SQLiteProjectStore(database, tmp_path / "objects").bootstrap()
        with database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO runs(run_id,created_at,config_content_hash) "
                "VALUES('r','now','hash')"
            )
            for identity in ("result", "failed-group"):
                connection.execute(
                    "INSERT INTO analysis_publications("
                    "subject_kind,run_id,subject_json,"
                    "record_id,record_entry_json,analysis_key,revision,publication_hash,"
                    "published_at,title,input_count,output_count) "
                    "VALUES('run','r','{}',?,'{}',?,1,?,'now','title',0,0)",
                    (identity, identity, identity + "-hash"),
                )
        repository = ManagedAnalysisRepository(database)
        revision = AuthorRevisionRef(content_hash="sha256:" + "a" * 64)
        request = AuthorAnalysisRequest(
            workspace_id="local",
            code_revision=revision,
            run_id="r",
            analysis="module:fit",
        )
        receipt = AuthorAnalysisReceipt(code_revision=revision, analysis_id="result")
        assert not repository.contains("r", "result", "result-hash")
        with pytest.raises(ValueError, match="revision"):
            repository.retain(
                request,
                receipt.model_copy(
                    update={
                        "code_revision": AuthorRevisionRef(
                            content_hash="sha256:" + "b" * 64
                        )
                    }
                ),
            )
        with pytest.raises(ValueError, match="unrelated"):
            repository.retain(request.model_copy(update={"run_id": "other"}), receipt)
        with pytest.raises(ValueError, match="unrelated"):
            repository.retain(
                request,
                receipt.model_copy(
                    update={
                        "groups": (
                            AuthorAnalysisGroupReceipt(
                                coordinates={}, point_indices=(), analysis_id="missing"
                            ),
                        )
                    }
                ),
            )
        assert not repository.contains("r", "result", "result-hash")
        repository.retain(
            request,
            receipt.model_copy(
                update={
                    "groups": (
                        AuthorAnalysisGroupReceipt(
                            coordinates={},
                            point_indices=(),
                            analysis_id="failed-group",
                            error="fit failed",
                        ),
                    )
                }
            ),
        )
        repository.retain(request, receipt)
        assert repository.contains("r", "result", "result-hash")
        assert not repository.contains("r", "result", "changed-hash")
        assert not repository.contains("other", "result", "result-hash")
        assert not repository.contains("r", "failed-group", "failed-group-hash")
    finally:
        database.close()
