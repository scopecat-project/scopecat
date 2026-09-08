"""Execute project preview callbacks outside the instrument daemon process."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import scopecat as sc
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchRequest,
    LaunchResult,
    validate_launch_control_edits,
)
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.project import load_project

if TYPE_CHECKING:
    from io import TextIOWrapper

    from scopecat.api.lab import LabClient


def run_procedure(lab: LabClient, procedure_id: str) -> None:
    """Run until the next durable boundary; review waits belong to the manager."""
    handle = lab.procedures.get(procedure_id)
    if handle.state == "ready":
        handle.resume()


def main() -> None:
    # This internal worker belongs to its spawning project, even when the server
    # inherited a user's endpoint override for a different interactive session.
    os.environ.pop(DAEMON_URL_ENV, None)
    if len(sys.argv) == 4 and sys.argv[2] == "--procedure":
        with sc.open_project(Path(sys.argv[1])).connect(
            operator="console-worker"
        ) as lab:
            run_procedure(lab, sys.argv[3])
        return
    request = LaunchRequest.model_validate_json(sys.stdin.read())
    root = Path(sys.argv[1]).resolve()
    with contextlib.redirect_stdout(sys.stderr):
        application = load_project(root / "scopecat.toml").load_application()
        result: LaunchResult
        if application.launch_provider is None:
            result = LaunchCatalog()
            if request.action != "list":
                raise ValueError("project has no experiment preview provider")
        else:
            with sc.open_project(root).connect(operator=request.actor) as lab:
                if request.control_edits:
                    catalog = application.launch_provider(
                        lab, LaunchRequest(action="list")
                    )
                    if not isinstance(catalog, LaunchCatalog):
                        raise TypeError(
                            "project list callback must return LaunchCatalog"
                        )
                    validate_launch_control_edits(catalog, request)
                result = application.launch_provider(lab, request)
    print(result.model_dump_json())


if __name__ == "__main__":
    # The subprocess JSON protocol and redirected diagnostics are UTF-8 on every OS.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    main()
