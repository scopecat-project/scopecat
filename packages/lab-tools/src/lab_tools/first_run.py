"""Create or connect ordinary experiment folders without starting a daemon."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from scopecat.project import load_project
from scopecat.runtime_binding import RUNTIME_BINDING_NAME, load_runtime_binding
from scopecat_server.scaffold import write_project_scaffold
from scopecat_server.static_assets import select_static_dir

from .host_models import SetupRequest as SetupRequest
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


def _data_binding(project: Path, requested: str | None) -> str | None:
    """Allow initial placement only; setup never relocates an existing store."""
    existing = load_runtime_binding(project)
    if requested is None:
        return None
    data = Path(requested).resolve()
    if data == existing.data_root:
        return None
    state = project / ".scopecat"
    if (
        (project / RUNTIME_BINDING_NAME).exists()
        or state.exists()
        or state.is_symlink()
    ):
        raise ValueError("已有运行位置或数据，不能在首次接入时更改；请保留现有数据目录")
    if data.exists() or Path(requested).is_symlink():
        raise ValueError("新数据目录必须尚不存在；已有数据请从原代码目录接入")
    if project.is_relative_to(data) or data.is_relative_to(project):
        raise ValueError("自选数据目录必须与代码目录分开，不能互相包含")
    deployment = json.dumps(str(existing.deployment_root), ensure_ascii=False)
    return (
        "[runtime]\n"
        f"data_root = {json.dumps(str(data), ensure_ascii=False)}\n"
        f"deployment_root = {deployment}\n"
    )


def setup(
    home: Path, request: SetupRequest, *, static_dir: Path | None = None
) -> Service:
    """Validate, retain a runnable folder, then register; acquisition is separate.

    Creation publishes the scaffold with one directory rename. An interrupted
    unpublished staging folder contains no scientific state. After publication,
    failures preserve the user's folder; reconnecting is the explicit retry path.
    """
    gui = select_static_dir(static_dir=static_dir, api_only=False)
    project = Path(request.project).resolve()
    if request.mode == "create":
        if project.exists() or Path(request.project).is_symlink():
            raise ValueError("新代码目录必须尚不存在；已有目录请选择接入实验室")
        if not project.parent.is_dir():
            raise ValueError("代码目录的上级目录不存在；请先选择已有位置")
    else:
        if not (project / "scopecat.toml").is_file():
            raise ValueError("请选择直接包含 scopecat.toml 的实验室代码目录")
        load_project(project / "scopecat.toml")
    python = choose_python(project)
    binding = _data_binding(project, request.data_root)
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
        # Exclusive creation cannot replace a binding written by another setup.
        with (project / RUNTIME_BINDING_NAME).open("x", encoding="utf-8") as stream:
            stream.write(binding)
    try:
        return Services(home).register(project, python, name=name, static_dir=gui)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"代码和数据位置已保留在 {project}。接入检查失败：{error}。"
            "修复环境后选择接入已有实验室重试；尚未启动实验服务。"
        ) from error
