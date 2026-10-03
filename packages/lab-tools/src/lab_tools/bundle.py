"""Delivery integrity and offline installation; standalone standard library only.

This file is also copied as install.py so a recipient needs only Python and uv.
Checksums detect mismatched artifacts, not the authenticity of their publisher.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import uuid
from pathlib import Path
from typing import Protocol, TypedDict, cast

MANIFEST = "bundle.json"
CURRENT_DELIVERY = "delivery-current.json"
RECEIPT = "scopecat-lab-delivery.json"


class Bundle(TypedDict):
    format: int
    target: dict[str, str]
    sources: dict[str, str]
    runtime: dict[str, object]
    files: dict[str, str]


def configure_console() -> None:
    """CLI output and subprocesses use UTF-8 even under redirected Windows output."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    os.environ["PYTHONUTF8"] = "1"


def target_identity() -> dict[str, str]:
    return {
        "platform": sys.platform,
        "machine": platform.machine().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "implementation": sys.implementation.name,
        "free_threaded": str(
            bool(cast("object", sysconfig.get_config_var("Py_GIL_DISABLED")))
        ),
    }


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root: Path, folders: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for folder in folders:
        for path in sorted((root / folder).rglob("*")):
            if path.is_symlink():
                raise ValueError(f"交付目录不能包含符号链接: {path}")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = file_hash(path)
    return result


def resolve_delivery(root: Path) -> Path:
    """Pin a build home's current artifact once; explicit bundles remain supported."""
    root = root.resolve()
    pointer = root / CURRENT_DELIVERY
    if not pointer.exists():
        return root
    document = cast("object", json.loads(pointer.read_text(encoding="utf-8")))
    if not isinstance(document, dict):
        raise ValueError("无法识别当前交付选择")
    selected = cast("dict[str, object]", document)
    identity = selected.get("build")
    digest = selected.get("manifest_sha256")
    if (
        selected.get("format") != 1
        or not isinstance(identity, str)
        or len(identity) != 32
        or any(character not in "0123456789abcdef" for character in identity)
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ValueError("无效的当前交付选择")
    artifact = managed_path(root, root / "builds" / identity)
    if file_hash(artifact / MANIFEST) != digest:
        raise ValueError("当前交付清单与构建选择不符；请保留产物并检查构建结果")
    return artifact


def read_bundle(root: Path) -> Bundle:
    raw = cast("object", json.loads((root / MANIFEST).read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise ValueError("无法识别交付清单版本")
    document = cast("dict[str, object]", raw)
    if document.get("format") != 1:
        raise ValueError("无法识别交付清单版本")
    bundle = cast("Bundle", cast("object", document))
    for key in ("target", "sources", "runtime", "files"):
        if not isinstance(bundle.get(key), dict):
            raise ValueError(f"交付清单缺少 {key}")
    for name, digest in cast("dict[str, object]", document["files"]).items():
        path = Path(name)
        if (
            not isinstance(digest, str)
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or len(digest) != 64
            or not (
                name.startswith(("gui/", "wheels/", "toolchain/"))
                or name
                in {
                    "requirements.lock",
                    "dependencies.lock",
                    "build.lock",
                    "install.py",
                }
            )
        ):
            raise ValueError(f"无效交付文件: {name}")
    return bundle


def verify_bundle(root: Path, *, gui_only: bool = False) -> Bundle:
    root = root.resolve()
    if (root / MANIFEST).is_symlink():
        raise ValueError("交付清单不能是符号链接")
    bundle = read_bundle(root)
    if bundle["target"] != target_identity():
        raise ValueError("交付包的操作系统、CPU 或 Python ABI 与当前环境不同")
    folders = ("gui",) if gui_only else ("gui", "wheels", "toolchain")
    actual = inventory(root, folders)
    expected = {
        name: value
        for name, value in bundle["files"].items()
        if name.startswith(tuple(folder + "/" for folder in folders))
    }
    if not gui_only:
        for name in (
            "requirements.lock",
            "dependencies.lock",
            "build.lock",
            "install.py",
        ):
            path = root / name
            if path.is_symlink():
                raise ValueError(f"交付文件不能是符号链接: {name}")
            actual[name] = file_hash(path)
            expected[name] = bundle["files"].get(name, "")
    if "gui/index.html" not in actual or actual != expected:
        raise ValueError("交付文件缺失、被修改或包含旧产物; 请恢复匹配的交付目录")
    return bundle


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


def gui_directory(bundle_root: Path | None, runtime: dict[str, object]) -> Path:
    """Validate just the small GUI payload at startup, not all dependency wheels."""
    if bundle_root is None:
        receipt = Path(sys.prefix) / RECEIPT
        if not receipt.is_file():
            raise ValueError("未安装 GUI 交付记录; 请用 install.py 或指定 --bundle")
        info = cast("dict[str, str]", json.loads(receipt.read_text(encoding="utf-8")))
        bundle_root = Path(info["bundle"])
        if file_hash(bundle_root / MANIFEST) != info["manifest_sha256"]:
            raise ValueError("交付清单与安装记录不同; 请保留原环境和原产物")
    bundle = verify_bundle(bundle_root, gui_only=True)
    if bundle["runtime"] != runtime:
        raise ValueError("GUI 交付包与当前运行时代码不匹配; 拒绝启动旧 GUI")
    return bundle_root.resolve() / "gui"


def managed_path(home: Path, path: Path) -> Path:
    """Managed destinations must not redirect writes outside this installation."""
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"安装目标不能是符号链接: {current}")
    return path


class InstallArguments(Protocol):
    destination: Path
    bundle: Path


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description="从本地交付目录离线安装最小教学环境")
    _ = parser.add_argument("destination", type=Path)
    _ = parser.add_argument("--bundle", type=Path, default=Path(__file__).parent)
    args = cast("InstallArguments", cast("object", parser.parse_args()))
    try:
        print(install_bundle(args.bundle, args.destination))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
