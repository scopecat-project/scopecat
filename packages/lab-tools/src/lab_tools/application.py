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
from .services import Services


class Arguments(Protocol):
    project: Path | None
    python: Path
    name: str | None
    static_dir: Path | None
    home: Path
    source: Path | None
    no_browser: bool


def main(argv: list[str] | None = None) -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project",
        type=Path,
        nargs="?",
        help="Register an existing project; does not start its instruments",
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
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    try:
        if args.project is not None:
            service = Services(args.home.resolve()).register(
                args.project,
                args.python,
                name=args.name or args.project.resolve().name,
                static_dir=args.static_dir,
            )
            print(
                f"已登记实验服务: {service.name} ({service.id})\n环境: {service.python}"
            )
        client = ensure_host(args.home, args.source)
        if args.no_browser:
            print(client.state().model_dump_json(indent=2))
        else:
            webbrowser.open(f"{client.record.url}/#token={client.record.token}")
    except (OSError, ValueError, subprocess.SubprocessError, httpx2.HTTPError) as error:
        parser.exit(2, f"{error}\n登记失败时不会启动实验；已有记录保留。\n")


if __name__ == "__main__":
    main()
