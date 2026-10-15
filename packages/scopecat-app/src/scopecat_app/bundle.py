"""Installed application resource integrity; standard library only.
Checksums detect mismatched artifacts, not the authenticity of their publisher.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import sys
import sysconfig
from pathlib import Path
from typing import TypedDict, cast

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


def verify_bundle(root: Path) -> Bundle:
    root = root.resolve()
    if (root / MANIFEST).is_symlink():
        raise ValueError("交付清单不能是符号链接")
    bundle = read_bundle(root)
    if bundle["target"] != target_identity():
        raise ValueError("交付包的操作系统、CPU 或 Python ABI 与当前环境不同")
    folders = ("gui", "wheels", "toolchain")
    actual = inventory(root, folders)
    expected = {
        name: value
        for name, value in bundle["files"].items()
        if name.startswith(tuple(folder + "/" for folder in folders))
    }
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


def installed_bundle(prefix: Path) -> Path | None:
    """Resolve the existing installer receipt, without searching for moved payloads."""
    receipt = prefix / RECEIPT
    if not receipt.is_file():
        return None
    info = cast("dict[str, str]", json.loads(receipt.read_text(encoding="utf-8")))
    root = Path(info["bundle"])
    if not root.is_absolute():
        raise ValueError("交付记录必须使用绝对路径；请从原交付包重新准备环境")
    if not (root / MANIFEST).is_file():
        raise ValueError("交付记录指向的资源不存在；请保留原环境并恢复原交付包")
    if file_hash(root / MANIFEST) != info["manifest_sha256"]:
        raise ValueError("交付清单与安装记录不同; 请保留原环境和原产物")
    return root.resolve()


def managed_path(home: Path, path: Path) -> Path:
    """Managed destinations must not redirect writes outside this installation."""
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"安装目标不能是符号链接: {current}")
    return path
