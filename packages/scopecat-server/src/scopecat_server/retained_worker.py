"""Reuse one immutable application for analysis and comparison, not their results."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from io import TextIOWrapper
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.records.author_revision import AuthorRevisionRef

from scopecat_server.author_worker import analyze, revision_project
from scopecat_server.comparison_worker import compare
from scopecat_server.retained_request import AnalysisCall, RetainedRequest
from scopecat_server.worker_diagnostics import report_stage, report_validation_error


def main() -> None:
    os.environ.pop(DAEMON_URL_ENV, None)
    root = Path(sys.argv[1]).resolve()
    ref = AuthorRevisionRef(content_hash=sys.argv[3])
    adapter: TypeAdapter[RetainedRequest] = TypeAdapter(RetainedRequest)
    project = None
    application = None
    for line in sys.stdin:
        started = time.perf_counter()
        call = adapter.validate_json(line)
        if call.code_revision != ref:
            raise ValueError("worker requires its exact retained source revision")
        phases: dict[str, float] = {}
        with contextlib.redirect_stdout(sys.stderr):
            if project is None:
                report_stage("author revision initialization")
                project = revision_project(root, ref)
                now = time.perf_counter()
                phases["revision"] = now - started
                report_stage("project application load")
                application = project.load_application()
                phases["application"] = time.perf_counter() - now
            assert application is not None
            now = time.perf_counter()
            if isinstance(call, AnalysisCall):
                report_stage("retained analysis")
                result = analyze(project, application, call.request)
            else:
                report_stage("retained comparison")
                result = compare(application, root, call.request)
            phases["operation"] = time.perf_counter() - now
            encoded = result.model_dump_json()
            phases["worker"] = time.perf_counter() - started
            print(
                "Scopecat launch timing: " + json.dumps(phases),
                file=sys.stderr,
                flush=True,
            )
        print(encoded, flush=True)


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    try:
        main()
    except ValidationError as error:
        report_validation_error(error)
        raise SystemExit(1) from None
