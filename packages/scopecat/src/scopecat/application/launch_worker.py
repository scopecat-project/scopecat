"""Execute project preview callbacks outside the instrument daemon process."""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

import scopecat as sc
from scopecat.project import load_project

from .launch import LaunchRequest


def run_procedure(lab, procedure_id: str) -> None:
    """Wait across operator review; durable state remains authoritative."""
    while True:
        handle = lab.procedures.get(procedure_id)
        state = handle.state
        if state in {"closed", "attention_required"}:
            return
        if state == "ready":
            handle.resume()
        else:
            time.sleep(1)


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
