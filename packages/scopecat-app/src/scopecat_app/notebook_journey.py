"""Help's bounded ordinary-author Notebook journey in the current application."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx2
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict

from scopecat.records.practice import NotebookPracticeClearCommand, PracticeScope
from scopecat_tutorials.lessons import TOPICS, LessonTopic, install_lesson

from .author_environment import (
    create_client_environment,
    environment_python,
    prepare_execution_environment,
)

if TYPE_CHECKING:
    from .application_runtime import ApplicationRuntime


def _validate_topic(topic: str) -> None:
    if topic not in TOPICS:
        raise ValueError(f"Help 尚不支持此课程：{topic}")


class NotebookJourney(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: LessonTopic = "parameters"
    directory: Path
    ready: bool = False
    rebuild_client: bool = False
    registered_context: bool = False
    scope_id: str | None = None
    workspace_id: str | None = None
    cleared: bool = False
    directory_device: int | None = None
    directory_inode: int | None = None
    cleanup_command: NotebookPracticeClearCommand | None = None

    @property
    def notebook(self) -> Path:
        return self.directory / f"notebooks/{self.topic}.ipynb"

    def view(self) -> dict[str, object]:
        return {
            "directory": str(self.directory),
            "notebook": str(self.notebook),
            "python": str(environment_python(self.directory / ".venv")),
            "ready": self.ready,
            "scope_id": self.scope_id,
            "cleared": self.cleared,
            "cleanup_files": self.cleanup_command.files
            if self.cleanup_command
            else None,
        }


def receipt_path(runtime: ApplicationRuntime, topic: LessonTopic) -> Path:
    _validate_topic(topic)
    return runtime.home / f"learning/{topic}.json"


def save_journey(receipt: Path, journey: NotebookJourney) -> None:
    staged = receipt.with_suffix(".tmp")
    _ = staged.write_text(journey.model_dump_json(indent=2), encoding="utf-8")
    staged.replace(receipt)


def current(
    runtime: ApplicationRuntime, topic: LessonTopic = "parameters"
) -> NotebookJourney | None:
    path = receipt_path(runtime, topic)
    if not path.exists():
        return None
    journey = NotebookJourney.model_validate_json(path.read_bytes())
    if journey.topic != topic:
        raise ValueError("课程记录与所选课程不符；请恢复原记录后继续")
    return journey


def status(
    runtime: ApplicationRuntime, topic: LessonTopic = "parameters"
) -> dict[str, object]:
    """Observe the cross-process preparation owner, including before allocation.

    A receipt describes completed work, not whether its writer is still alive.
    The OS releases the lock on failure or process exit, making retry available.
    """
    receipt = receipt_path(runtime, topic)
    if not receipt.parent.exists():
        return {"state": "not_started", "journey": None}
    try:
        with FileLock(receipt.with_suffix(".lock"), timeout=0):
            journey = current(runtime, topic)
            state = (
                "not_started"
                if journey is None
                else "ready"
                if journey.ready
                else "retryable"
            )
    except Timeout:
        journey = current(runtime, topic)
        state = "preparing"
    return {"state": state, "journey": journey.view() if journey else None}


def create_lesson_source(directory: Path, topic: LessonTopic = "parameters") -> None:
    """Publish one fresh, dependency-light folder; never overlay existing files."""
    _validate_topic(topic)
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise ValueError(f"代码目录已存在，未覆盖：{directory}")
    with TemporaryDirectory(prefix=f".{topic}-", dir=directory.parent) as temporary:
        staged = Path(temporary) / "source"
        (staged / "src/my_experiment").mkdir(parents=True)
        (staged / "src/my_experiment/__init__.py").touch()
        (staged / "notebooks").mkdir()
        (staged / "scopecat.toml").write_text(
            '[lab.capabilities]\nauthor_modules = ["my_experiment"]\n\n'
            '[authors]\nsource_roots = ["src"]\n'
            'refresh_roots = ["src/my_experiment"]\ndependencies = []\n',
            encoding="utf-8",
        )
        _ = install_lesson(staged, topic)
        (staged / "pyproject.toml").write_text(
            f'[project]\nname = "{topic}-lesson"\nversion = "0.1.0"\n'
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
    runtime: ApplicationRuntime,
    parent: str | None = None,
    topic: LessonTopic = "parameters",
    *,
    repair: bool = False,
) -> NotebookJourney:
    """Retry preparation without rewriting source; continue without dependency work."""
    receipt = receipt_path(runtime, topic)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(receipt.with_suffix(".lock"), timeout=30):
        journey = current(runtime, topic)
        if journey is None or journey.cleared:
            destination = Path(parent) if parent else runtime.home / "authors"
            if not destination.is_absolute() or (parent and not destination.is_dir()):
                raise ValueError("请选择已有保存目录的完整路径")
            journey = NotebookJourney(
                topic=topic,
                directory=destination / f"{topic}-{uuid4().hex[:12]}",
                registered_context=True,
            )
            create_lesson_source(journey.directory, topic)
            stat = journey.directory.stat()
            journey = journey.model_copy(
                update={
                    "directory_device": stat.st_dev,
                    "directory_inode": stat.st_ino,
                }
            )
            save_journey(receipt, journey)
        if not journey.notebook.is_file():
            raise ValueError(
                f"练习文件不可用，请恢复原目录后继续；不会重建或覆盖修改：{journey.directory}"
            )
        if repair:
            journey = journey.model_copy(
                update={"ready": False, "rebuild_client": True}
            )
            save_journey(receipt, journey)
        if journey.ready:
            _ = runtime.source(journey.directory)
            if not environment_python(journey.directory / ".venv").is_file():
                raise ValueError(
                    "本地 Python 缺失；在 Settings 重建此作者目录的本地环境后继续"
                )
            return journey
        if journey.rebuild_client:
            _ = create_client_environment(runtime, journey.directory, rebuild=True)
            journey = journey.model_copy(update={"rebuild_client": False})
            save_journey(receipt, journey)
        else:
            _ = create_client_environment(runtime, journey.directory)
        python = prepare_execution_environment(runtime, journey.directory)
        workspace_id = runtime.register_source(journey.directory, python=python)
        if journey.registered_context:
            scope = enroll_course(runtime, workspace_id, topic)
            if scope.state != "active":
                raise ValueError(
                    "此课程已进入清理，不能继续写入；请完成清理后准备新课程"
                )
            journey = journey.model_copy(
                update={
                    "scope_id": scope.id,
                    "workspace_id": scope.workspace_id,
                }
            )
        journey = journey.model_copy(update={"ready": True})
        save_journey(receipt, journey)
        return journey


def enroll_course(
    runtime: ApplicationRuntime, workspace_id: str, topic: LessonTopic
) -> PracticeScope:
    endpoint = runtime.status().record
    if endpoint is None:
        raise ValueError("应用服务不可用；课程文件已保留，请恢复服务后重试")
    response = httpx2.post(
        endpoint.base_url + "/api/v1/notebook-practice",
        json={"workspace_id": workspace_id, "lesson": topic},
        timeout=30,
        trust_env=False,
    )
    response.raise_for_status()
    return PracticeScope.model_validate_json(response.content)


def open_editor(journey: NotebookJourney) -> None:
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
    # The application's environment is not the course's selected interpreter.
    # Do not advertise its active venv to the editor's environment manager.
    editor_env = os.environ.copy()
    editor_env.pop("VIRTUAL_ENV", None)
    if sys.platform == "win32":
        # Code.exe owns a GUI lifetime; waiting for it would time out on first launch.
        _ = subprocess.Popen(  # noqa: S603 - native editor and argument vector
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=editor_env,
        )
        return
    result = subprocess.run(  # noqa: S603 - fixed editor executable and argument vector
        command,
        env=editor_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            "编辑器未打开；请手动打开下方 Notebook。" + result.stderr[-2048:]
        )
