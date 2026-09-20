"""Notebook preparation renders typed checks without hiding ordinary failures."""

import httpx2
import pytest

from scopecat.application.author_project import AuthorProject
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.records.launch_rejection import AuthorLaunchRejected, LaunchRejection
from scopecat.records.launch_request import LaunchRequest


def test_notebook_retains_diagnostic_and_readable_message() -> None:
    diagnostic = LaunchRejection(
        message="Preparation checks failed",
        problems=(
            problem("unsupported_readback", "No readback", phase=ProblemPhase.PLANNING),
        ),
    )
    with (
        AuthorProject(
            "http://test",
            transport=httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    422, json={"detail": diagnostic.model_dump(mode="json")}
                )
            ),
        ) as client,
        pytest.raises(AuthorLaunchRejected) as caught,
    ):
        client.preview(LaunchRequest(action="preview", experiment="test", version="1"))
    assert caught.value.diagnostic == diagnostic
    assert "unsupported_readback: No readback" in str(caught.value)
    assert '{"' not in str(caught.value)


def test_plain_rejection_is_not_classified_as_capability_failure() -> None:
    with (
        AuthorProject(
            "http://test",
            transport=httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    422, json={"detail": "TypeError: invalid author computation"}
                )
            ),
        ) as client,
        pytest.raises(httpx2.HTTPStatusError),
    ):
        client.preview(LaunchRequest(action="preview", experiment="test", version="1"))
