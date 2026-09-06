"""Execute project preview callbacks outside the instrument daemon process."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import scopecat as sc
from scopecat.project import load_project

from .launch import LaunchRequest

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


def run_procedure(lab: LabClient, procedure_id: str) -> None:
    """Run until the next durable boundary; review waits belong to the manager."""
    handle = lab.procedures.get(procedure_id)
    if handle.state == "ready":
        handle.resume()


def main() -> None:
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
        if application.launch_provider is None:
            result = {"calibrations": []}
            if request.action != "list":
                raise ValueError("project has no experiment preview provider")
        else:
            with sc.open_project(root).connect(operator=request.actor) as lab:
                result = application.launch_provider(lab, request)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
