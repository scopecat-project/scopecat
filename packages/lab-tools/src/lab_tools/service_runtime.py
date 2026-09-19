"""Fixed subprocess entry executed by a deployment's registered interpreter.

Run by file path so the target needs Scopecat, not the host's lab-tools package.
Only trusted registration/operation workers supply these arguments.
"""

from __future__ import annotations

import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, Field

from scopecat.project import open_project
from scopecat_server.cli import select_static_dir
from scopecat_server.lifecycle import inspect_daemon, start_project


class Request(BaseModel):
    action: Literal["probe", "start"]
    root: str
    static_dir: str | None
    environment: dict[str, str] = Field(default_factory=dict)


def main() -> None:
    request = Request.model_validate_json(sys.argv[1])
    project = open_project(request.root)
    gui = select_static_dir(
        static_dir=Path(request.static_dir) if request.static_dir else None,
        api_only=False,
    )
    environment = {
        "prefix": sys.prefix,
        "python": sys.version,
        "scopecat": version("scopecat"),
        "server": version("scopecat-server"),
    }
    if request.action == "start":
        if environment != request.environment:
            raise ValueError(
                "Registered runtime changed; register this service again "
                "before starting it"
            )
        status = inspect_daemon(project)
        if status.state == "running" and status.record is not None:
            executable = psutil.Process(status.record.pid).cmdline()[0]
            if os.path.normcase(str(Path(executable).absolute())) != os.path.normcase(
                str(Path(sys.executable).absolute())
            ):
                raise ValueError(
                    "Existing daemon uses another interpreter; stop it explicitly "
                    "before switching environments"
                )
        record = start_project(
            project,
            static_dir=gui,
            on_progress=lambda elapsed, stage: print(
                f"Starting ({elapsed:.0f}s): {stage}", flush=True
            ),
        )
        executable = psutil.Process(record.pid).cmdline()[0]
        if os.path.normcase(str(Path(executable).absolute())) != os.path.normcase(
            str(Path(sys.executable).absolute())
        ):
            raise ValueError(
                "Daemon started in another interpreter; "
                "retain it and stop explicitly before switching"
            )
    Path(sys.argv[2]).write_text(
        json.dumps(
            {
                "root": str(project.root),
                "static_dir": str(gui),
                "environment": environment,
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
