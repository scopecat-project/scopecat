"""Repository offline installer, also assembled as a standalone install.py."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Protocol, cast

from scopecat_app.bundle import (
    MANIFEST,
    RECEIPT,
    configure_console,
    file_hash,
    resolve_delivery,
    verify_bundle,
)


def _run_install(command: list[str]) -> None:
    _ = subprocess.run(  # noqa: S603 - fixed installer command
        command,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def install_bundle(
    root: Path,
    destination: Path,
    *,
    copy_packages: bool = False,
    base_python: Path | None = None,
    packages: tuple[str, ...] | None = None,
) -> Path:
    root = resolve_delivery(root)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"环境目录已存在: {destination}; 请使用新目录")
    _ = verify_bundle(root)
    toolchain = root / "toolchain"
    uv_name = "uv.exe" if os.name == "nt" else "uv"
    installed_uv = (
        Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin") / uv_name
    )
    uv = (
        str(toolchain / uv_name)
        if toolchain.is_dir()
        else str(installed_uv)
        if installed_uv.is_file()
        else shutil.which("uv")
    )
    if uv is None:
        raise ValueError("离线安装前请准备 uv 和匹配的 Python 解释器")
    _run_install(
        [
            uv,
            "venv",
            "--offline",
            "--python",
            str(base_python) if base_python else sys.executable,
            str(destination),
        ]
    )
    python = destination / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    with tempfile.TemporaryDirectory(prefix="scopecat-install-") as temporary:
        requirements = root / "requirements.lock"
        if packages is not None:
            requested = Path(temporary) / "requirements.in"
            requested.write_text("\n".join(packages) + "\n", encoding="utf-8")
            requirements = Path(temporary) / "requirements.lock"
            _run_install(
                [
                    uv,
                    "pip",
                    "compile",
                    str(requested),
                    "--offline",
                    "--no-index",
                    "--find-links",
                    (root / "wheels").as_uri(),
                    "--constraint",
                    (root / "requirements.lock").as_uri(),
                    "--python",
                    str(python),
                    "--generate-hashes",
                    "--no-header",
                    "--no-annotate",
                    "--output-file",
                    str(requirements),
                ]
            )
        _run_install(
            [
                uv,
                "pip",
                "install",
                *(["--link-mode", "copy"] if copy_packages else []),
                "--offline",
                "--no-index",
                "--require-hashes",
                "--find-links",
                str(root / "wheels"),
                "--python",
                str(python),
                "-r",
                str(requirements),
            ]
        )
    staged_receipt = destination / f".{RECEIPT}-{uuid.uuid4().hex}"
    _ = staged_receipt.write_text(
        json.dumps(
            {
                "bundle": str(root),
                "manifest_sha256": file_hash(root / MANIFEST),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _ = staged_receipt.replace(destination / RECEIPT)
    return destination


class InstallArguments(Protocol):
    destination: Path
    bundle: Path


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description="从本地交付目录离线安装应用环境")
    _ = parser.add_argument("destination", type=Path)
    _ = parser.add_argument("--bundle", type=Path, default=Path(__file__).parent)
    args = cast("InstallArguments", cast("object", parser.parse_args()))
    try:
        print(install_bundle(args.bundle, args.destination))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
