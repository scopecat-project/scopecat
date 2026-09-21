"""Create or connect ordinary experiment folders without starting a daemon."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

from filelock import FileLock, Timeout

from scopecat.lab_settings import validate_lab_settings_file
from scopecat.project import load_project
from scopecat.runtime_binding import RUNTIME_BINDING_NAME, load_runtime_binding
from scopecat_server.lifecycle import inspect_daemon
from scopecat_server.scaffold import write_project_scaffold
from scopecat_server.static_assets import select_static_dir

from .bundle import verify_bundle
from .host_models import SetupRequest as SetupRequest
from .lab_environment import prepare_environment
from .services import Service, Services


def choose_python(project: Path) -> Path:
    """Use a selected folder's environment, never hide a broken local environment."""
    environment = project / ".venv"
    if environment.exists() or environment.is_symlink():
        python = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        if not python.is_file():
            raise ValueError("代码目录的 .venv 不完整；请修复实验室环境后重新接入")
        return python.absolute()
    return Path(sys.executable).absolute()


def _runtime_binding(project: Path, request: SetupRequest) -> str | None:
    """Select local settings while retaining all existing scientific locations."""
    existing = load_runtime_binding(project)
    data = existing.data_root
    if request.data_root is not None:
        requested = Path(request.data_root).resolve()
        if requested != data:
            state = project / ".scopecat"
            if (
                (project / RUNTIME_BINDING_NAME).exists()
                or state.exists()
                or state.is_symlink()
            ):
                raise ValueError(
                    "已有运行位置或数据，不能在首次接入时更改；请保留现有数据目录"
                )
            if requested.exists() or Path(request.data_root).is_symlink():
                raise ValueError("新数据目录必须尚不存在；已有数据请从原代码目录接入")
            if project.is_relative_to(requested) or requested.is_relative_to(project):
                raise ValueError("自选数据目录必须与代码目录分开，不能互相包含")
            data = requested
    settings = existing.settings_file
    if request.settings_file is not None:
        settings = validate_lab_settings_file(request.settings_file)
    if data == existing.data_root and settings == existing.settings_file:
        return None
    deployment = json.dumps(str(existing.deployment_root), ensure_ascii=False)
    document = (
        "[runtime]\n"
        f"data_root = {json.dumps(str(data), ensure_ascii=False)}\n"
        f"deployment_root = {deployment}\n"
    )
    if settings is not None:
        document += f"settings_file = {json.dumps(str(settings), ensure_ascii=False)}\n"
    return document


def _update_binding(project: Path, document: str, before: bytes | None) -> None:
    """Replace a sidecar atomically only while its daemon ownership is free."""
    selected = load_project(project / "scopecat.toml", resolve_adapter=False)
    binding = selected.runtime_binding
    destination = project / RUNTIME_BINDING_NAME
    try:
        with ExitStack() as stack:
            for path in (
                binding.deployment_root / "deployment.lock",
                binding.data_root / "daemon.lock",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                stack.enter_context(FileLock(path, timeout=0))
            if inspect_daemon(selected).state != "stopped":
                raise ValueError("更改本机设置前请先停止实验服务，并确认服务已停止")
            current = destination.read_bytes() if destination.exists() else None
            if current != before:
                raise ValueError("运行绑定已被其他操作更改，请重新接入")
            descriptor, temporary = tempfile.mkstemp(prefix=".runtime-", dir=project)
            staged = Path(temporary)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(document)
                staged.replace(destination)
            finally:
                staged.unlink(missing_ok=True)
    except Timeout as error:
        raise ValueError("更改本机设置前请先停止实验服务，并确认服务已停止") from error


def setup(
    home: Path, request: SetupRequest, *, static_dir: Path | None = None
) -> Service:
    """Validate, retain a runnable folder, then register; acquisition is separate.

    Creation publishes the scaffold with one directory rename. An interrupted
    unpublished staging folder contains no scientific state. After publication,
    failures preserve the user's folder; reconnecting is the explicit retry path.
    """
    delivery = (
        Path(request.environment_bundle).resolve()
        if request.environment_bundle
        else None
    )
    if delivery is not None:
        verify_bundle(delivery)
    gui = select_static_dir(
        static_dir=delivery / "gui" if delivery is not None else static_dir,
        api_only=False,
    )
    project = Path(request.project).resolve()
    if request.mode == "create":
        if project.exists() or Path(request.project).is_symlink():
            raise ValueError("新代码目录必须尚不存在；已有目录请选择接入实验室")
        if not project.parent.is_dir():
            raise ValueError("代码目录的上级目录不存在；请先选择已有位置")
    else:
        if not (project / "scopecat.toml").is_file():
            raise ValueError("请选择直接包含 scopecat.toml 的实验室代码目录")
        selected = load_project(project / "scopecat.toml", resolve_adapter=False)
        if selected.author_only:
            raise ValueError(
                "这是作者代码目录，请先登记到已有实验室，再使用 "
                "scopecat app --workspace 打开；不能作为新实验室接入"
            )
    python = choose_python(project) if delivery is None else None
    location = project / RUNTIME_BINDING_NAME
    previous_binding = location.read_bytes() if location.exists() else None
    binding = _runtime_binding(project, request)
    name = request.name.strip() if request.name else project.name
    if not name:
        raise ValueError("请填写实验室名称")
    if request.mode == "create":
        with tempfile.TemporaryDirectory(
            prefix=".scopecat-setup-", dir=project.parent
        ) as temporary:
            staged = Path(temporary) / "project"
            staged.mkdir()
            write_project_scaffold(staged)
            (staged / ".gitignore").write_text(
                ".scopecat/\nscopecat.runtime.toml\n.venv/\n", encoding="utf-8"
            )
            if binding is not None:
                (staged / RUNTIME_BINDING_NAME).write_text(binding, encoding="utf-8")
            staged.rename(project)
    elif binding is not None:
        _update_binding(project, binding, previous_binding)
    try:
        if delivery is not None:
            prepared = prepare_environment(project, delivery, home)
            python, gui = prepared.python, prepared.gui
        assert python is not None
        return Services(home).register(project, python, name=name, static_dir=gui)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError(
            f"代码和数据位置已保留在 {project}。环境准备或接入检查失败：{error}。"
            "修复环境后选择接入已有实验室重试；尚未启动实验服务。"
        ) from error
