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

from scopecat.trial_policy import (
    checked_path,
    current_policy,
    read_policy,
    validate_arguments,
)

from .application_runtime import ApplicationRuntime
from .bundle import MANIFEST, file_hash
from .installation_paths import InstallationPaths


class Arguments(Protocol):
    payload: Path
    home: Path | None
    check_result: Path | None
    entry: Path | None
    trial_window_probe: bool


def prepare(args: Arguments, paths: InstallationPaths) -> None:
    runtime = ApplicationRuntime(paths.state)
    with FileLock(paths.state / "native-start.lock"):
        from .data_reset import check_format

        check_format(runtime)
        payload = args.payload.resolve()
        python = Path(sys.executable)
        if os.name == "nt":
            python = python.with_name("python.exe")
        if not runtime.selection.exists():
            _ = runtime.configure(
                python=python, static_dir=payload / "gui", delivery_root=payload
            )
        # The package determines the interpreter, including when an update replaces
        # files at the same path. Never install or choose another environment here.
        candidate = runtime.qualify(python, payload / "gui", delivery_root=payload)
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
        prepare_reset=lambda: prepare(args, paths),
    )


def main(*, expected_trial_digest: str | None = None) -> None:
    os.environ["PYTHONUTF8"] = "1"
    policy = current_policy()
    if (policy is None) != (expected_trial_digest is None):
        raise ValueError("Trial bootstrap and runtime policy do not match")
    validate_arguments(sys.argv[1:], native=True)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=policy is None)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--home", type=Path, help="Isolated installation root")
    parser.add_argument("--entry", type=Path, help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-result", type=Path, help="Prepare without opening UI")
    mode.add_argument(
        "--trial-window-probe", action="store_true", help=argparse.SUPPRESS
    )
    args = cast("Arguments", cast("object", parser.parse_args()))
    if policy is not None:
        assert expected_trial_digest is not None
        if read_policy(policy.resources, expected_trial_digest) != policy:
            raise ValueError("Trial policy changed before startup")
        entry = (
            policy.resources.parents[1]
            if sys.platform == "darwin"
            else policy.resources.parent / "Scopecat.exe"
        )
        if (
            checked_path(args.payload) != policy.resources / "payload"
            or args.entry is None
            or checked_path(args.entry) != entry
        ):
            raise ValueError(
                "Trial payload or native entry does not match this package"
            )
        if args.check_result and not checked_path(args.check_result).is_relative_to(
            policy.home
        ):
            raise ValueError("Trial check output must stay inside its isolated home")
    if args.trial_window_probe:
        if policy is None:
            raise ValueError("The native window probe requires a marked trial package")
        if sys.platform not in ("darwin", "win32"):
            raise ValueError("The native window probe requires a supported native host")
        if sys.platform == "darwin":
            from .desktop_platform import macos_bundle_identifier

            if macos_bundle_identifier() != f"org.scopecat.trial.{policy.identity}":
                raise ValueError("Trial probe native bundle identity does not match")
        # Fixed packaged entry only. It owns its preflight, report and one-shot
        # lifecycle; do not prepare, start a regular desktop, or restart here.
        from .trial_window_probe import run as run_trial_window_probe

        run_trial_window_probe(policy.home)
        return
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
