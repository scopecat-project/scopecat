"""Prepare teaching exercises directly, without a separate management service."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Protocol, cast

from filelock import FileLock, Timeout

from lab_teaching.lessons import TOPICS

from .cleanup import cleanup_menu, in_use
from .notebook import project_python
from .sandboxes import run, sandbox_key, select_project


class Arguments(Protocol):
    topic: str | None
    home: Path
    source: Path | None
    reset: bool
    stop: bool
    verify: bool
    clean: bool
    status: bool


def execute(args: Arguments) -> None:
    home = args.home.resolve()
    key = sandbox_key(args.source)
    if args.clean:
        cleanup_menu(home, key)
        return
    if args.status or args.topic is None:
        print("用 VS Code 打开下列练习目录；关闭内核后可使用 --stop 停止练习。")
        for topic, title in TOPICS.items():
            pointer = home / "sandboxes" / key / topic / "current.json"
            if pointer.exists():
                root = select_project(
                    home, topic, source=args.source, existing_only=True
                )
                print(f"{topic}: {title}\n{root}")
            else:
                print(f"{topic}: {title}（尚未准备）")
        return
    if args.reset:
        pointer = home / "sandboxes" / key / args.topic / "current.json"
        if pointer.exists():
            previous = select_project(
                home, args.topic, source=args.source, existing_only=True
            )
            if in_use(previous):
                raise ValueError("重置前请关闭 Notebook 内核，并用 --stop 停止练习")
    root = select_project(
        home, args.topic, source=args.source, reset=args.reset, existing_only=args.stop
    )
    python = str(project_python(root))
    if args.stop:
        command = [python, "-m", "lab_tools.cli", "stop", str(root)]
    elif args.verify:
        command = [python, "-m", "lab_tools.verify_lesson", str(root), args.topic]
    else:
        command = [python, "-m", "lab_tools.cli", "start", str(root)]
    if args.source is not None and not args.stop:
        command.append("--api-only")
    run(command)
    print(f"练习目录：{root}\nNotebook：{root / 'notebooks' / (args.topic + '.ipynb')}")
    print("用 VS Code 打开练习目录，选择其中 .venv 的 Python / Notebook 内核。")


def main(argv: list[str] | None = None) -> None:
    from .bundle import configure_console

    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", choices=TOPICS, nargs="?")
    parser.add_argument("--home", type=Path, default=Path.home() / "Scopecat-Lab")
    parser.add_argument("--source", type=Path)
    for flag in ("reset", "stop", "verify", "clean", "status"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    try:
        args.home.mkdir(parents=True, exist_ok=True)
        with FileLock(args.home / "teaching.lock", timeout=0):
            execute(args)
    except Timeout:
        parser.exit(2, "另一个教学命令正在执行，请等待完成后重试。\n")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"{error}\n练习目录保留，修正后重试同一命令。\n")


if __name__ == "__main__":
    main()
