"""Invoke a lab's retained-data model outside the daemon; never dispatch runs."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scopecat.application.comparison import ComparisonHandoff
from scopecat.daemon.endpoint import DAEMON_URL_ENV, resolve_daemon_endpoint
from scopecat.project import load_project
from scopecat.records.comparison import (
    ComparisonCatalog,
    ComparisonInspection,
    ComparisonRequest,
)

if TYPE_CHECKING:
    from io import TextIOWrapper

    from scopecat.application import LabApplication
    from scopecat.application.comparison import ComparisonResult


def main() -> None:
    os.environ.pop(DAEMON_URL_ENV, None)
    request = ComparisonRequest.model_validate_json(sys.stdin.read())
    root = Path(sys.argv[1]).resolve()
    if request.action != "list" or request.code_revision is not None:
        raise ValueError("one-shot comparison worker only accepts unversioned catalogs")
    project = load_project(root / "scopecat.toml")
    with contextlib.redirect_stdout(sys.stderr):
        application = project.load_application()
        result = compare(application, root, request)
    print(result.model_dump_json())


def compare(
    application: LabApplication, root: Path, request: ComparisonRequest
) -> ComparisonResult:
    if application.comparison_provider is None:
        if request.action != "list":
            raise ValueError("Project has no retained-run comparison provider")
        result = ComparisonCatalog()
    else:
        with application.connect(
            resolve_daemon_endpoint(root), operator=request.actor
        ) as lab:
            result = application.comparison_provider(lab, request)
    if isinstance(result, ComparisonCatalog | ComparisonInspection):
        result = result.model_copy(
            update={
                "code_revision": request.code_revision,
                "workspace_id": request.workspace_id,
            }
        )
    if isinstance(result, ComparisonHandoff):
        result = result.model_copy(
            update={
                "request": result.request.model_copy(
                    update={
                        "workspace_id": request.workspace_id,
                        "code_revision": request.code_revision,
                    }
                )
            }
        )
    return result


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    main()
