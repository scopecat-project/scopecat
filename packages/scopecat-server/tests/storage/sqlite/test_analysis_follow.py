from collections.abc import Iterator
from pathlib import Path
from threading import Event
from typing import Never

import pytest
from scopecat.records.analysis_follow import AnalysisFollowEvent, AnalysisFollowRequest
from scopecat.records.analysis_grouping import AnalysisGrouping
from scopecat.records.author_revision import AuthorAnalysisRequest, AuthorRevisionRef

from scopecat_server.services.analysis_follow import AnalysisFollowRunner
from scopecat_server.storage.sqlite.analysis_follow import AnalysisFollowRepository
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteProjectStore]:
    store = SQLiteProjectStore(SQLiteDatabase(tmp_path / "db"), tmp_path / "objects")
    store.bootstrap()
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO runs(run_id,created_at,config_content_hash) "
            "VALUES ('scan','now','hash')"
        )
    try:
        yield store
    finally:
        store.close()


def _request(identity: str) -> AnalysisFollowRequest:
    return AnalysisFollowRequest(
        id=identity,
        analysis=AuthorAnalysisRequest(
            workspace_id="authors",
            code_revision=AuthorRevisionRef(content_hash="sha256:" + "0" * 64),
            run_id="scan",
            analysis="authors:fit",
            grouping=AnalysisGrouping(by=(), fitting="frequency"),
        ),
    )


def test_retry_is_idempotent_and_active_follows_are_bounded(
    store: SQLiteProjectStore,
) -> None:
    repository = AnalysisFollowRepository(store.sqlite)
    for index in range(8):
        repository.create(_request(str(index)))
    assert repository.create(_request("0")).state == "running"
    with pytest.raises(ValueError, match="different inputs"):
        repository.create(_request("0").model_copy(update={"timeout_seconds": 30}))
    with pytest.raises(ValueError, match="eight"):
        repository.create(_request("9"))
    repository.set_state("0", "stopped")
    assert repository.create(_request("9")).state == "running"


def test_restart_marks_unknown_publication_without_silently_reexecuting(
    store: SQLiteProjectStore,
) -> None:
    repository = AnalysisFollowRepository(store.sqlite)
    repository.create(_request("live"))
    repository.event(
        "live",
        AnalysisFollowEvent(
            cursor=0, group_index=0, state="running", measurement_slice="slice-fixed"
        ),
    )
    first = repository.page("live", limit=1)

    def unexpected(
        _request: AuthorAnalysisRequest, _timeout: float, _cancelled: Event
    ) -> Never:
        pytest.fail("uncertain publication must not execute automatically")

    runner = AnalysisFollowRunner(
        AnalysisFollowRepository(store.sqlite),
        SQLiteRunRepository(store.sqlite, store.objects.root),
        unexpected,
    )
    runner.start()
    runner.stop()
    page = repository.page("live", after=first.next_cursor, limit=1)
    assert page.follow.state == "attention"
    assert page.follow.failed_count == 1
    assert len(page.events) == 1
    assert page.events[0].state == "uncertain"
    assert page.next_cursor > first.next_cursor
    assert not repository.page("live", after=page.next_cursor).events
