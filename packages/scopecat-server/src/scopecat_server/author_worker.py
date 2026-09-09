"""Fresh-process validation and retained-data analysis of immutable source trees."""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import cast

from scopecat.api.analysis import AnalysisContext, AnalysisDefinition
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV, resolve_daemon_endpoint
from scopecat.project import Project, load_project
from scopecat.project_sources import materialize_sources, require_environment
from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorRevisionRef,
)


def revision_project(root: Path, ref: AuthorRevisionRef) -> Project:
    """Read original identity before importing any project implementation."""
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


def main() -> None:
    os.environ.pop(DAEMON_URL_ENV, None)
    root = Path(sys.argv[1]).resolve()
    if sys.argv[2] == "--validate":
        code_root = Path(sys.argv[3])
        project = replace(
            load_project(code_root / "scopecat.toml"), root=root, code_root=code_root
        )
        for source in project.source_roots:
            for path in (code_root / source).rglob("*.py"):
                compile(path.read_bytes(), str(path.relative_to(code_root)), "exec")
        project.load_application()
        return
    request = AuthorAnalysisRequest.model_validate_json(sys.stdin.read())
    with contextlib.redirect_stdout(sys.stderr):
        project = revision_project(root, request.code_revision)
        with project.connect(operator="author-analysis") as lab:
            module_name, separator, name = request.analysis.partition(":")
            if not separator or not module_name or not name:
                raise ValueError("analysis must use module:name")
            module = import_module(module_name)
            source = Path(cast("str", module.__file__)).relative_to(
                project.code_root or root
            )
            if not any(source.is_relative_to(path) for path in project.refresh_roots):
                raise ValueError(
                    "analysis must belong to a configured author refresh root"
                )
            definition = cast("object", getattr(module, name))
            if not isinstance(definition, AnalysisDefinition):
                raise ValueError(
                    "author analysis must use the existing analysis decorator"
                )
            step = definition()
            run = lab.get_run(request.run_id)
            result = step.run(
                AnalysisContext(
                    run=run, default_key=request.key or step.id, step_id=step.id
                )
            )
            published = result.fact(
                "author_code_revision", request.code_revision.content_hash
            ).save()
            receipt = AuthorAnalysisReceipt(
                code_revision=request.code_revision, analysis_id=published.id
            )
    print(receipt.model_dump_json())


if __name__ == "__main__":
    main()
