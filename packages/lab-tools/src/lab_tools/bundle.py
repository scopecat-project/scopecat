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
from pathlib import Path
from typing import Protocol, TypedDict, cast

MANIFEST = "bundle.json"
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
                name.startswith(("gui/", "wheels/"))
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
    bundle = read_bundle(root)
    if bundle["target"] != target_identity():
        raise ValueError("交付包的操作系统、CPU 或 Python ABI 与当前环境不同")
    folders = ("gui",) if gui_only else ("gui", "wheels")
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


def install_bundle(root: Path, destination: Path) -> Path:
    root = root.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"环境目录已存在: {destination}; 请使用新目录")
    _ = verify_bundle(root)
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("离线安装前请准备 uv 和匹配的 Python 解释器")
    _ = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [uv, "venv", "--offline", "--python", sys.executable, str(destination)],
        check=True,
    )
    python = destination / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    _ = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-index",
            "--require-hashes",
            "--find-links",
            str(root / "wheels"),
            "--python",
            str(python),
            "-r",
            str(root / "requirements.lock"),
        ],
        check=True,
    )
    _ = (destination / RECEIPT).write_text(
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


def install_home(root: Path, home: Path) -> Path:
    """复制固定产物后安装, 转移介质拔出后仍可打开和重置沙盒。"""
    root = root.resolve()
    home = home.resolve()
    if home.is_relative_to(root):
        raise ValueError("安装中心不能位于待复制的交付目录内")
    _ = verify_bundle(root)
    key = file_hash(root / MANIFEST)[:16]
    release = home / "releases" / key
    bundle = release / "bundle"
    if not bundle.exists():
        release.mkdir(parents=True, exist_ok=True)
        _ = shutil.copytree(root, bundle)
    _ = verify_bundle(bundle)
    environment = release / "runtime"
    if not environment.exists():
        _ = install_bundle(bundle, environment)
    elif not (environment / RECEIPT).is_file():
        raise ValueError(f"上次安装未完成; 删除此不完整运行环境后重试: {environment}")
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    # Validate the installed entry before changing the user's default release.
    _ = subprocess.run([str(python), "-m", "lab_tools.sandbox", "--help"], check=True)  # noqa: S603 - explicit local tool and argument list
    launcher = home / "lab.py"
    _ = launcher.write_text(
        "import subprocess, sys\nfrom pathlib import Path\n"
        "home = Path(__file__).resolve().parent\n"
        f"python = home / {python.relative_to(home).as_posix()!r}\n"
        "command = [str(python), '-m', 'lab_tools.sandbox', '--home', str(home)]\n"
        "raise SystemExit(subprocess.call([*command, *sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    _ = (home / "lab.cmd").write_text(
        '@echo off\ncd /d "%~dp0"\n'
        f'"%~dp0{python.relative_to(home)}" "%~dp0lab.py" %*\n'
        "if errorlevel 1 pause\n",
        encoding="utf-8",
    )
    print(
        f"已安装固定版本 {key}。Windows 双击 {home / 'lab.cmd'}; "
        f"其他系统运行 python {launcher}。"
    )
    return launcher


class InstallArguments(Protocol):
    destination: Path | None
    home: Path | None
    bundle: Path


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description="从本地交付目录离线安装最小教学环境")
    _ = parser.add_argument("destination", type=Path, nargs="?")
    _ = parser.add_argument("--home", type=Path)
    _ = parser.add_argument("--bundle", type=Path, default=Path(__file__).parent)
    args = cast("InstallArguments", cast("object", parser.parse_args()))
    try:
        if args.home is not None:
            if args.destination is not None:
                parser.error("destination 和 --home 只能选择一个")
            print(install_home(Path(args.bundle), Path(args.home)))
        elif args.destination is not None:
            print(install_bundle(Path(args.bundle), Path(args.destination)))
        else:
            print(install_home(Path(args.bundle), Path.home() / "Scopecat-Lab"))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
