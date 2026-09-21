"""Delivery integrity and offline installation; standalone standard library only.

This file is also copied as install.py so a recipient needs only Python and uv.
Checksums detect mismatched artifacts, not the authenticity of their publisher.
"""

from __future__ import annotations

import argparse
import errno
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
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol, TypedDict, cast

MANIFEST = "bundle.json"
RECEIPT = "scopecat-lab-delivery.json"
OWNERSHIP = ".scopecat-environment-owner"


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
    if (root / MANIFEST).is_symlink():
        raise ValueError("交付清单不能是符号链接")
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


def _run_install(
    command: list[str],
    on_process: Callable[[int | Literal["not-started"] | None], None] | None,
) -> None:
    if on_process is None:
        _ = subprocess.run(command, check=True)  # noqa: S603 - fixed installer command
        return
    # Persist the launch intent before spawning: an interrupted unrecorded launch
    # must never be mistaken for proof that no installer is still writing.
    on_process(None)
    try:
        process = subprocess.Popen(command)  # noqa: S603 - fixed installer command
    except OSError:
        # Popen did not return a child: unlike an interrupted launch, this is
        # positive evidence that this command has no process still writing.
        on_process("not-started")
        raise
    with process:
        on_process(process.pid)
        if process.wait() != 0:
            raise subprocess.CalledProcessError(process.returncode, command)


def install_bundle(
    root: Path,
    destination: Path,
    *,
    ownership_token: str | None = None,
    on_process: Callable[[int | Literal["not-started"] | None], None] | None = None,
) -> Path:
    root = root.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"环境目录已存在: {destination}; 请使用新目录")
    _ = verify_bundle(root)
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("离线安装前请准备 uv 和匹配的 Python 解释器")
    if ownership_token is not None:
        destination.mkdir()
        (destination / OWNERSHIP).write_text(ownership_token, encoding="utf-8")
    _run_install(
        [
            uv,
            "venv",
            "--offline",
            *(["--allow-existing"] if ownership_token else []),
            "--python",
            sys.executable,
            str(destination),
        ],
        on_process,
    )
    python = destination / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    _run_install(
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
        on_process,
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


@contextmanager
def _installation_lock(home: Path) -> Generator[None]:
    # OS locks are released on process exit, including interrupted installation.
    path = managed_path(home, home / ".install.lock")
    with path.open("a+b") as stream:
        if sys.platform == "win32":
            import msvcrt

            if path.stat().st_size == 0:
                _ = stream.write(b"0")
                stream.flush()
            stream.seek(0)
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    time.sleep(0.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def check_receipt(environment: Path, bundle: Path) -> None:
    """Require the exact retained delivery recorded by a completed install."""
    expected = {
        "bundle": str(bundle),
        "manifest_sha256": file_hash(bundle / MANIFEST),
    }
    actual = cast(
        "object", json.loads((environment / RECEIPT).read_text(encoding="utf-8"))
    )
    if actual != expected:
        raise ValueError(f"运行环境的交付记录与当前产物不同: {environment}")


def install_home(root: Path, home: Path) -> Path:
    """Prepare a retained release, then atomically select it for the next launch."""
    root = root.resolve()
    home = home.resolve()
    if home.is_relative_to(root):
        raise ValueError("安装中心不能位于待复制的交付目录内")
    _ = verify_bundle(root)
    home.mkdir(parents=True, exist_ok=True)
    with _installation_lock(home):
        return _install_home_locked(root, home)


def retain_bundle(root: Path, home: Path) -> Path:
    """Retain and verify delivery files without selecting an application runtime."""
    root = root.resolve()
    home = home.resolve()
    if home.is_relative_to(root):
        raise ValueError("安装中心不能位于待复制的交付目录内")
    _ = verify_bundle(root)
    home.mkdir(parents=True, exist_ok=True)
    with _installation_lock(home):
        return _retain_bundle_locked(root, home)


def _retain_bundle_locked(root: Path, home: Path) -> Path:
    manifest_hash = file_hash(root / MANIFEST)
    key = manifest_hash[:16]
    release = managed_path(home, home / "releases" / key)
    release.mkdir(parents=True, exist_ok=True)
    bundle = managed_path(home, release / "bundle")
    if not bundle.exists():
        staged = release / f"bundle-staging-{uuid.uuid4().hex}"
        # Retain interrupted copies for diagnosis; retries use a new staging path.
        _ = shutil.copytree(root, staged, symlinks=True)
        _ = verify_bundle(staged)
        if file_hash(staged / MANIFEST) != manifest_hash:
            raise ValueError("复制后的交付清单与源目录不同")
        staged.rename(bundle)
    _ = verify_bundle(bundle)
    if file_hash(bundle / MANIFEST) != manifest_hash:
        raise ValueError("保留的交付清单与待安装版本不同")
    return bundle


def _install_home_locked(root: Path, home: Path) -> Path:
    bundle = _retain_bundle_locked(root, home)
    release = bundle.parent
    key = release.name
    environment = managed_path(home, release / "runtime")
    receipt = managed_path(home, environment / RECEIPT)
    if environment.exists() and not receipt.is_file():
        # Only unfinished installer-owned runtime is moved. Completed venvs must
        # stay at their creation path because their scripts contain absolute paths.
        failed = environment.rename(release / f"runtime-failed-{uuid.uuid4().hex}")
        print(f"已保留上次未完成的运行环境: {failed}")
    if not environment.exists():
        _ = install_bundle(bundle, environment)
    check_receipt(environment, bundle)
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    _ = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [str(python), "-m", "lab_tools.application", "--help"], check=True
    )
    launcher = home / "lab.py"
    launcher_text = (
        "import subprocess, sys\nfrom pathlib import Path\n"
        "home = Path(__file__).resolve().parent\n"
        f"python = home / {python.relative_to(home).as_posix()!r}\n"
        "args = sys.argv[1:]\n"
        "module = ('lab_tools.sandbox' if args[:1] == ['teach'] "
        "else 'lab_tools.application')\n"
        "if args[:1] == ['teach']: args = args[1:]\n"
        "command = [str(python), '-m', module, '--home', str(home)]\n"
        "raise SystemExit(subprocess.call([*command, *args]))\n"
    )
    command_text = (
        '@echo off\ncd /d "%~dp0"\n'
        f'"%~dp0{python.relative_to(home)}" "%~dp0lab.py" %*\n'
        "if errorlevel 1 pause\n"
    )
    # Both files are complete before replacement. Either retained interpreter can
    # bootstrap lab.py; only lab.py chooses the selected application release.
    pending: list[Path] = []
    try:
        for name, content in (("lab.cmd", command_text), ("lab.py", launcher_text)):
            _ = managed_path(home, home / name)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=home, prefix=f".{name}-", delete=False
            ) as stream:
                pending.append(Path(stream.name))
                _ = stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        _ = pending[0].replace(home / "lab.cmd")
        _ = pending[1].replace(launcher)
    finally:
        for path in pending:
            path.unlink(missing_ok=True)
    print(
        f"已准备并选择默认版本 {key}。Windows 双击 {home / 'lab.cmd'}; "
        f"其他系统运行 python {launcher}。\n"
        "正在运行的管理器尚未更换；下次启动时尝试切换，存在进行中的管理操作时会拒绝切换。"
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
