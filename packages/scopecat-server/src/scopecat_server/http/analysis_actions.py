"""Read the actions associated with a run's retained author declaration."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from scopecat.daemon.views import RunRequestView
from scopecat.records.analysis_actions import (
    RunAnalysisActions,
    RunAnalysisActionsRequest,
)
from scopecat.records.author_revision import AuthorRevisionRef


def analysis_actions_router(
    read_request: Callable[[str], RunRequestView],
    describe: Callable[[RunAnalysisActionsRequest], RunAnalysisActions],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/runs")

    @router.get("/{run_id}/analysis-actions")
    def run_analysis_actions(run_id: str) -> RunAnalysisActions | None:
        metadata = read_request(run_id).request.metadata
        workspace = metadata.get("author_workspace")
        revision = metadata.get("author_code_revision")
        if not isinstance(workspace, str) or not isinstance(revision, str):
            return None
        try:
            request = RunAnalysisActionsRequest(
                run_id=run_id,
                workspace_id=workspace,
                code_revision=AuthorRevisionRef(content_hash=revision),
            )
        except ValueError as error:
            raise HTTPException(422, "Invalid retained author identity") from error
        try:
            return describe(request)
        except HTTPException as error:
            if error.status_code == 504:
                raise HTTPException(
                    504,
                    "Loading declared analyses timed out. No analysis was submitted. "
                    "Reload the declarations to try again.",
                ) from error
            raise
        except (KeyError, ValueError, OSError) as error:
            raise HTTPException(
                503,
                "The run's saved author source or execution environment "
                "is unavailable: "
                f"{error}. Restore the original author environment, then reload "
                "declared analyses. Existing results remain available.",
            ) from error

    return router
