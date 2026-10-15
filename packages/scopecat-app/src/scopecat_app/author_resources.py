"""Small author preparation resources, separate from full offline deliveries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from .bundle import (
    MANIFEST,
    Bundle,
    file_hash,
    inventory,
    read_bundle,
    target_identity,
    verify_bundle,
)

PACKAGES = ("scopecat", "scopecat_server")


def read_resources(root: Path) -> Bundle:
    document = cast("Bundle", json.loads((root / MANIFEST).read_text(encoding="utf-8")))
    if document.get("format") == 1:
        return read_bundle(root)
    if document.get("format") != 2 or any(
        not isinstance(document.get(key), dict)
        for key in ("target", "sources", "runtime", "files")
    ):
        raise ValueError("无法识别作者资源清单")
    return document


def verify_resources(root: Path) -> Bundle:
    document = read_resources(root)
    if document["format"] == 1:
        return verify_bundle(root)
    if (root / MANIFEST).is_symlink() or document["target"] != target_identity():
        raise ValueError("作者资源平台或清单不匹配")
    for folder in ("gui", "wheels", "toolchain"):
        if (root / folder).is_symlink() or not (root / folder).is_dir():
            raise ValueError("作者资源目录缺失或是符号链接")
    actual = inventory(root, ("gui", "wheels", "toolchain"))
    for name in ("requirements.lock", "author.json"):
        if (root / name).is_symlink():
            raise ValueError("作者资源不能是符号链接")
        actual[name] = file_hash(root / name)
    allowed = {
        "gui",
        "wheels",
        "toolchain",
        MANIFEST,
        "requirements.lock",
        "author.json",
    }
    wheels = list((root / "wheels").iterdir())
    if (
        actual != document["files"]
        or "gui/index.html" not in actual
        or any(path.name not in allowed for path in root.iterdir())
        or len(wheels) != len(PACKAGES)
        or {path.name.split("-")[0] for path in wheels} != set(PACKAGES)
        or any(not path.name.endswith(".whl") for path in wheels)
        or any(
            path.name not in {"uv", "uv.exe", "LICENSE-APACHE", "LICENSE-MIT"}
            for path in (root / "toolchain").iterdir()
        )
    ):
        raise ValueError("作者资源缺失、被修改或含有多余交付文件")
    return document
