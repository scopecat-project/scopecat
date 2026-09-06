"""Execute project preview callbacks outside the instrument daemon process."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import scopecat as sc
from scopecat.project import load_project

from .launch import LaunchRequest


def main() -> None:
    request = LaunchRequest.model_validate_json(sys.stdin.read())
    root = Path(sys.argv[1]).resolve()
    with contextlib.redirect_stdout(sys.stderr):
        application = load_project(root / "scopecat.toml").load_application()
        if application.launch_provider is None:
            result = {"calibrations": []}
            if request.action != "list":
                raise ValueError("project has no experiment preview provider")
        else:
            with sc.open_project(root).connect(operator="experiment-preview") as lab:
                result = application.launch_provider(lab, request)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()

