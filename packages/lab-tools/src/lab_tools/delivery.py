"""Build a platform-specific offline teaching delivery from this checkout."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Protocol, cast

from lab_tools.bundle import MANIFEST, file_hash, inventory, target_identity
from scopecat.kernel.content_identity import sha256_content_hash, sha256_json_hash

REPOSITORY = Path.cwd()
MODULES = {
    "scopecat": "scopecat",
    "scopecat_server": "scopecat-server",
    "lab_teaching": "scopecat-lab-teaching",
}


class BuildArguments(Protocol):
    destination: Path
    release: bool
    notebook: bool
    source: Path
    gui: Path | None


def run(command: list[str], *, cwd: Path) -> None:
    print("执行: " + " ".join(command), flush=True)
    _ = subprocess.run(command, cwd=cwd, check=True)  # noqa: S603 - explicit local tool and argument list


def wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        message = BytesParser().parsebytes(archive.read(metadata))
        return str(message["Name"]), str(message["Version"])


def build_delivery(
    destination: Path,
    *,
    release: bool = False,
    notebook: bool = False,
    source: Path | None = None,
    gui: Path | None = None,
) -> Path:
    repository = (source or REPOSITORY).resolve()
    destination = destination.resolve()
    if release:
        for repo in (repository,):
            if subprocess.check_output(
                ["git", "status", "--porcelain"],  # noqa: S607 - standard maintainer tool
                cwd=repo,
                text=True,
            ).strip():
                raise ValueError("正式发布要求 public 工作目录均干净")
    destination.mkdir(parents=True, exist_ok=False)
    public = repository
    ui = public / "apps/scopecat-ui"
    pnpm = shutil.which("pnpm")
    if pnpm is None and gui is None:
        raise ValueError("维护者构建需要 pnpm")
    if gui is None:
        assert pnpm is not None
        run([pnpm, "install", "--frozen-lockfile"], cwd=ui)
        run([pnpm, "run", "build"], cwd=ui)
    _ = shutil.copytree(gui or ui / "dist", destination / "gui")
    wheels = destination / "wheels"
    wheels.mkdir()
    # Export the reviewed repository lock, excluding locally built distributions.
    # Download exact third-party artifacts without another dependency resolution.
    dependency_lock = destination / "dependencies.lock"
    run(
        [
            "uv",
            "export",
            "--locked",
            "--only-group",
            "delivery-notebook" if notebook else "delivery",
            "--no-emit-local",
            "--format",
            "requirements-txt",
            "--output-file",
            str(dependency_lock),
        ],
        cwd=repository,
    )
    packages = (
        public / "packages/scopecat",
        public / "packages/scopecat-server",
        repository / "packages/lab-teaching",
        repository / "packages/lab-tools",
    )
    for package in packages:
        run(
            [
                "uv",
                "build",
                "--wheel",
                "--build-constraints",
                str(dependency_lock),
                "--out-dir",
                str(wheels),
                str(package),
            ],
            cwd=repository,
        )
    run(
        [
            "uv",
            "run",
            "--isolated",
            "--locked",
            "--only-group",
            "delivery-build",
            "python",
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--require-hashes",
            "--only-binary=:all:",
            "--dest",
            str(wheels),
            "-r",
            str(dependency_lock),
        ],
        cwd=repository,
    )
    release_version = next(
        version
        for wheel in wheels.glob("*.whl")
        for name, version in (wheel_metadata(wheel),)
        if name.replace("_", "-").lower() == "scopecat-lab-tools"
    )
    _ = shutil.copyfile(repository / "uv.lock", destination / "build.lock")
    runtime: dict[str, object] = {}
    requirements: list[str] = []
    for wheel in sorted(wheels.glob("*.whl")):
        name, version = wheel_metadata(wheel)
        requirements.append(f"{name}=={version} --hash=sha256:{file_hash(wheel)}")
        for module, package_name in MODULES.items():
            if name.replace("_", "-").lower() != package_name:
                continue
            with zipfile.ZipFile(wheel) as archive:
                files = {
                    item: sha256_content_hash(archive.read(item))
                    for item in archive.namelist()
                    if item.startswith(module + "/")
                    and not item.endswith("/")
                    and not item.endswith((".pyc", ".pyo"))
                }
            runtime[module] = {
                "distribution": package_name,
                "version": version,
                "content_hash": sha256_json_hash(files),
            }
    if set(runtime) != set(MODULES):
        raise ValueError("交付缺少科学软件 wheel")
    _ = (destination / "requirements.lock").write_text(
        "\n".join(sorted(requirements)) + "\n", encoding="utf-8"
    )
    # Copy the maintained stdlib-only installer; no second installer implementation.
    _ = shutil.copyfile(
        repository / "packages/lab-tools/src/lab_tools/bundle.py",
        destination / "install.py",
    )
    files = inventory(destination, ("gui", "wheels"))
    files.update(
        {
            name: file_hash(destination / name)
            for name in (
                "requirements.lock",
                "dependencies.lock",
                "build.lock",
                "install.py",
            )
        }
    )
    sources = {
        name: subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - standard maintainer tool
            cwd=repo,
            text=True,
        ).strip()
        for name, repo in (("public", public),)
    }
    for name, repo in (("public", public),):
        if subprocess.check_output(
            ["git", "status", "--porcelain"],  # noqa: S607 - standard maintainer tool
            cwd=repo,
            text=True,
        ).strip():
            sources[name] += "+dirty"
    _ = (destination / MANIFEST).write_text(
        json.dumps(
            {
                "format": 1,
                "release_version": release_version,
                "release_kind": "release" if release else "development",
                "build_id": sha256_json_hash(
                    {"sources": sources, "files": files, "target": target_identity()}
                ),
                "build_tools": {
                    "python": sys.version,
                    "uv": subprocess.check_output(
                        ["uv", "--version"],  # noqa: S607 - standard maintainer tool
                        text=True,
                    ).strip(),
                    "pnpm": "prebuilt GUI"
                    if pnpm is None
                    else subprocess.check_output(  # noqa: S603 - fixed tool commands
                        [pnpm, "--version"],
                        text=True,
                    ).strip(),
                },
                "target": target_identity(),
                "sources": sources,
                "runtime": runtime,
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已构建 {len(requirements)} 个 wheels; 安装验证通过前不要交付", flush=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("destination", type=Path)
    _ = parser.add_argument("--release", action="store_true")
    _ = parser.add_argument("--notebook", action="store_true")
    _ = parser.add_argument("--source", type=Path, default=Path.cwd())
    _ = parser.add_argument("--gui", type=Path)
    args = cast("BuildArguments", cast("object", parser.parse_args()))
    print(
        build_delivery(
            args.destination,
            release=args.release,
            notebook=args.notebook,
            source=args.source,
            gui=args.gui,
        )
    )


if __name__ == "__main__":
    main()
