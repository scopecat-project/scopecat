"""按版本管理可重建专题; 每个副本拥有自己的环境和合成数据。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast
from uuid import uuid4

from lab_teaching.lessons import TOPICS

from .bundle import MANIFEST, RECEIPT, file_hash
from .notebook import project_python
from .project import create_project


class EditorTask(TypedDict):
    label: str
    args: list[str]


class EditorTasks(TypedDict):
    tasks: list[EditorTask]


def run(
    command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> None:
    _ = subprocess.run(command, cwd=cwd, env=env, check=True)  # noqa: S603 - explicit local tool and argument list


def sandbox_key(source: Path | None) -> str:
    if source is None:
        receipt = cast(
            "dict[str, str]",
            json.loads((Path(sys.prefix) / RECEIPT).read_text(encoding="utf-8")),
        )
        return "release-" + file_hash(Path(receipt["bundle"]) / MANIFEST)[:16]
    identity = {
        # The host can run from an editable checkout; each exercise still installs
        # locked wheels. Identify source bytes, not the host's Python environment.
        "runtime": {
            path.relative_to(source).as_posix(): file_hash(path)
            for name in ("scopecat", "scopecat-server", "lab-tools", "lab-teaching")
            for path in sorted((source / "packages" / name / "src").rglob("*.py"))
            if "__pycache__" not in path.parts
        },
        "lock": file_hash(source / "uv.lock"),
        "lessons": {
            p.relative_to(source).as_posix(): file_hash(p)
            for p in sorted(
                (
                    source / "packages/lab-teaching/src/lab_teaching/course_material"
                ).rglob("*")
            )
            if p.is_file() and "__pycache__" not in p.parts
        },
        "tools": {
            path.name: file_hash(path)
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    return (
        "source-"
        + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    )


def select_project(
    home: Path,
    topic: str,
    *,
    source: Path | None = None,
    reset: bool = False,
    existing_only: bool = False,
) -> Path:
    if topic not in TOPICS:
        raise ValueError(f"未知专题: {topic}")
    location = home.resolve() / "sandboxes" / sandbox_key(source) / topic
    if any(p.is_symlink() for p in (location, location.parent, location.parent.parent)):
        raise ValueError("教学目录不能指向其他位置")
    location.mkdir(parents=True, exist_ok=True)
    current = location / "current.json"
    if current.exists():
        generation = cast(
            "dict[str, object]", json.loads(current.read_text(encoding="utf-8"))
        ).get("generation")
        if (
            not isinstance(generation, str)
            or len(generation) != 32
            or any(c not in "0123456789abcdef" for c in generation)
        ):
            raise ValueError("沙盒记录损坏; 请保留目录并检查 current.json")
        previous = location / generation
        if previous.is_symlink():
            raise ValueError("沙盒不能指向外部目录")
        if not reset:
            return previous
        run(
            [
                str(project_python(previous)),
                "-m",
                "lab_tools.cli",
                "stop",
                str(previous),
            ]
        )
    if existing_only:
        raise ValueError("此版本尚未打开该专题, 没有需要停止的服务")
    root = location / uuid4().hex
    _ = create_project(root, topic=topic)
    task_file = root / ".vscode/tasks.json"
    tasks = cast("EditorTasks", json.loads(task_file.read_text(encoding="utf-8")))
    tasks["tasks"] = [
        task for task in tasks["tasks"] if task["label"] != "首次准备项目环境"
    ]
    if source is not None:
        for task in tasks["tasks"]:
            if "start" in task["args"]:
                task["args"].append("--api-only")
    _ = task_file.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if source is None:
        from .environment import prepare_project

        _ = prepare_project(root)
    else:
        uv = shutil.which("uv")
        if uv is None:
            raise ValueError("源码沙盒需要 uv")
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(root / ".venv"))
        env.pop("VIRTUAL_ENV", None)
        run(
            [
                uv,
                "sync",
                "--locked",
                "--only-group",
                "delivery",
                "--no-editable",
                "--inexact",
                "--reinstall-package",
                "scopecat-lab-tools",
                "--reinstall-package",
                "scopecat",
                "--reinstall-package",
                "scopecat-server",
                "--reinstall-package",
                "scopecat-lab-teaching",
            ],
            cwd=source,
            env=env,
        )
        run(
            [
                uv,
                "pip",
                "install",
                "--no-deps",
                "--no-build-isolation",
                "--python",
                str(project_python(root)),
                "--editable",
                str(root),
            ],
            cwd=source,
        )
    # Publish only after successful installation. Failed copies never become current.
    temporary = current.with_suffix(".tmp")
    _ = temporary.write_text(
        json.dumps({"generation": root.name}) + "\n", encoding="utf-8"
    )
    _ = temporary.replace(current)
    return root
