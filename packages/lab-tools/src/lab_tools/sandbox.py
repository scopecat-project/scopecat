"""按版本管理可重建专题; 每个副本拥有自己的环境和合成数据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, TypedDict, cast
from uuid import uuid4

from lab_teaching.lessons import TOPICS

from .bundle import MANIFEST, RECEIPT, file_hash
from .notebook import project_python
from .project import create_project, environment_identity


class Arguments(Protocol):
    topic: str | None
    home: Path
    source: Path | None
    reset: bool
    stop: bool
    no_editor: bool
    verify: bool


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
        "runtime": environment_identity(),
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


def open_sandbox(root: Path, topic: str, *, source: bool, no_editor: bool) -> None:
    python = str(project_python(root))
    command = [python, "-m", "lab_tools.cli", "start", str(root)]
    if source:
        command.append("--api-only")
    run(command)
    notebook = root / "notebooks" / f"{topic}.ipynb"
    print(
        f"专题: {TOPICS[topic]}\n项目: {root}\nNotebook: {notebook}\n内核: {python}",
        flush=True,
    )
    if not no_editor:
        code = shutil.which("code")
        if code:
            run([code, str(root), str(notebook)])
        else:
            print("未找到 VS Code 命令入口; 请在编辑器中打开上面的项目和 Notebook。")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("topic", choices=TOPICS, nargs="?")
    _ = parser.add_argument("--home", type=Path, default=Path.home() / "Scopecat-Lab")
    _ = parser.add_argument("--source", type=Path)
    _ = parser.add_argument("--reset", action="store_true")
    _ = parser.add_argument("--stop", action="store_true")
    _ = parser.add_argument("--no-editor", action="store_true")
    _ = parser.add_argument("--verify", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    if args.topic is None:
        for number, (name, title) in enumerate(TOPICS.items(), 1):
            print(f"{number}. {title} ({name})")
        answer = input("选择专题编号 (直接回车退出): ").strip()
        if not answer:
            return
        if answer not in {str(i) for i in range(1, len(TOPICS) + 1)}:
            parser.exit(2, "无效专题编号\n")
        args.topic = list(TOPICS)[int(answer) - 1]
        action = input(
            "1 打开/继续 (回车默认), 2 重置, 3 停止服务, 4 自动验收: "
        ).strip()
        if action not in {"", "1", "2", "3", "4"}:
            parser.exit(2, "无效操作编号\n")
        args.reset = args.reset or action == "2"
        args.stop = args.stop or action == "3"
        args.verify = args.verify or action == "4"
    try:
        root = select_project(
            args.home,
            args.topic,
            source=args.source,
            reset=args.reset,
            existing_only=args.stop,
        )
        if args.stop:
            run([str(project_python(root)), "-m", "lab_tools.cli", "stop", str(root)])
        elif args.verify:
            command = [
                str(project_python(root)),
                "-m",
                "lab_tools.verify_lesson",
                str(root),
                args.topic,
            ]
            if args.source:
                command.append("--api-only")
            run(command)
        else:
            open_sandbox(
                root,
                args.topic,
                source=args.source is not None,
                no_editor=args.no_editor,
            )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"沙盒操作失败: {error}\n目录和日志保留, 可修复后重试。\n")


if __name__ == "__main__":
    main()
