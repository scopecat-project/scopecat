"""Revision-aware notebook entry point; fresh workers own all project imports."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from scopecat.application.experiment_plans import plan_definition, plan_launch_request
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchField,
    LaunchPreview,
    LaunchSubmission,
)
from scopecat.daemon.client import DaemonClient
from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorRevisionRef,
    AuthorRevisionState,
)
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experiment_plan import ExperimentPlanRevision, ExperimentPlanSave
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import ExperimentPlanRef, PlanAnalysisSource


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
        context: ConfigContextRef | None = None,
        overrides: tuple[ParameterUpdate, ...] = (),
        sample: str | None = None,
        actor: str = "operator",
    ) -> AuthorPreparedLaunch:
        """Select the current declaration and retain a preview's exact submission."""
        catalog = self.catalog()
        entry = next(item for item in catalog.entries if item.id == experiment)
        declared_inputs = {
            name: field.default
            for name, field in entry.request.properties.items()
            if isinstance(field, LaunchField) and "default" in field.model_fields_set
        }
        request = LaunchRequest(
            action="preview",
            experiment=entry.id,
            version=entry.version,
            control_edits=control_edits or {},
            inputs=declared_inputs | (inputs or {}),
            context=context,
            overrides=overrides,
            sample=sample,
            actor=actor,
            code_revision=catalog.code_revision,
        )
        return AuthorPreparedLaunch(self, request, self.preview(request))

    def prepare_plan(
        self, ref: ExperimentPlanRef, *, actor: str
    ) -> AuthorPreparedLaunch:
        """Read an exact plan and obtain a new preview for this execution actor."""
        request = plan_launch_request(self.experiment_plan(ref), actor=actor)
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

    def save_plan(
        self,
        name: str,
        *,
        saved_by: str,
        previous: ExperimentPlanRef | None = None,
        copied_from: ExperimentPlanRef | None = None,
        source: PlanAnalysisSource | None = None,
    ) -> ExperimentPlanRevision:
        """Save immutable inputs/configuration; no run is admitted or activated."""
        return self.client.save_experiment_plan(
            ExperimentPlanSave(
                name=name,
                saved_by=saved_by,
                previous=previous,
                copied_from=copied_from,
                definition=plan_definition(self.request, self.preview, source=source),
            )
        )

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
                    "manual_state": self.preview.manual_state,
                }
            )
        )
