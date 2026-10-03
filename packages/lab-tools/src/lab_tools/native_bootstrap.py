"""Installed-package host using its build-time Python and dependencies directly."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast

from filelock import FileLock

from .application_runtime import ApplicationRuntime
from .bundle import MANIFEST, file_hash
from .installation_paths import InstallationPaths


class Arguments(Protocol):
    payload: Path
    home: Path | None
    check_result: Path | None
    entry: Path | None


def prepare(args: Arguments, paths: InstallationPaths) -> None:
    runtime = ApplicationRuntime(paths.state)
    with FileLock(paths.state / "native-start.lock"):
        payload = args.payload.resolve()
        python = Path(sys.executable)
        if os.name == "nt":
            python = python.with_name("python.exe")
        if not runtime.selection.exists():
            _ = runtime.configure(python=python, static_dir=payload / "gui")
        # The package determines the interpreter, including when an update replaces
        # files at the same path. Never install or choose another environment here.
        candidate = runtime.qualify(python, payload / "gui")
        # This receipt is derived from the current package, not a source of
        # software selection. Do not require the previous package's receipt
        # format or interpreter to be usable before registering the current one.
        registered = runtime.selection.read_text(encoding="utf-8")
        if (
            registered != candidate.model_dump_json(indent=2)
            or runtime.pending.exists()
        ):
            if runtime.status().state not in ("stopped", "stale"):
                raise ValueError(
                    "当前旧应用仍在运行。请完成工作后退出旧应用，"
                    "再点重试；或选择停止后台并完成更新。"
                )
            runtime.select(candidate)


def launch(args: Arguments, paths: InstallationPaths) -> None:
    if args.check_result:
        prepare(args, paths)
        runtime = ApplicationRuntime(paths.state)
        selected = runtime.installation()
        bundle_identifier = None
        if sys.platform == "darwin":
            from .desktop_platform import macos_bundle_identifier

            bundle_identifier = macos_bundle_identifier()
        _ = args.check_result.write_text(
            json.dumps(
                {
                    "python": str(selected.python),
                    "state": str(paths.state),
                    "status": runtime.status().state,
                    "bundle_identifier": bundle_identifier,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return
    from .desktop import run

    run(
        paths.state,
        prepare=lambda: prepare(args, paths),
        package_identity=file_hash(args.payload / MANIFEST),
    )


def main() -> None:
    os.environ["PYTHONUTF8"] = "1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--home", type=Path, help="Isolated installation root")
    parser.add_argument("--entry", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--check-result", type=Path, help="Prepare without opening UI")
    args = cast("Arguments", cast("object", parser.parse_args()))
    paths = (
        InstallationPaths.isolated(args.home)
        if args.home
        else InstallationPaths.current_user()
    )
    if args.entry:
        paths = replace(paths, entry=args.entry)
    paths.state.mkdir(parents=True, exist_ok=True)
    with (paths.state / "native-start.log").open(
        "a", encoding="utf-8", buffering=1
    ) as log:
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        try:
            launch(args, paths)
        except Exception:
            traceback.print_exc()
            raise SystemExit(1) from None
        finally:
            sys.stdout, sys.stderr = stdout, stderr


if __name__ == "__main__":
    main()
