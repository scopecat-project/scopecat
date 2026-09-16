"""Immutable source validation, loading and retained-data analysis helpers."""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import time
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scopecat.runtime_binding import load_runtime_binding

from scopecat_server.worker_diagnostics import report_stage

if TYPE_CHECKING:
    from scopecat.application import LabApplication
    from scopecat.project import Project
    from scopecat.records.author_revision import (
        AuthorAnalysisReceipt,
        AuthorAnalysisRequest,
        AuthorRevisionRef,
    )


def revision_project(root: Path, ref: AuthorRevisionRef) -> Project:
    """Read original identity before importing any project implementation."""
    from scopecat.daemon.client import DaemonClient
    from scopecat.daemon.endpoint import resolve_daemon_endpoint
    from scopecat.project import load_project
    from scopecat.project_sources import materialize_sources, require_environment

    with DaemonClient(resolve_daemon_endpoint(root)) as client:
        bundle = client.author_revision(ref)
    require_environment(bundle.manifest)
    code_root = materialize_sources(
        bundle, load_runtime_binding(root).data_root / "code"
    )
    return replace(
        load_project(code_root / "scopecat.toml"),
        root=root,
        code_root=code_root,
        code_revision=ref,
    )


def author_module_path(project: Project, module_name: str) -> Path:
    """Resolve the HTTP-selected module inside configured roots before importing it."""
    if not all(part.isidentifier() for part in module_name.split(".")):
        raise ValueError("analysis module must be a qualified Python module name")
    from scopecat.installed_authors import installed_module_path

    top_level = module_name.split(".")[0]
    for module, distribution in project.installed_packages:
        if module == top_level:
            return installed_module_path(module, distribution, module_name)
    code_root = project.code_root or project.root
    relative = Path(*module_name.split("."))
    for prefix in (code_root / "src", code_root):
        for path in (
            prefix / relative.with_suffix(".py"),
            prefix / relative / "__init__.py",
        ):
            if path.is_file() and any(
                path.relative_to(code_root).is_relative_to(root)
                for root in project.refresh_roots
            ):
                return path
    raise ValueError("module must belong to a configured author refresh root")


def validate(
    root: Path, code_root: Path, ref: AuthorRevisionRef | None = None
) -> LabApplication:
    # Start before framework imports so a slow import is observable too. One
    # stack dump fits inside the unchanged parent deadline; no retry is implied.
    faulthandler.dump_traceback_later(30, file=sys.stderr)
    try:
        report_stage("framework imports")
        from scopecat.daemon.endpoint import DAEMON_URL_ENV
        from scopecat.project import load_project

        os.environ.pop(DAEMON_URL_ENV, None)
        project = replace(
            load_project(code_root / "scopecat.toml"),
            root=root,
            code_root=code_root,
            code_revision=ref,
        )
        started = time.perf_counter()
        report_stage("source compilation")
        for source in project.source_roots:
            for path in (code_root / source).rglob("*.py"):
                compile(path.read_bytes(), str(path.relative_to(code_root)), "exec")
        compiled = time.perf_counter()
        report_stage("application import")
        application = project.load_application()
        loaded = time.perf_counter()
        report_stage("source identity validation")
        if application.authors is not None:
            for experiment in application.authors.experiments:
                name = experiment.source["module"]
                expected = author_module_path(project, name)
                if Path(cast("str", sys.modules[name].__file__)) != expected:
                    raise ValueError(
                        f"author module resolved outside its source snapshot: {name}"
                    )
        print(
            "Scopecat validation timing: "
            + json.dumps(
                {
                    "compilation": compiled - started,
                    "application": loaded - compiled,
                    "identity": time.perf_counter() - loaded,
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return application
    finally:
        faulthandler.cancel_dump_traceback_later()


def analyze(
    project: Project, application: LabApplication, request: AuthorAnalysisRequest
) -> AuthorAnalysisReceipt:
    from scopecat.api.analysis import (
        AnalysisContext,
        AnalysisDefinition,
        AnalysisFunctionDefinition,
    )
    from scopecat.daemon.endpoint import resolve_daemon_endpoint
    from scopecat.kernel.content_identity import canonical_json
    from scopecat.records.author_revision import (
        AuthorAnalysisReceipt,
    )

    with application.connect(
        resolve_daemon_endpoint(project.root), operator="author-analysis"
    ) as lab:
        module_name, separator, name = request.analysis.partition(":")
        if not separator or not module_name or not name:
            raise ValueError("analysis must use module:name")
        expected = author_module_path(project, module_name)
        module = import_module(module_name)
        if Path(cast("str", module.__file__)) != expected:
            raise ValueError("analysis resolved outside its source snapshot")
        definition = cast("object", getattr(module, name))
        if not isinstance(definition, AnalysisDefinition | AnalysisFunctionDefinition):
            raise ValueError("author analysis must use the existing analysis decorator")
        if isinstance(definition, AnalysisFunctionDefinition):
            from scopecat.analysis.arguments import bind_arguments

            arguments = bind_arguments(definition.function, request.arguments)
        else:
            arguments = request.arguments
        step = definition(**arguments)
        run = lab.get_run(request.run_id)
        result = step.run(
            AnalysisContext(
                run=run, default_key=request.key or step.id, step_id=step.id
            )
        )
        published = (
            result.fact("author_code_revision", request.code_revision.content_hash)
            .artifact(
                "author_analysis_arguments", text=canonical_json(request.arguments)
            )
            .save()
        )
        receipt = AuthorAnalysisReceipt(
            code_revision=request.code_revision, analysis_id=published.id
        )
    return receipt
