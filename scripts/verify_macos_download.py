"""Inspect the shipped DMG and quarantined app without bypassing Gatekeeper."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol, cast

from lab_tools.macos_signing import verify as verify_signature


def verify(installer: Path, home: Path, *, keep_work: bool = False) -> None:
    home.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(
        prefix="work-", dir=home, delete=not keep_work
    ) as directory:
        workspace = Path(directory) / "download"
        try:
            _verify(installer, workspace)
        finally:
            report = workspace / "report.json"
            if report.is_file():
                shutil.copyfile(report, home / "report.json")
            if keep_work:
                print(f"Retained download workspace: {directory}")


def _verify(installer: Path, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=False)
    mount = home / "mounted"
    mount.mkdir()
    _ = subprocess.run(  # noqa: S603 - local acceptance artifact, read-only mount
        [
            "/usr/bin/hdiutil",
            "attach",
            "-readonly",
            "-nobrowse",
            "-mountpoint",
            str(mount),
            str(installer.resolve()),
        ],
        check=True,
    )
    app = home / "Scopecat.app"
    try:
        shutil.copytree(mount / "Scopecat.app", app, symlinks=True)
    finally:
        _ = subprocess.run(  # noqa: S603 - detach only this acceptance mount
            ["/usr/bin/hdiutil", "detach", str(mount)], check=True
        )
    verify_signature(app)
    quarantine = f"0083;{int(time.time()):x};ScopecatAcceptance;"
    _ = subprocess.run(  # noqa: S603 - add download quarantine to disposable copy only
        ["/usr/bin/xattr", "-w", "com.apple.quarantine", quarantine, str(app)],
        check=True,
    )
    assessment = subprocess.run(  # noqa: S603 - read-only Gatekeeper assessment
        ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(app)],
        capture_output=True,
        text=True,
        check=False,
    )
    # Ad-hoc signatures do not establish developer trust. Preserve actual policy
    # output: CI machines may disable assessment or have prior local approvals.
    report = {
        "signature": "passed",
        "quarantine": subprocess.check_output(  # noqa: S603 - read test attribute
            ["/usr/bin/xattr", "-p", "com.apple.quarantine", str(app)], text=True
        ).strip(),
        "gatekeeper_returncode": assessment.returncode,
        "gatekeeper_output": assessment.stdout + assessment.stderr,
        "notarization": "not-provided",
        "finder_first_open": "not-evaluated",
    }
    script = app / "Contents/Resources/bootstrap.py"
    original = script.read_bytes()
    try:
        script.write_bytes(original + b"\n# signature integrity acceptance probe\n")
        tampered = subprocess.run(  # noqa: S603 - deliberate change to disposable copy
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert tampered.returncode != 0, "Modified application passed signature check"
    finally:
        script.write_bytes(original)
    verify_signature(app)
    report["tamper_detection"] = "passed"
    (home / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


class Arguments(Protocol):
    installer: Path
    reports: Path
    keep_work: bool


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args()))
    verify(args.installer, args.reports, keep_work=args.keep_work)
