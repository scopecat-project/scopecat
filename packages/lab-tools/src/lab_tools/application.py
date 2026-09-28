"""Trusted local registration and entry for existing experiment deployments."""

from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlencode

import httpx2

from .bundle import configure_console
from .host_client import ensure_host, existing_host
from .services import Service, Services


def registered(store: Services, project: Path) -> Service:
    root = project.resolve()
    if root.name == "scopecat.toml":
        root = root.parent
    selected = next((item for item in store.list() if Path(item.root) == root), None)
    if selected is None:
        raise ValueError("未找到此项目的登记；没有停止其他服务")
    return selected


class Arguments(Protocol):
    action: str
    project: Path | None
    workspace: Path | None
    python: Path | None
    name: str | None
    static_dir: Path | None
    home: Path
    source: Path | None
    no_browser: bool
    manage: bool
    stop_started: bool
    keep_background: bool


def main(argv: list[str] | None = None) -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=("status", "start", "stop", "open", "desktop", "quit"),
        default="status",
        help="Status is read-only; only open explicitly launches a browser",
    )
    location = parser.add_mutually_exclusive_group()
    location.add_argument(
        "project",
        type=Path,
        nargs="?",
        help="Open an existing workbench; startup may initialize instruments",
    )
    location.add_argument(
        "--workspace",
        type=Path,
        help="Open already bound author code in its registered laboratory",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="Project's Python interpreter (venv path)",
    )
    parser.add_argument("--name")
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument(
        "--home",
        type=Path,
        required=True,
        help="Explicit installation or isolated development home",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    exit_mode = parser.add_mutually_exclusive_group()
    exit_mode.add_argument("--stop-started", action="store_true")
    exit_mode.add_argument("--keep-background", action="store_true")
    parser.add_argument(
        "--manage",
        action="store_true",
        help="Open maintenance without starting a service",
    )
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    if args.action in ("status", "desktop", "quit") and (
        args.project or args.workspace
    ):
        parser.error("项目或作者目录只用于 start、stop 或 open")
    if args.workspace is not None and any(
        value is not None for value in (args.python, args.name, args.static_dir)
    ):
        parser.error(
            "--workspace 使用实验室已登记环境，"
            "不能同时指定 --python、--name 或 --static-dir"
        )
    workspace_id: str | None = None
    try:
        store = Services(args.home.resolve())
        if args.action == "status":
            import json

            host = existing_host(args.home.resolve())
            print(
                json.dumps(
                    {
                        "host": host.record.model_dump(exclude={"token"})
                        if host
                        else None,
                        "services": [v.model_dump() for v in store.views()],
                    },
                    ensure_ascii=False,
                )
            )
            return
        if args.action == "desktop":
            from .desktop import run

            run(args.home.resolve(), args.source)
            return
        if args.action == "quit":
            client = existing_host(args.home.resolve())
            if client is not None:
                if not (args.stop_started or args.keep_background):
                    raise ValueError("退出需指定 --stop-started 或 --keep-background")
                client.exit(stop_started=args.stop_started)
            return
        selected = None
        if args.workspace is not None:
            selected, workspace_id = store.for_workspace(args.workspace)
        elif args.project is not None:
            if args.action == "stop":
                service = registered(store, args.project)
            else:
                service = store.register(
                    args.project,
                    args.python or Path(sys.executable),
                    name=args.name or args.project.resolve().name,
                    static_dir=args.static_dir,
                )
                print(
                    f"已登记实验服务: {service.name} ({service.id})\n"
                    f"环境: {service.python}"
                )
            selected = service
        selected = selected or store.preferred()
        if args.action in ("start", "stop"):
            if selected is None:
                raise ValueError("请明确选择已登记的实验室")
            if args.action == "start":
                store.start(selected.id)
                store.remember(selected.id)
            else:
                store.stop(selected.id)
            print(
                next(
                    view for view in store.views() if view.service.id == selected.id
                ).model_dump_json()
            )
            return
        client = ensure_host(args.home, args.source)
        fragment = {"token": client.record.token}
        if selected is not None:
            fragment["service"] = selected.id
        if workspace_id is not None:
            fragment["workspace"] = workspace_id
        if args.manage:
            fragment["view"] = "settings"
        url = f"{client.record.url}/#{urlencode(fragment)}"
        if not args.no_browser:
            webbrowser.open(url)
        else:
            print(client.state().model_dump_json(indent=2))
    except (OSError, ValueError, subprocess.SubprocessError, httpx2.HTTPError) as error:
        parser.exit(2, f"{error}\n已有登记、数据和操作日志保留；没有自动打开浏览器。\n")


if __name__ == "__main__":
    main()
