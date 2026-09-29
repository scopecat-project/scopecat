"""Native application bootstrap, also shipped with a minimal Python installation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast

from .bundle import MANIFEST, file_hash, prepare_home
from .installation_paths import InstallationPaths


class Arguments(Protocol):
    payload: Path
    home: Path | None
    check_result: Path | None
    prepared: bool
    entry: Path | None


def _run(command: list[str]) -> None:
    _ = subprocess.run(  # noqa: S603 - explicit bundled interpreter and module
        command,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def launch(args: Arguments, paths: InstallationPaths) -> None:
    if not args.prepared:
        python, payload = prepare_home(args.payload, paths.software)
        _run(
            [
                str(python),
                "-I",
                "-m",
                "lab_tools.native_bootstrap",
                "--prepared",
                "--payload",
                str(payload),
                *(["--home", str(args.home)] if args.home else []),
                *(["--entry", str(args.entry)] if args.entry else []),
                *(
                    ["--check-result", str(args.check_result)]
                    if args.check_result
                    else []
                ),
            ]
        )
        return

    # These dependencies live only in the retained application runtime, not the
    # small bootstrap interpreter embedded in the relocatable native app.
    from filelock import FileLock

    from .application_runtime import ApplicationRuntime

    runtime = ApplicationRuntime(paths.state)
    with FileLock(paths.state / "native-start.lock"):
        _ = runtime.configure(
            static_dir=args.payload / "gui", software_home=paths.software
        )
        receipt = paths.state / "native-setup.json"
        identity = file_hash(args.payload / MANIFEST)
        seen = (
            cast("list[str]", json.loads(receipt.read_text()))
            if receipt.exists()
            else []
        )
        if not receipt.exists():
            initializer = args.payload / "initialize.py"
            if initializer.is_file():
                _run(
                    [
                        sys.executable,
                        "-I",
                        str(initializer),
                        "--state",
                        str(paths.state),
                        "--workspace",
                        str(paths.workspace),
                        *(["--entry", str(paths.entry)] if paths.entry else []),
                    ]
                )
        if identity not in seen:
            if runtime.installation().python != Path(sys.executable):
                _ = runtime.prepare_update(args.payload)
            # Reopening an older app must not propose a downgrade or overwrite a
            # newer candidate. Only a previously unseen payload prepares an update.
            pending = receipt.with_suffix(".pending")
            _ = pending.write_text(json.dumps([*seen, identity]), encoding="utf-8")
            _ = pending.replace(receipt)
        selected = runtime.installation()
    if args.check_result:
        _ = args.check_result.write_text(
            json.dumps(
                {
                    "python": str(selected.python),
                    "state": str(paths.state),
                    "software": str(paths.software),
                    "update_available": runtime.prepared_update() is not None,
                    "status": runtime.status().state,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return
    python = selected.python
    if os.name == "nt":
        python = python.with_name("pythonw.exe")
    _run(
        [
            str(python),
            "-I",
            "-m",
            "lab_tools.application",
            "--home",
            str(paths.state),
            "--action",
            "desktop",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--home", type=Path, help="Isolated installation root")
    parser.add_argument("--entry", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--check-result", type=Path, help="Prepare without opening UI")
    parser.add_argument("--prepared", action="store_true", help=argparse.SUPPRESS)
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
