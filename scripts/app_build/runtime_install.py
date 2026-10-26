"""Install a verified delivery's runtime into a fresh application Python directory."""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path


def install_runtime(payload: Path, python_home: Path) -> Path:
    """Callers verify the delivery and own layout, platform checks and publication."""
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
    return python
