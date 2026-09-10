"""Submission interruption and bounded procedure waiting without acquisition retries."""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest

from scopecat.application.author_project import (
    AuthorJob,
    AuthorJobTimeout,
    AuthorProject,
    AuthorSubmissionUncertain,
)
from scopecat.records.launch_request import LaunchRequest


def test_reopen_missing_admission_never_posts(tmp_path: Path) -> None:
    requests: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.method)
        assert request.url.params["request_key"] == "retained-key"
        return httpx2.Response(200, json={"items": []})

    receipt = tmp_path / "receipt.json"
    with AuthorProject("http://test", transport=httpx2.MockTransport(respond)) as first:
        job = AuthorJob.retain(
            first,
            receipt,
            LaunchRequest(
                action="preview",
                experiment="signal",
                version="1",
                request_key="retained-key",
            ),
        )
        assert job.recover() is None
    with AuthorProject(
        "http://test", transport=httpx2.MockTransport(respond)
    ) as second:
        reopened = job.reconnect(second)
        with pytest.raises(AuthorSubmissionUncertain) as caught:
            reopened.wait(timeout=0)
        assert caught.value.job.receipt == receipt
    assert requests == ["GET", "GET"]


def test_wait_transport_timeout_is_not_cancellation(tmp_path: Path) -> None:
    calls: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        timeout = request.extensions["timeout"]
        assert isinstance(timeout, dict)
        assert timeout["read"] <= 0.1
        raise httpx2.ReadTimeout("delayed", request=request)

    with AuthorProject(
        "http://test", transport=httpx2.MockTransport(respond)
    ) as session:
        job = AuthorJob.retain(
            session,
            tmp_path / "receipt.json",
            LaunchRequest(
                action="preview", experiment="signal", version="1", request_key="key"
            ),
        )
        with pytest.raises(AuthorJobTimeout):
            job.wait(timeout=0.1)
    assert calls == ["GET"]


@pytest.mark.parametrize(
    ("state", "closure", "error"),
    [
        ("attention_required", None, "AuthorJobAttention"),
        ("waiting_for_input", None, "AuthorJobAttention"),
        ("closed", "cancelled", "AuthorJobCancelled"),
        ("closed", "failed", "AuthorJobFailed"),
        ("ready", None, "AuthorJobTimeout"),
    ],
)
def test_wait_distinguishes_operator_outcomes(
    tmp_path: Path, state: str, closure: str | None, error: str
) -> None:
    import scopecat.application.author_project as author_api
    from scopecat.automation.models import ProcedureDefinitionRef, procedure_intent_hash

    definition = ProcedureDefinitionRef(
        id="test", version="1", fingerprint="sha256:" + "a" * 64
    )
    snapshot = {
        "procedure_run_id": "original",
        "request_key": "key",
        "definition": definition.model_dump(),
        "intent": {},
        "intent_hash": procedure_intent_hash(definition, {}),
        "revision": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:01Z",
        "state": state,
        "attention_reason": "Inspect device" if state == "attention_required" else None,
        "closure": {
            "status": closure,
            "reason": "Stopped",
            "closed_at": "2026-01-01T00:00:01Z",
        }
        if closure
        else None,
    }
    calls: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        return httpx2.Response(200, json={"items": [snapshot]})

    with AuthorProject(
        "http://test", transport=httpx2.MockTransport(respond)
    ) as session:
        job = AuthorJob.retain(
            session,
            tmp_path / "receipt.json",
            LaunchRequest(
                action="preview", experiment="signal", version="1", request_key="key"
            ),
        )
        with pytest.raises(getattr(author_api, error)):
            job.wait(timeout=0)
    assert calls == ["GET"]


@pytest.mark.parametrize("status", [422, 500, 503])
def test_submission_rejection_is_distinct_from_unknown(
    tmp_path: Path, status: int
) -> None:
    from scopecat.application.author_project import AuthorPreparedLaunch
    from scopecat.application.launch import LaunchPreview
    from scopecat.records.run import ConfigRegistryRunConfigSource

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert len(list(tmp_path.glob("*.json"))) == 1
        return httpx2.Response(status, json={"detail": "test response"})

    with AuthorProject(
        "http://test", receipts=tmp_path, transport=httpx2.MockTransport(respond)
    ) as session:
        request = LaunchRequest(action="preview", experiment="signal", version="1")
        source = ConfigRegistryRunConfigSource(
            selector="active",
            entry_id="config",
            config_ref="config",
            content_hash="sha256:" + "a" * 64,
            registry_generation=1,
        )
        prepared = AuthorPreparedLaunch(
            session,
            request,
            LaunchPreview(
                experiment_id="signal",
                request_hash=request.request_hash,
                config_source=source,
                point_count=1,
                summary="Checked",
            ),
        )
        expected = (
            httpx2.HTTPStatusError if status == 422 else AuthorSubmissionUncertain
        )
        with pytest.raises(expected):
            prepared.run()
