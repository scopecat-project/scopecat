from pathlib import Path

from fastapi.testclient import TestClient
from scopecat.records.analysis_follow import AnalysisFollowRequest
from scopecat.records.analysis_grouping import AnalysisGrouping
from scopecat.records.author_revision import AuthorAnalysisRequest, AuthorRevisionRef
from scopecat_testkit.server.instruments import signal_endpoint

from scopecat_server import LocalDaemonRuntime

from .signal_scenario import complete_signal_run as _complete_signal_run
from .signal_scenario import signal_config as _config


def test_unresolved_background_publication_blocks_idle_exit_after_reopening(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=_config(), instrument_endpoint=signal_endpoint()
    ) as runtime:
        run_id = _complete_signal_run(runtime, submission_id="scan", signal=1.0)
        follows = runtime.application.analysis_follows
        follows.create(
            AnalysisFollowRequest(
                id="follow",
                analysis=AuthorAnalysisRequest(
                    workspace_id="authors",
                    code_revision=AuthorRevisionRef(content_hash="sha256:" + "0" * 64),
                    run_id=run_id,
                    analysis="authors:fit",
                    grouping=AnalysisGrouping(by=(), fitting="frequency"),
                ),
            )
        )
        assert not runtime.application.activity().busy
        follows.set_state("follow", "attention", "publication outcome unknown")
        expected = follows.get("follow")
        assert runtime.application.activity().background_attention == 1
    with LocalDaemonRuntime(tmp_path) as reopened:
        assert reopened.application.analysis_follows.get("follow") == expected
        activity = reopened.application.activity()
        assert activity.background_attention == 1 and activity.busy
        client = TestClient(reopened.app(request_shutdown=lambda _: True))
        response = client.post(
            "/api/v1/shutdown?only_if_idle=true",
            headers={"X-Scopecat-Shutdown-Token": "fixture"},
        )
        assert response.status_code == 409
        assert reopened.application.analysis_follows.get("follow") == expected
