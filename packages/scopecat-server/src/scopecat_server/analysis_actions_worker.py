"""Read declared analysis inputs inside the existing retained-source worker."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scopecat_server.author_worker import author_module_path

if TYPE_CHECKING:
    from scopecat.application import LabApplication
    from scopecat.project import Project
    from scopecat.records.analysis_actions import (
        RunAnalysisActions,
        RunAnalysisActionsRequest,
    )


def describe(
    project: Project,
    application: LabApplication,
    workspace_root: Path,
    request: RunAnalysisActionsRequest,
) -> RunAnalysisActions:
    from scopecat.daemon.endpoint import resolve_daemon_endpoint

    from scopecat_server.analysis_actions import describe_run_analyses

    def load_definition(reference: str) -> object:
        module_name, _, name = reference.partition(":")
        expected = author_module_path(project, module_name)
        module = import_module(module_name)
        if Path(cast("str", module.__file__)) != expected:
            raise ValueError("analysis resolved outside its source snapshot")
        return cast("object", getattr(module, name))

    with application.connect(resolve_daemon_endpoint(workspace_root)) as lab:
        run = lab.get_run(request.run_id)
        if run.status != "completed":
            raise ValueError("Declared analysis actions require a completed run.")
        return describe_run_analyses(
            request,
            metadata=run.request.metadata,
            authors=application.authors,
            load_definition=load_definition,
        )
