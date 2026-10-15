"""Fixed subprocess entry executed by a deployment's registered interpreter.

Run by file path so the target needs Scopecat, not the host's scopecat-app package.
Only trusted registration/operation workers supply these arguments.
"""

from __future__ import annotations

import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from scopecat.application_instance import ApplicationInstance
from scopecat.lab_settings import lab_settings_identity
from scopecat_server.lifecycle import inspect_daemon, start_project, stop_project
from scopecat_server.static_assets import select_static_dir


class Request(BaseModel):
    action: Literal["probe", "start", "stop", "register_source"]
    root: str
    static_dir: str | None
    environment: dict[str, str] = Field(default_factory=dict)
    settings_identity: str | None = None
    workspace: str | None = None
    author_python: Path | None = None


def main() -> None:
    request = Request.model_validate_json(sys.argv[1])
    # Qualification checks the application installation, not any author environment.
    project = (
        ApplicationInstance.open(request.root) if request.action != "probe" else None
    )
    if request.action == "stop":
        assert project is not None
        stop_project(project)
        Path(sys.argv[2]).write_text("{}", encoding="utf-8")
        return
    settings_identity = lab_settings_identity(request.root)
    if (
        request.action in ("start", "register_source")
        and settings_identity != request.settings_identity
    ):
        raise ValueError("本机设置已改变；请重新启动应用后重试")
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
    source_id: str | None = None
    if request.action == "register_source":
        from scopecat_server.author_registration import register_author_workspace

        if environment != request.environment:
            raise ValueError("应用运行环境已改变；请重新启动应用后重试")
        assert request.workspace is not None
        assert project is not None
        source_id = register_author_workspace(
            project.root, Path(request.workspace), python=request.author_python
        ).id
    if request.action == "start":
        assert project is not None
        if environment != request.environment:
            raise ValueError("应用运行环境已改变；请重新启动应用后重试。")
        status = inspect_daemon(project)
        if status.state in ("running", "degraded") and status.record is not None:
            executable = status.record.python
            if executable is None or os.path.normcase(
                str(executable.absolute())
            ) != os.path.normcase(str(Path(sys.executable).absolute())):
                raise ValueError(
                    "应用仍在运行，但无法确认它使用当前环境。"
                    "请选择“停止后台并重新启动”；已有记录保留。"
                )
        record = start_project(
            project,
            static_dir=gui,
            on_progress=lambda elapsed, stage: print(
                f"Starting ({elapsed:.0f}s): {stage}", flush=True
            ),
        )
        executable = record.python
        if executable is None or os.path.normcase(
            str(executable.absolute())
        ) != os.path.normcase(str(Path(sys.executable).absolute())):
            raise ValueError(
                "服务已启动，但运行环境未通过检查。"
                "请选择“停止后台并重新启动”；已有记录保留。"
            )
    Path(sys.argv[2]).write_text(
        json.dumps(
            {
                "root": request.root,
                "source_id": source_id,
                "static_dir": str(gui),
                "environment": environment,
                "settings_identity": settings_identity,
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        Path(sys.argv[2]).write_text(
            json.dumps({"error": str(error)}), encoding="utf-8"
        )
        raise
