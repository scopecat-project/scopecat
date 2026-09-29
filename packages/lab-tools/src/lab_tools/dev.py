"""Run a foreground development application without installing a desktop entry."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import psutil
from filelock import FileLock

from scopecat.project import open_project
from scopecat_server.author_registration import register_author_workspace
from scopecat_server.lifecycle import inspect_daemon, start_project, stop_project
from scopecat_server.scaffold import write_author_scaffold
from scopecat_server.validation_process import terminate_validation_process_tree

if TYPE_CHECKING:
    from scopecat.daemon.endpoint import DaemonEndpointRecord


class Arguments(Protocol):
    home: Path
    source: Path | None
    preview: Path | None
    workspace: Path | None
    composition: Path | None


@contextmanager
def development_session(
    home: Path,
    *,
    workspace: Path | None = None,
    composition: Path | None = None,
    static_dir: Path | None = None,
) -> Generator[DaemonEndpointRecord]:
    import tomlkit

    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    with FileLock(home / "development.lock", timeout=0):
        marker = home / "development.json"
        root = home / "runtime"
        if root.exists() and not marker.exists():
            raise ValueError("This directory is not owned by the development launcher")
        root.mkdir(exist_ok=True)
        marker.write_text(json.dumps({"kind": "scopecat-development"}))
        manifest = root / "scopecat.toml"
        if manifest.exists() and inspect_daemon(open_project(root)).state != "stopped":
            raise ValueError(
                "Stop the existing development application before restarting"
            )
        document = tomlkit.document()
        document["lab"] = (
            tomlkit.parse(composition.read_text(encoding="utf-8"))["lab"]
            if composition
            else tomlkit.table()
        )
        document["authors"] = {"dependencies": []}
        manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
        source = workspace.resolve() if workspace else home / "authors"
        if workspace is None and not source.exists():
            write_author_scaffold(source)
        register_author_workspace(root, source)
        project = open_project(root)
        try:
            record = start_project(project, static_dir=static_dir)
            yield record
        finally:
            stop_project(project)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.cwd() / ".scopecat-dev")
    frontend = parser.add_mutually_exclusive_group()
    frontend.add_argument("--source", type=Path, help="Public checkout for Vite HMR")
    frontend.add_argument("--preview", type=Path, help="Fixed public preview pin")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--composition", type=Path)
    args = cast("Arguments", cast("object", parser.parse_args()))
    gui = None
    if args.preview:
        from .preview import fetch_preview, unpack_gui

        preview = fetch_preview(args.preview, args.home / "cache")
        gui = args.home / "gui"
        unpack_gui(preview, gui)

    def terminate(_signal: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    child: subprocess.Popen[str] | None = None
    owner: psutil.Process | None = None
    try:
        with development_session(
            args.home,
            workspace=args.workspace,
            composition=args.composition,
            static_dir=gui,
        ) as record:
            print(
                f"Development backend: {record.base_url}\n"
                "Ctrl-C stops this application.",
                flush=True,
            )
            try:
                if args.source:
                    pnpm = shutil.which("pnpm")
                    if pnpm is None:
                        raise ValueError("Source UI development requires pnpm")
                    ui = args.source.resolve() / "apps/scopecat-ui"
                    subprocess.run(  # noqa: S603 - selected developer checkout
                        [pnpm, "install", "--frozen-lockfile"], cwd=ui, check=True
                    )
                    child = subprocess.Popen(  # noqa: S603 - fixed developer command
                        [pnpm, "run", "dev"],
                        cwd=ui,
                        env=dict(os.environ, SCOPECAT_DEV_ENDPOINT=record.base_url),
                        text=True,
                    )
                    owner = psutil.Process(child.pid)
                while child is None or child.poll() is None:
                    time.sleep(0.2)
            finally:
                if child is not None:
                    terminate_validation_process_tree(child, owner=owner)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
