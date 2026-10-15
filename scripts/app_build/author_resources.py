"""Build the author-resource projection from a verified delivery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

from scopecat_app.author_resources import PACKAGES, verify_resources
from scopecat_app.bundle import MANIFEST, file_hash, inventory, verify_bundle


def build(source: Path, destination: Path, *, python: Path | None = None) -> Path:
    """Project verified delivery inputs into an independently hashed artifact."""
    document = verify_bundle(source)
    recipe = _recipe(python or Path(sys.executable), document["target"])
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "gui", destination / "gui")
    wheels = destination / "wheels"
    wheels.mkdir()
    for package in PACKAGES:
        matches = list((source / "wheels").glob(f"{package}-*.whl"))
        if len(matches) != 1:
            raise ValueError(f"作者资源需要唯一 {package} wheel")
        shutil.copy2(matches[0], wheels / matches[0].name)
    shutil.copy2(source / "requirements.lock", destination / "requirements.lock")
    toolchain = destination / "toolchain"
    toolchain.mkdir()
    # A source host already has uv; native delivery may carry the verified tool.
    for name in (
        "uv.exe" if os.name == "nt" else "uv",
        "LICENSE-APACHE",
        "LICENSE-MIT",
    ):
        path = source / "toolchain" / name
        if path.is_file():
            shutil.copy2(path, toolchain / name)
    (destination / "author.json").write_text(
        json.dumps(recipe, indent=2) + "\n", encoding="utf-8"
    )
    document["format"] = 2
    document["files"] = inventory(destination, ("gui", "wheels", "toolchain"))
    for name in ("requirements.lock", "author.json"):
        document["files"][name] = file_hash(destination / name)
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
