"""Install a verified delivery's runtime into a fresh application Python directory."""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

from scopecat_app.bundle import verify_bundle


def install_runtime(payload: Path, python_home: Path) -> Path:
    """Verify internal build materials before creating the application runtime."""
    verify_bundle(payload)
    python_home.mkdir(parents=True, exist_ok=False)
    with tarfile.open(payload / "toolchain/python.tar") as archive:
        archive.extractall(python_home, filter="data")
    python = python_home / ("python.exe" if os.name == "nt" else "bin/python3")
    subprocess.run(  # noqa: S603 - verified local tool and isolated application runtime
        [
            str(payload / "toolchain" / ("uv.exe" if os.name == "nt" else "uv")),
            "pip",
            "install",
            "--python",
            str(python),
            "--break-system-packages",
            "--offline",
            "--no-index",
            "--no-deps",
            "--require-hashes",
            "--find-links",
            str(payload / "wheels"),
            "-r",
            str(payload / "requirements.lock"),
        ],
        check=True,
    )
    compile_bytecode(python, python_home)
    return python


def compile_bytecode(python: Path, python_home: Path) -> None:
    """Compile shipped sources with relocatable names and hash-based validity."""
    subprocess.run(  # noqa: S603 - selected packaged interpreter and owned sources
        [
            str(python),
            "-I",
            "-B",
            "-m",
            "compileall",
            "-q",
            "-f",
            "--invalidation-mode",
            "checked-hash",
            "-s",
            str(python_home),
            "-p",
            "scopecat-runtime",
            str(python_home),
        ],
        check=True,
    )
