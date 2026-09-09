"""Revision-aware notebook entry point; fresh workers own all project imports."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from scopecat.application.controls import ControlEdit
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


class AuthorProject(DaemonClient):
    """Refresh and execute author code without mutating notebook module state.

    Keep a preview's code_revision for submission. Analysis always requires an
    explicit revision, which can be copied from retained run metadata.
    """

    def prepare(
        self,
        experiment: str,
        *,
        control_edits: dict[str, ControlEdit] | None = None,
        inputs: dict[str, JsonValue] | None = None,
        sample: str | None = None,
        actor: str = "operator",
    ) -> AuthorPreparedLaunch:
        """Select the current declaration and retain a preview's exact submission."""
        catalog = self.catalog()
        entry = next(item for item in catalog.entries if item.id == experiment)
        request = LaunchRequest(
            action="preview",
            experiment=entry.id,
            version=entry.version,
            control_edits=control_edits or {},
            inputs=inputs or {},
            sample=sample,
            actor=actor,
            code_revision=catalog.code_revision,
        )
        return AuthorPreparedLaunch(self, request, self.preview(request))

    def state(self) -> AuthorRevisionState:
        return self.author_revision_state()

    def refresh(self, *, expected_generation: int) -> AuthorRevisionState:
        """Validate and publish against an observed generation."""
        return self.refresh_authors(expected_generation=expected_generation)

    def catalog(self) -> LaunchCatalog:
        return self._get_model("/api/v1/experiment-launcher", LaunchCatalog)

    def preview(self, request: LaunchRequest) -> LaunchPreview:
        return self._post_model(
            "/api/v1/experiment-launcher/preview", request, LaunchPreview
        )

    def submit(self, request: LaunchRequest) -> LaunchSubmission:
        return self._post_model(
            "/api/v1/experiment-launcher/submit", request, LaunchSubmission
        )

    def analyze(
        self,
        run_id: str,
        analysis: str,
        *,
        code_revision: AuthorRevisionRef,
        key: str | None = None,
    ) -> AuthorAnalysisReceipt:
        return self.analyze_author_revision(
            AuthorAnalysisRequest(
                run_id=run_id, analysis=analysis, code_revision=code_revision, key=key
            )
        )


@dataclass(frozen=True, slots=True)
class AuthorPreparedLaunch:
    client: AuthorProject
    request: LaunchRequest
    preview: LaunchPreview

    def submit(self, *, request_key: str) -> LaunchSubmission:
        """Submit this checked revision; reuse the same key only for a retry."""
        return self.client.submit(
            LaunchRequest.model_validate(
                {
                    **self.request.model_dump(),
                    "action": "submit",
                    "request_key": request_key,
                    "expected_request_hash": self.preview.request_hash,
                    "config_source": self.preview.config_source,
                    "code_revision": self.preview.code_revision,
                }
            )
        )
