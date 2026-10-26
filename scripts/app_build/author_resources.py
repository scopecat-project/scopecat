"""Build author resources from explicit materials or a verified legacy delivery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from scopecat_app.author_resources import PACKAGES, verify_resources
from scopecat_app.bundle import (
    MANIFEST,
    Bundle,
    file_hash,
    inventory,
    target_identity,
    verify_bundle,
)


@dataclass(frozen=True)
class AuthorInputs:
    """Build-time materials; hashes and source identity come from their producer.

    ``files`` covers the entire GUI, exactly one wheel per PACKAGES entry,
    requirements.lock and any selected host uv executable/licenses. Unrelated
    producer files are not consumed. No delivery manifest or installer is needed.
    """

    root: Path
    target: dict[str, str]
    sources: dict[str, str]
    files: dict[str, str]
    runtime: dict[str, object] = field(default_factory=dict)


def _materials(root: Path) -> dict[str, str]:
    for folder in ("gui", "wheels", "toolchain"):
        path = root / folder
        if (
            path.is_symlink()
            or (path.exists() and not path.is_dir())
            or (folder != "toolchain" and not path.is_dir())
        ):
            raise ValueError("作者输入目录缺失或是符号链接")
    files = inventory(root, ("gui",))
    if "gui/index.html" not in files:
        raise ValueError("作者输入缺少 gui/index.html")
    selected = [root / "requirements.lock"]
    for package in PACKAGES:
        matches = list((root / "wheels").glob(f"{package}-*.whl"))
        if len(matches) != 1:
            raise ValueError(f"作者资源需要唯一 {package} wheel")
        selected.append(matches[0])
    for name in (
        "uv.exe" if os.name == "nt" else "uv",
        "LICENSE-APACHE",
        "LICENSE-MIT",
    ):
        path = root / "toolchain" / name
        if path.exists() or path.is_symlink():
            selected.append(path)
    for path in selected:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"作者输入文件缺失或是符号链接: {path.name}")
        files[path.relative_to(root).as_posix()] = file_hash(path)
    return files


def build(
    source: Path | AuthorInputs, destination: Path, *, python: Path | None = None
) -> Path:
    """Verify required materials before projecting an independently hashed artifact."""
    if isinstance(source, Path):
        # Compatibility adapter: legacy callers keep full delivery validation.
        document = verify_bundle(source)
        selected = _materials(source)
        inputs = AuthorInputs(
            source,
            document["target"],
            document["sources"],
            {name: document["files"][name] for name in selected},
            document["runtime"],
        )
    else:
        inputs = source
        if not inputs.sources or any(
            not key or not isinstance(value, str) or not value
            for key, value in cast("dict[str, object]", inputs.sources).items()
        ):
            raise ValueError("作者输入缺少源码身份")
        document: Bundle = {
            "format": 2,
            "target": inputs.target,
            "sources": inputs.sources,
            "runtime": inputs.runtime,
            "files": inputs.files,
        }
    if inputs.target != target_identity():
        raise ValueError("作者输入 target 与当前环境不一致")
    materials = _materials(inputs.root)
    if materials != inputs.files:
        raise ValueError("作者输入缺失、被修改或哈希清单不匹配")
    recipe = _recipe(python or Path(sys.executable), inputs.target)
    destination.mkdir(parents=True, exist_ok=False)
    for folder in ("gui", "wheels", "toolchain"):
        (destination / folder).mkdir()
    for name in materials:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inputs.root / name, target)
    # Compare copied bytes with the producer's hashes before generating new hashes.
    if _materials(destination) != inputs.files:
        raise ValueError("复制的作者输入与预期哈希不一致")
    (destination / "author.json").write_text(
        json.dumps(recipe, indent=2) + "\n", encoding="utf-8"
    )
    document = {**document, "format": 2, "files": dict(materials)}
    document["files"]["author.json"] = file_hash(destination / "author.json")
    (destination / MANIFEST).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    verify_resources(destination)
    return destination


def _recipe(python: Path, target: dict[str, str]) -> dict[str, object]:
    """Read the actual host/runtime being shipped, including its free-threaded ABI."""
    raw = subprocess.check_output(  # noqa: S603 - explicit build/runtime interpreter
        [
            str(python),
            "-I",
            "-c",
            (
                "import json,sys,sysconfig,platform; "
                "print(json.dumps(dict(version=platform.python_version(), target=dict("
                "platform=sys.platform, machine=platform.machine().lower(), "
                "python=f'{sys.version_info.major}.{sys.version_info.minor}', "
                "implementation=sys.implementation.name, "
                "free_threaded=str(bool(sysconfig.get_config_var('Py_GIL_DISABLED')))))))"
            ),
        ],
        text=True,
    )
    info = cast("dict[str, object]", json.loads(raw))
    if info["target"] != target:
        raise ValueError("作者配方的实际 Python 与交付 target 不一致")
    return {
        "python": str(info["version"])
        + ("t" if target["free_threaded"] == "True" else ""),
        "requires-python": ">=3.14",
        "packages": list(PACKAGES),
    }
