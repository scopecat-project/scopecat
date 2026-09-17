"""独立安装的教学项目入口;无需 Git checkout 或 campaigns。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Protocol, cast

from .project import check_project, create_project, notebook_command


class Arguments(Protocol):
    command: str
    topic: str | None
    project: Path
    api_only: bool
    bundle: Path | None


def dispatch(args: Arguments) -> None:
    if args.command == "create":
        print(create_project(args.project, topic=args.topic))
    elif args.command == "prepare":
        from .environment import prepare_project

        print(prepare_project(args.project, bundle=args.bundle))
    elif args.command == "check":
        print(check_project(args.project))
    elif args.command == "verify":
        from .verify import verify_project

        print(verify_project(args.project, static_dir=selected_gui(args)))
    elif args.command == "notebook":
        command, env = notebook_command(args.project)
        available = subprocess.run(  # noqa: S603 - explicit local tool and argument list
            [
                command[0],
                "-c",
                (
                    "import importlib.util; "
                    "raise SystemExit(importlib.util.find_spec('jupyterlab') is None)"
                ),
            ],
            env=env,
            check=False,
        )
        if available.returncode:
            raise ValueError(
                "当前交付未包含 JupyterLab; 请使用 VS Code, "
                "或由维护者提供 notebook extra 交付"
            )
        _ = subprocess.run(command, env=env, check=True)  # noqa: S603 - explicit local tool and argument list
    else:
        project = check_project(args.project)
        command = [
            sys.executable,
            "-m",
            "scopecat_server.cli",
            args.command,
            str(project),
        ]
        static_dir: Path | None = None
        if args.command == "start":
            if args.api_only:
                command.append("--api-only")
            static_dir = selected_gui(args)
            if static_dir is not None:
                command.extend(("--static-dir", str(static_dir)))
        _ = subprocess.run(command, check=True)  # noqa: S603 - explicit local tool and argument list
        if args.command == "start" and static_dir is not None:
            check_served_gui(project, static_dir)


def check_served_gui(project: Path, static_dir: Path) -> None:
    """A reused daemon may still be API-only; never restart an active experiment."""
    import httpx2 as httpx

    from scopecat.daemon.endpoint import read_daemon_endpoint_record

    record = read_daemon_endpoint_record(project)
    if record is None:
        raise ValueError("启动后未找到项目服务记录")
    try:
        response = httpx.get(record.base_url + "/", timeout=30, trust_env=False)
        matches = (
            response.status_code == 200
            and response.content == (static_dir / "index.html").read_bytes()
        )
    except httpx.HTTPError as error:
        raise ValueError(f"无法检查 GUI; 服务保持运行: {error}") from error
    if not matches:
        raise ValueError(
            "现有服务的 GUI 不匹配; 服务保持运行。完成操作后请 stop 再 start"
        )


def selected_gui(args: Arguments) -> Path | None:
    if args.api_only:
        return None
    from .bundle import gui_directory
    from .project import environment_identity

    return gui_directory(args.bundle, environment_identity())


def main() -> None:
    from .bundle import configure_console

    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--version", action="version", version=version("scopecat-lab-tools")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "create",
        "prepare",
        "check",
        "start",
        "stop",
        "open",
        "notebook",
        "verify",
    ):
        command = commands.add_parser(name)
        _ = command.add_argument("project", type=Path)
        if name == "create":
            from lab_teaching.lessons import TOPICS

            _ = command.add_argument("--topic", choices=TOPICS)
        if name == "prepare":
            _ = command.add_argument("--bundle", type=Path)
        if name in {"start", "verify"}:
            gui = command.add_mutually_exclusive_group()
            _ = gui.add_argument("--api-only", action="store_true")
            _ = gui.add_argument("--bundle", type=Path)
    args = cast("Arguments", cast("object", parser.parse_args()))
    try:
        dispatch(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
