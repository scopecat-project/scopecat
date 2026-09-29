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
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
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


def _run_install(command: list[str]) -> None:
    _ = subprocess.run(  # noqa: S603 - fixed installer command
        command,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def install_bundle(
    root: Path, destination: Path, *, copy_packages: bool = False
) -> Path:
    root = resolve_delivery(root)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"环境目录已存在: {destination}; 请使用新目录")
    _ = verify_bundle(root)
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("离线安装前请准备 uv 和匹配的 Python 解释器")
    _run_install(
        [
            uv,
            "venv",
            "--offline",
            "--python",
            sys.executable,
            str(destination),
        ],
    )
    python = destination / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
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
            str(root / "requirements.lock"),
        ],
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


def install_home(
    root: Path,
    home: Path,
    *,
    software_home: Path | None = None,
    entry: Path | None = None,
) -> Path:
    """Prepare a retained release, then atomically select it for the next launch."""
    root = resolve_delivery(root)
    home = home.resolve()
    software_home = software_home.resolve() if software_home else home
    if home.is_relative_to(root) or software_home.is_relative_to(root):
        raise ValueError("安装中心不能位于待复制的交付目录内")
    _ = verify_bundle(root)
    home.mkdir(parents=True, exist_ok=True)
    software_home.mkdir(parents=True, exist_ok=True)
    with _installation_lock(home):
        if software_home != home:
            with _installation_lock(software_home):
                return _install_home_locked(root, home, software_home, entry)
        return _install_home_locked(root, home, software_home, entry)


def retain_bundle(root: Path, home: Path) -> Path:
    """Retain and verify delivery files without selecting an application runtime."""
    root = resolve_delivery(root)
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


def prepare_home(root: Path, home: Path) -> tuple[Path, Path]:
    """Retain a candidate runtime without selecting software or touching data."""
    root = resolve_delivery(root)
    home = home.resolve()
    if home.is_relative_to(root):
        raise ValueError("安装中心不能位于待复制的交付目录内")
    _ = verify_bundle(root)
    home.mkdir(parents=True, exist_ok=True)
    with _installation_lock(home):
        return _prepare_home_locked(root, home)


def _prepare_home_locked(root: Path, home: Path) -> tuple[Path, Path]:
    bundle = _retain_bundle_locked(root, home)
    release = bundle.parent
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
    return python, bundle


def _install_home_locked(
    root: Path, home: Path, software_home: Path, entry: Path | None
) -> Path:
    python, bundle = _prepare_home_locked(root, software_home)
    key = bundle.parent.name
    _ = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [
            str(python),
            "-m",
            "lab_tools.application",
            "--home",
            str(home),
            "--software-home",
            str(software_home),
            "--action",
            "update",
            "--static-dir",
            str(bundle / "gui"),
        ],
        check=True,
    )
    launcher = software_home / "lab.py"
    launcher_text = (
        "import json, subprocess, sys\nfrom pathlib import Path\n"
        f"home = Path({str(home)!r})\n"
        "selection = json.loads((home / 'installation.json')"
        ".read_text(encoding='utf-8'))\n"
        "python = Path(selection['python'])\n"
        "args = sys.argv[1:]\n"
        "entries = {'teach': 'lab_tools.practice', "
        "'notebook': 'lab_tools.author_notebook'}\n"
        "module = entries.get(args[0], 'lab_tools.application') "
        "if args else 'lab_tools.application'\n"
        "if args and args[0] in entries: args = args[1:]\n"
        "if '--action' in args and 'desktop' in args and sys.platform == 'win32':\n"
        "    python = python.with_name('pythonw.exe')\n"
        "command = [str(python), '-m', module, '--home', str(home)]\n"
        "raise SystemExit(subprocess.call([*command, *args]))\n"
    )
    command_text = (
        '@echo off\ncd /d "%~dp0"\n'
        f'"%~dp0{python.relative_to(software_home)}" "%~dp0lab.py" %*\n'
        "if errorlevel 1 pause\n"
    )
    notebook_command = '@echo off\ncall "%~dp0lab.cmd" notebook %*\n'
    shell_command = (
        '#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\n'
        f"{shlex.quote('./' + python.relative_to(software_home).as_posix())}"
        ' ./lab.py "$@"\n'
        'status=$?\nif [ "$status" -ne 0 ]; then\n'
        '  printf "\\n启动失败，请保留上方错误信息。按回车关闭。"\n'
        "  read answer\nfi\n"
        'exit "$status"\n'
    )
    notebook_shell = (
        '#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\n'
        'exec ./Scopecat.command notebook "$@"\n'
    )
    # Retained interpreters can bootstrap the stable entry; installation.json is
    # the only release selection, including updates prepared inside the workbench.
    pending: list[Path] = []
    try:
        entries = [
            ("lab.cmd", command_text),
            ("Notebook.cmd", notebook_command),
            ("Scopecat.command", shell_command),
            ("Notebook.command", notebook_shell),
            ("lab.py", launcher_text),
        ]
        for name, content in entries:
            _ = managed_path(software_home, software_home / name)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=software_home,
                prefix=f".{name}-",
                delete=False,
            ) as stream:
                pending.append(Path(stream.name))
                _ = stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if name.endswith(".command"):
                pending[-1].chmod(0o755)
        for staged, (name, _) in zip(pending, entries, strict=True):
            _ = staged.replace(software_home / name)
    finally:
        for path in pending:
            path.unlink(missing_ok=True)
    # This file is also the standalone installer. Use the completed runtime's
    # helper, without importing package code into the bootstrap interpreter.
    _ = subprocess.run(  # noqa: S603 - selected installed runtime and fixed module
        [
            str(python),
            "-m",
            "lab_tools.desktop_install",
            str(software_home),
            *([str(entry)] if entry else []),
        ],
        check=True,
    )
    print(
        f"已准备并选择默认版本 {key}。"
        "Mac 使用 Scopecat.app；Windows 使用 Scopecat.lnk。\n"
        "应用保持停止；打开入口直接进入工作台。作者目录和科学数据保留。"
    )
    return launcher


class InstallArguments(Protocol):
    destination: Path | None
    home: Path | None
    bundle: Path
    software_home: Path | None
    entry: Path | None


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description="从本地交付目录离线安装最小教学环境")
    _ = parser.add_argument("destination", type=Path, nargs="?")
    _ = parser.add_argument("--home", type=Path)
    _ = parser.add_argument("--software-home", type=Path)
    _ = parser.add_argument("--entry", type=Path)
    _ = parser.add_argument("--bundle", type=Path, default=Path(__file__).parent)
    args = cast("InstallArguments", cast("object", parser.parse_args()))
    try:
        if args.home is not None:
            if args.destination is not None:
                parser.error("destination 和 --home 只能选择一个")
            print(
                install_home(
                    Path(args.bundle),
                    Path(args.home),
                    software_home=args.software_home,
                    entry=args.entry,
                )
            )
        elif args.destination is not None:
            print(install_bundle(Path(args.bundle), Path(args.destination)))
        else:
            parser.error("请指定 --home；平台默认安装请使用应用安装入口")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
