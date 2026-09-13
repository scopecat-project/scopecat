"""Lightweight execution entry point with observable framework startup."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scopecat.kernel.interaction_timing import record_timing

if TYPE_CHECKING:
    from io import TextIOWrapper


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    root, procedure_id = Path(sys.argv[1]).resolve(), sys.argv[2]
    record_timing("procedure_worker_entry", procedure_id=procedure_id)
    from pydantic import ValidationError
    from scopecat.daemon.endpoint import DAEMON_URL_ENV

    from scopecat_server.launch_worker import run_project_procedure
    from scopecat_server.worker_diagnostics import report_validation_error

    os.environ.pop(DAEMON_URL_ENV, None)
    record_timing("framework_ready", procedure_id=procedure_id)
    try:
        run_project_procedure(root, procedure_id)
    except ValidationError as error:
        report_validation_error(error)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
