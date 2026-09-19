"""Publication failures cross the pooled HTTP boundary without implicit retries."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from scopecat.records.author_revision import AuthorAnalysisRequest, AuthorRevisionRef
from scopecat.records.comparison import ComparisonRequest

from scopecat_server.http.transport import create_app
from scopecat_server.retained_request import AnalysisCall, ComparisonCall
from scopecat_server.services.application import DaemonApplication
from scopecat_server.services.revision_workers import AuthorWorkerBinding


@pytest.mark.parametrize("operation", ["analysis", "comparison"])
def test_timeout_reports_unknown_publication_and_never_retries(operation: str) -> None:
    application = cast(
        "DaemonApplication",
        cast(
            "object",
            SimpleNamespace(
                project_root=Path.cwd(),
                author_revisions=SimpleNamespace(
                    worker_binding=AuthorWorkerBinding(Path.cwd(), Path(sys.executable))
                ),
            ),
        ),
    )
    ref = AuthorRevisionRef(content_hash="sha256:" + "a" * 64)
    command = (
        AuthorAnalysisRequest(
            code_revision=ref, run_id="retained", analysis="lab.analysis:fit"
        )
        if operation == "analysis"
        else ComparisonRequest(action="fit", code_revision=ref)
    )
    path = (
        "/api/v1/author-revisions/analyze"
        if operation == "analysis"
        else "/api/v1/run-comparison"
    )
    with patch(
        "scopecat_server.http.transport.RevisionWorkers.call",
        side_effect=subprocess.TimeoutExpired(
            "worker", 60, stderr=f"Scopecat worker stage: retained {operation}\n"
        ),
    ) as call:
        response = TestClient(create_app(application)).post(
            path, json=command.model_dump(mode="json")
        )
    assert response.status_code == 504
    assert "Publication outcome may be unknown" in response.json()["detail"]
    assert f"retained {operation}" in response.json()["detail"]
    assert call.call_count == 1
    assert call.call_args.args[0] == application.author_revisions.worker_binding
    payload = call.call_args.args[1]
    assert isinstance(
        payload, AnalysisCall if operation == "analysis" else ComparisonCall
    )
    assert payload.code_revision == ref
    assert 0 < call.call_args.kwargs["timeout"] <= 60


def test_analysis_failure_keeps_stack_in_daemon_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = cast(
        "DaemonApplication",
        cast(
            "object",
            SimpleNamespace(
                project_root=Path.cwd(),
                author_revisions=SimpleNamespace(
                    worker_binding=AuthorWorkerBinding(Path.cwd(), Path(sys.executable))
                ),
            ),
        ),
    )
    command = AuthorAnalysisRequest(
        code_revision=AuthorRevisionRef(content_hash="sha256:" + "a" * 64),
        run_id="retained",
        analysis="lab.analysis:fit",
    )
    with patch("scopecat_server.http.transport.RevisionWorkers.call") as call:
        call.return_value = subprocess.CompletedProcess(
            "worker", 1, "", "original stack evidence\nValueError: invalid fit"
        )
        response = TestClient(create_app(application)).post(
            "/api/v1/author-revisions/analyze", json=command.model_dump(mode="json")
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "ValueError: invalid fit"
    assert "original stack evidence" in caplog.text
    assert call.call_count == 1
