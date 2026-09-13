"""Validate a candidate before accepting revision-pinned launch requests."""

from __future__ import annotations

import faulthandler
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scopecat_server.worker_diagnostics import report_stage, report_validation_error

if TYPE_CHECKING:
    from io import TextIOWrapper


def main() -> None:
    # Start diagnostics before importing the framework.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    if sys.argv[2] != "--validate-serve":
        raise ValueError("source worker only accepts --validate-serve")
    import contextlib

    faulthandler.dump_traceback_later(30, file=sys.stderr)
    report_stage("framework imports")
    from pydantic import ValidationError
    from scopecat.records.author_revision import AuthorRevisionRef

    root = Path(sys.argv[1]).resolve()
    ref = AuthorRevisionRef(content_hash=sys.argv[4])
    with contextlib.redirect_stdout(sys.stderr):
        from scopecat_server.author_worker import validate

        application = validate(root, Path(sys.argv[3]), ref)
        from scopecat_server.launch_worker import serve
    print(ref.model_dump_json(), flush=True)
    try:
        serve(root, ref, application=application)
    except ValidationError as error:
        report_validation_error(error)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
