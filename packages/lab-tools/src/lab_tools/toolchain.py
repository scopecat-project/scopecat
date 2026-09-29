"""Add a relocatable Python and uv to a platform-specific offline delivery."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from importlib.metadata import distribution
from pathlib import Path
from typing import Protocol, cast

from uv import find_uv_bin

from .bundle import MANIFEST, inventory, resolve_delivery, verify_bundle


def build(source: Path, destination: Path) -> Path:
    """Build a new delivery; never modify an already published manifest."""
    source = resolve_delivery(source)
    document = verify_bundle(source)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if destination.is_relative_to(source):
        raise ValueError("输出目录不能位于源交付目录内")
    if (source / "toolchain").exists():
        raise ValueError("源交付已包含 Python；请使用原始平台交付包")
    destination.parent.mkdir(parents=True, exist_ok=True)
    uv = find_uv_bin()
    # Stage beside the final artifact so publication is a single directory rename.
    with tempfile.TemporaryDirectory(
        prefix=".scopecat-toolchain-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        managed = staging / "managed"
        version = platform.python_version()
        if document["target"]["free_threaded"] == "True":
            version += "t"
        _ = subprocess.run(  # noqa: S603 - owned uv and exact Python version
            [
                uv,
                "python",
                "install",
                "--install-dir",
                str(managed),
                "--no-bin",
                "--no-registry",
                version,
            ],
            check=True,
        )
        relative_python = "python.exe" if os.name == "nt" else "bin/python3"
        candidates = {
            root.resolve()
            for root in managed.glob("cpython-*")
            if (root / relative_python).is_file()
        }
        if len(candidates) != 1:
            raise ValueError("下载的 Python 布局无法识别")
        python_root = candidates.pop()
        # Dereference uv's managed-install links; the archive owns every byte.
        portable = staging / "python"
        _ = shutil.copytree(python_root, portable, symlinks=False)
        result = subprocess.run(  # noqa: S603 - freshly downloaded relocated Python
            [
                str(portable / relative_python),
                "-I",
                "-B",
                "-c",
                (
                    "import json, sys; "
                    "print(json.dumps([sys.version.split()[0], sys.prefix]))"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        identity = cast("list[str]", json.loads(result.stdout))
        if identity != [platform.python_version(), str(portable)]:
            raise ValueError("Python 重定位检查失败")
        artifact = staging / "delivery"
        _ = shutil.copytree(source, artifact)
        toolchain = artifact / "toolchain"
        toolchain.mkdir()
        with tarfile.open(toolchain / "python.tar", "w") as archive:
            archive.add(portable, arcname=".")
        _ = shutil.copy2(uv, toolchain / ("uv.exe" if os.name == "nt" else "uv"))
        package = distribution("uv")
        for name in ("LICENSE-APACHE", "LICENSE-MIT"):
            files = [
                file
                for file in package.files or ()
                if file.name == name and "licenses" in file.parts
            ]
            if len(files) != 1:
                raise ValueError(f"uv 发行包缺少许可证：{name}")
            _ = shutil.copy2(str(package.locate_file(files[0])), toolchain / name)
        document["files"].update(inventory(artifact, ("toolchain",)))
        _ = (artifact / MANIFEST).write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
        _ = verify_bundle(artifact)
        _ = artifact.rename(destination)
    return destination


class Arguments(Protocol):
    source: Path
    destination: Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = cast("Arguments", cast("object", parser.parse_args()))
    print(build(args.source, args.destination))


if __name__ == "__main__":
    main()
