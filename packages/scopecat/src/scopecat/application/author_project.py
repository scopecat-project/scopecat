"""Revision-aware notebook entry point; fresh workers own all project imports."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from scopecat.application.launch import (
    LaunchCatalog,
    LaunchPreview,
    LaunchRequest,
    LaunchSubmission,
)
from scopecat.daemon.client import DaemonClient
from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorRevisionRef,
    AuthorRevisionState,
)


class AuthorProject:
    """Refresh and execute author code without mutating notebook module state.

    Keep a preview's code_revision for submission. Analysis always requires an
    explicit revision, which can be copied from retained run metadata.
    """

    def __init__(self, endpoint: str) -> None:
        self.client = DaemonClient(endpoint, timeout=120)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.client.close()

    def state(self) -> AuthorRevisionState:
        return self.client.author_revision_state()

    def refresh(self, *, expected_generation: int) -> AuthorRevisionState:
        """Validate and publish against an observed generation."""
        return self.client.refresh_authors(expected_generation=expected_generation)

    def catalog(self) -> LaunchCatalog:
        return self.client.author_launch_catalog()

    def preview(self, request: LaunchRequest) -> LaunchPreview:
        return self.client.author_launch_preview(request)

    def submit(self, request: LaunchRequest) -> LaunchSubmission:
        return self.client.author_launch_submit(request)

    def analyze(
        self,
        run_id: str,
        analysis: str,
        *,
        code_revision: AuthorRevisionRef,
        key: str | None = None,
    ) -> AuthorAnalysisReceipt:
        return self.client.analyze_author_revision(
            AuthorAnalysisRequest(
                run_id=run_id, analysis=analysis, code_revision=code_revision, key=key
            )
        )
