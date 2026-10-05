"""Help's bounded ordinary-author Notebook journey in the current application."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict

from lab_teaching.lessons import install_lesson

from .author_environment import (
    create_client_environment,
    environment_python,
    prepare_execution_environment,
)

if TYPE_CHECKING:
    from .application_runtime import ApplicationRuntime


class ParametersJourney(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: Path
    ready: bool = False

    @property
    def notebook(self) -> Path:
        return self.directory / "notebooks/parameters.ipynb"

    def view(self) -> dict[str, object]:
        return {
            "directory": str(self.directory),
            "notebook": str(self.notebook),
            "python": str(environment_python(self.directory / ".venv")),
            "ready": self.ready,
        }


def _receipt(runtime: ApplicationRuntime) -> Path:
    return runtime.home / "learning/parameters.json"


def _save(receipt: Path, journey: ParametersJourney) -> None:
    staged = receipt.with_suffix(".tmp")
    _ = staged.write_text(journey.model_dump_json(indent=2), encoding="utf-8")
    staged.replace(receipt)


def current(runtime: ApplicationRuntime) -> ParametersJourney | None:
    path = _receipt(runtime)
    return (
        ParametersJourney.model_validate_json(path.read_bytes())
        if path.exists()
        else None
    )


def create_parameters_source(directory: Path) -> None:
    """Publish one fresh, dependency-light folder; never overlay existing files."""
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise ValueError(f"代码目录已存在，未覆盖：{directory}")
    with TemporaryDirectory(prefix=".parameters-", dir=directory.parent) as temporary:
        staged = Path(temporary) / "source"
        (staged / "src/my_experiment").mkdir(parents=True)
        (staged / "src/my_experiment/__init__.py").touch()
        (staged / "notebooks").mkdir()
        _ = install_lesson(staged, "parameters")
        (staged / "scopecat.toml").write_text(
            '[lab.capabilities]\nauthor_modules = ["my_experiment"]\n\n'
            '[authors]\nsource_roots = ["src"]\n'
            'refresh_roots = ["src/my_experiment"]\ndependencies = []\n',
            encoding="utf-8",
        )
        (staged / "pyproject.toml").write_text(
            '[project]\nname = "parameters-lesson"\nversion = "0.1.0"\n'
            'requires-python = ">=3.14"\ndependencies = ["numpy"]\n',
            encoding="utf-8",
        )
        (staged / ".vscode").mkdir()
        (staged / ".vscode/settings.json").write_text(
            json.dumps({"python.defaultInterpreterPath": "${workspaceFolder}/.venv"}),
            encoding="utf-8",
        )
        (staged / ".vscode/extensions.json").write_text(
            json.dumps({"recommendations": ["ms-python.python", "ms-toolsai.jupyter"]}),
            encoding="utf-8",
        )
        (staged / ".gitignore").write_text(
            ".venv/\n.scopecat*/\n**/__pycache__/\n.ipynb_checkpoints/\n",
            encoding="utf-8",
        )
        staged.rename(directory)


def prepare(
    runtime: ApplicationRuntime, parent: str | None = None
) -> ParametersJourney:
    """Retry preparation without rewriting source; continue without dependency work."""
    receipt = _receipt(runtime)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(receipt.with_suffix(".lock"), timeout=30):
        journey = current(runtime)
        if journey is None:
            destination = Path(parent) if parent else runtime.home / "authors"
            if not destination.is_absolute() or (parent and not destination.is_dir()):
                raise ValueError("请选择已有保存目录的完整路径")
            journey = ParametersJourney(
                directory=destination / f"parameters-{uuid4().hex[:12]}"
            )
            create_parameters_source(journey.directory)
            _save(receipt, journey)
        if not journey.notebook.is_file():
            raise ValueError(
                f"练习文件不可用，请恢复原目录后继续；不会重建或覆盖修改：{journey.directory}"
            )
        if journey.ready:
            _ = runtime.source(journey.directory)
            if not environment_python(journey.directory / ".venv").is_file():
                raise ValueError(
                    "本地 Python 缺失；在 Settings 重建此作者目录的本地环境后继续"
                )
            return journey
        _ = create_client_environment(runtime, journey.directory)
        python = prepare_execution_environment(runtime, journey.directory)
        _ = runtime.register_source(journey.directory, python=python)
        journey = journey.model_copy(update={"ready": True})
        _save(receipt, journey)
        return journey


def open_editor(journey: ParametersJourney) -> None:
    """Open the shipped file and its folder in an external editor, never a kernel."""
    code = shutil.which("code")
    if sys.platform == "win32" and code is not None:
        # Use the native executable, never pass user paths through code.cmd.
        executable = Path(code).parent.parent / "Code.exe"
        code = str(executable) if executable.is_file() else None
    if sys.platform == "darwin":
        command = [
            "/usr/bin/open",
            "-a",
            "Visual Studio Code",
            str(journey.directory),
            str(journey.notebook),
        ]
    elif code is not None:
        command = [code, "--new-window", str(journey.directory), str(journey.notebook)]
    else:
        raise ValueError(
            "未找到 VS Code 的 code 命令。请在编辑器手动打开下方目录与 Notebook，"
            "选择所列 Python 内核；文件和准备结果已保留。"
        )
    if sys.platform == "win32":
        # Code.exe owns a GUI lifetime; waiting for it would time out on first launch.
        _ = subprocess.Popen(  # noqa: S603 - native editor and argument vector
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return
    result = subprocess.run(  # noqa: S603 - fixed editor executable and argument vector
        command,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            "编辑器未打开；请手动打开下方 Notebook。" + result.stderr[-2048:]
        )
