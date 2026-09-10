"""Fresh-process validation and retained-data analysis of immutable source trees."""

from __future__ import annotations

import contextlib
import faulthandler
import os
import sys
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scopecat_server.worker_diagnostics import report_stage

if TYPE_CHECKING:
    from scopecat.project import Project
    from scopecat.records.author_revision import AuthorRevisionRef


def revision_project(root: Path, ref: AuthorRevisionRef) -> Project:
    """Read original identity before importing any project implementation."""
    from scopecat.daemon.client import DaemonClient
    from scopecat.daemon.endpoint import resolve_daemon_endpoint
    from scopecat.project import load_project
    from scopecat.project_sources import materialize_sources, require_environment

    with DaemonClient(resolve_daemon_endpoint(root)) as client:
        bundle = client.author_revision(ref)
    require_environment(bundle.manifest)
    code_root = materialize_sources(bundle, root / ".scopecat" / "code")
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


def validate(root: Path, code_root: Path) -> None:
    # Start before framework imports so a slow import is observable too. One
    # stack dump fits inside the unchanged parent deadline; no retry is implied.
    faulthandler.dump_traceback_later(30, file=sys.stderr)
    try:
        report_stage("framework imports")
        from scopecat.daemon.endpoint import DAEMON_URL_ENV
        from scopecat.project import load_project

        os.environ.pop(DAEMON_URL_ENV, None)
        project = replace(
            load_project(code_root / "scopecat.toml"), root=root, code_root=code_root
        )
        report_stage("source compilation")
        for source in project.source_roots:
            for path in (code_root / source).rglob("*.py"):
                compile(path.read_bytes(), str(path.relative_to(code_root)), "exec")
        report_stage("application import")
        application = project.load_application()
        report_stage("source identity validation")
        if application.authors is not None:
            for experiment in application.authors.experiments:
                name = experiment.source["module"]
                expected = author_module_path(project, name)
                if Path(cast("str", sys.modules[name].__file__)) != expected:
                    raise ValueError(
                        f"author module resolved outside its source snapshot: {name}"
                    )
    finally:
        faulthandler.cancel_dump_traceback_later()


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    if sys.argv[2] == "--validate":
        validate(root, Path(sys.argv[3]))
        return

    from scopecat.api.analysis import (
        AnalysisContext,
        AnalysisDefinition,
        AnalysisFunctionDefinition,
    )
    from scopecat.daemon.endpoint import DAEMON_URL_ENV
    from scopecat.kernel.content_identity import canonical_json
    from scopecat.records.author_revision import (
        AuthorAnalysisReceipt,
        AuthorAnalysisRequest,
    )

    os.environ.pop(DAEMON_URL_ENV, None)
    request = AuthorAnalysisRequest.model_validate_json(sys.stdin.read())
    with contextlib.redirect_stdout(sys.stderr):
        project = revision_project(root, request.code_revision)
        with project.connect(operator="author-analysis") as lab:
            module_name, separator, name = request.analysis.partition(":")
            if not separator or not module_name or not name:
                raise ValueError("analysis must use module:name")
            expected = author_module_path(project, module_name)
            module = import_module(module_name)
            if Path(cast("str", module.__file__)) != expected:
                raise ValueError("analysis resolved outside its source snapshot")
            definition = cast("object", getattr(module, name))
            if not isinstance(
                definition, AnalysisDefinition | AnalysisFunctionDefinition
            ):
                raise ValueError(
                    "author analysis must use the existing analysis decorator"
                )
            step = definition(**request.arguments)
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
    print(receipt.model_dump_json())


if __name__ == "__main__":
    main()
