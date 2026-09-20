"""Trusted local registration and entry for existing experiment deployments."""

from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Protocol, cast

import httpx2

from .bundle import configure_console
from .host_client import ensure_host
from .host_models import Command
from .services import Services


class Arguments(Protocol):
    project: Path | None
    python: Path
    name: str | None
    static_dir: Path | None
    home: Path
    source: Path | None
    no_browser: bool
    manage: bool


def main(argv: list[str] | None = None) -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project",
        type=Path,
        nargs="?",
        help="Open an existing workbench; startup may initialize instruments",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Project's Python interpreter (venv path)",
    )
    parser.add_argument("--name")
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument("--home", type=Path, default=Path.home() / "Scopecat-Lab")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--manage",
        action="store_true",
        help="Open maintenance without starting a service",
    )
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    manager_url: str | None = None
    try:
        store = Services(args.home.resolve())
        selected = None
        if args.project is not None:
            service = store.register(
                args.project,
                args.python,
                name=args.name or args.project.resolve().name,
                static_dir=args.static_dir,
            )
            print(
                f"已登记实验服务: {service.name} ({service.id})\n环境: {service.python}"
            )
            selected = service
        client = ensure_host(args.home, args.source)
        if args.no_browser:
            print(client.state().model_dump_json(indent=2))
            return
        manager_url = f"{client.record.url}/#token={client.record.token}"
        selected = selected or store.preferred()
        if args.manage or selected is None:
            webbrowser.open(manager_url)
            return
        operation = client.submit(Command(action="service_start", service=selected.id))
        print(
            f"正在启动 / 检查工作台: {selected.name}；操作编号: {operation.command.id}"
        )
        client.wait(operation)
        view = next(
            (item for item in client.state().services if item.service == selected), None
        )
        if view is None or view.state != "running" or view.url is None:
            raise ValueError("启动后的服务状态或登记已改变；请在管理页面检查")
        store.remember(selected.id)
        webbrowser.open(view.url)
    except (OSError, ValueError, subprocess.SubprocessError, httpx2.HTTPError) as error:
        if manager_url is not None:
            webbrowser.open(manager_url)
        parser.exit(2, f"{error}\n未打开实验工作台；已有登记、数据和操作日志保留。\n")


if __name__ == "__main__":
    main()
