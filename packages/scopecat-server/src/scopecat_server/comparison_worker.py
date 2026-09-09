"""Invoke a lab's retained-data model outside the daemon; never dispatch runs."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scopecat.analysis.comparison import reopen_comparison
from scopecat.application.comparison import ComparisonCatalog, ComparisonRequest
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV, resolve_daemon_endpoint
from scopecat.project import load_project

if TYPE_CHECKING:
    from io import TextIOWrapper


def main() -> None:
    os.environ.pop(DAEMON_URL_ENV, None)
    request = ComparisonRequest.model_validate_json(sys.stdin.read())
    root = Path(sys.argv[1]).resolve()
    if request.action in ("candidate", "reject", "handoff"):
        with DaemonClient(resolve_daemon_endpoint(root)) as client:
            request = reopen_comparison(
                request, client.analysis(request.primary_run, request.analysis_id)
            )
    project = load_project(root / "scopecat.toml")
    with contextlib.redirect_stdout(sys.stderr):
        application = project.load_application()
        if application.comparison_provider is None:
            if request.action != "list":
                raise ValueError("Project has no retained-run comparison provider")
            result = ComparisonCatalog()
        else:
            with project.connect(operator=request.actor) as lab:
                result = application.comparison_provider(lab, request)
    print(result.model_dump_json())


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    main()
