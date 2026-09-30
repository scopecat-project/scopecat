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

from pydantic import BaseModel, Field

from scopecat.execution_environment import execution_packages
from scopecat.installed_authors import capture_installed_authors
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.lab_settings import lab_settings_identity
from scopecat.project import load_project, open_project
from scopecat_server.lifecycle import inspect_daemon, start_project, stop_project
from scopecat_server.static_assets import select_static_dir


class Request(BaseModel):
    action: Literal["probe", "start", "stop", "register_source"]
    root: str
    static_dir: str | None
    environment: dict[str, str] = Field(default_factory=dict)
    settings_identity: str | None = None
    adapter_identity: str | None = None
    workspace: str | None = None
    manifest: str | None = None
    author_python: Path | None = None


def main() -> None:
    request = Request.model_validate_json(sys.argv[1])
    project = (
        load_project(request.manifest)
        if request.action == "probe" and request.manifest is not None
        else open_project(request.root, resolve_adapter=request.action != "stop")
    )
    if request.action == "stop":
        stop_project(project)
        Path(sys.argv[2]).write_text("{}", encoding="utf-8")
        return
    if project.author_only:
        raise ValueError("作者目录不能作为应用运行目录；请通过应用 Settings 登记源码")
    settings_identity = lab_settings_identity(project.root)
    adapter_identity = (
        sha256_json_hash(
            {
                name: package.model_dump(mode="json")
                for name, package in capture_installed_authors(
                    project.adapter_packages
                ).items()
            }
        )
        if project.adapter_packages
        else None
    )
    if (
        request.action in ("start", "register_source")
        and adapter_identity != request.adapter_identity
    ):
        raise ValueError("应用能力包已改变；请选择“停止并重新核验当前环境”后重试")
    if (
        request.action in ("start", "register_source")
        and settings_identity != request.settings_identity
    ):
        raise ValueError("本机设置已改变；请选择“停止并重新核验当前环境”后重试")
    if request.action == "probe":
        execution_packages(
            (
                *(project.dependencies or ()),
                *(name for _, name in project.installed_packages),
            )
        )
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
    drivers: dict[str, object] | None = None
    if request.action == "probe" and project.instrument_backend_spec is not None:
        from scopecat_server.instruments.worker import (
            SubprocessInstrumentBackendEndpoint,
        )

        endpoint = SubprocessInstrumentBackendEndpoint(
            project.root,
            project.instrument_backend_spec,
            installed_packages=project.adapter_packages,
            startup_timeout=30,
        )
        try:
            # Metadata qualification must not describe bindings or connect devices.
            drivers = {
                "catalog": endpoint.driver_catalog.model_dump(mode="json"),
                "artifact_hash": endpoint.artifact_hash,
            }
        finally:
            endpoint.shutdown()
    if request.action == "register_source":
        from scopecat_server.author_registration import register_author_workspace

        if environment != request.environment:
            raise ValueError("登记的 Python 环境已改变；请先重新核验当前应用环境")
        assert request.workspace is not None
        source_id = register_author_workspace(
            project.root, Path(request.workspace), python=request.author_python
        ).id
    if request.action == "start":
        if environment != request.environment:
            raise ValueError(
                "选定的 Python 环境已改变。请选择"
                "“停止并重新核验当前环境”，完成后重新启动。"
            )
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
                "root": str(project.root),
                "source_id": source_id,
                "static_dir": str(gui),
                "environment": environment,
                "settings_identity": settings_identity,
                "adapter_identity": adapter_identity,
                "drivers": drivers,
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
