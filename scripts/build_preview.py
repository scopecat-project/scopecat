"""Build immutable public wheels and GUI from one Git commit, never the worktree."""
# ruff: noqa: S603, S607 -- fixed maintainer tools, explicit argument lists

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/lab-tools/src"))
from lab_tools.release_identity import read_source_identity


class Arguments(Protocol):
    destination: Path
    ref: str
    release: bool


PACKAGES = (
    "packages/scopecat",
    "packages/scopecat-server",
    "packages/scopecat-instruments",
    "packages/scopecat-quantum",
    "packages/lab-tools",
    "packages/lab-teaching",
    "testing/scopecat-testkit",
)


def build(
    repository: Path, destination: Path, ref: str = "HEAD", *, release: bool = False
) -> Path:
    commit = subprocess.check_output(
        ["git", "rev-parse", f"{ref}^{{commit}}"], cwd=repository, text=True
    ).strip()
    timestamp = subprocess.check_output(
        ["git", "show", "-s", "--format=%ct", commit], cwd=repository, text=True
    ).strip()
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="scopecat-preview-") as temporary:
        root = Path(temporary)
        archive = root / "source.tar"
        subprocess.run(
            ["git", "archive", commit, "-o", str(archive)], cwd=repository, check=True
        )
        source = root / "source"
        with tarfile.open(archive) as stream:
            stream.extractall(source, filter="data")
        identity = read_source_identity(source / "release.toml", release=release)
        packages: dict[str, str] = {}
        for directory in PACKAGES:
            project = source / directory / "pyproject.toml"
            content = project.read_text()
            metadata = cast("dict[str, dict[str, str]]", tomllib.loads(content))[
                "project"
            ]
            version = (
                identity.python_package_version(metadata["version"], commit, timestamp)
                if release
                else f"{metadata['version']}.dev{timestamp}+g{commit[:12]}"
            )
            content = re.sub(
                r'^version = "[^"]+"$',
                f'version = "{version}"',
                content,
                count=1,
                flags=re.MULTILINE,
            )
            project.write_text(content)
            subprocess.run(
                [
                    "uv",
                    "build",
                    "--wheel",
                    "--no-sources",
                    "--out-dir",
                    str(destination),
                    str(project.parent),
                ],
                check=True,
                cwd=source,
            )
            packages[metadata["name"]] = version
        ui = source / "apps/scopecat-ui"
        subprocess.run(["pnpm", "install", "--frozen-lockfile"], cwd=ui, check=True)
        ui_project = ui / "package.json"
        ui_metadata = cast("dict[str, str]", json.loads(ui_project.read_text()))
        ui_version = (
            identity.javascript_package_version(ui_metadata["version"], commit)
            if release
            else f"{ui_metadata['version']}-dev.{timestamp}.g{commit[:12]}"
        )
        ui_metadata["version"] = ui_version
        ui_project.write_text(json.dumps(ui_metadata, indent=2) + "\n")
        subprocess.run(["pnpm", "run", "build"], cwd=ui, check=True)
        (ui / "dist/build-info.json").write_text(
            json.dumps({"source_commit": commit, "ui_version": ui_version}) + "\n",
            encoding="utf-8",
        )
        shutil.make_archive(str(destination / "scopecat-ui"), "zip", ui / "dist")
    files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(destination.iterdir())
        if path.suffix in {".whl", ".zip"}
    }
    manifest = destination / "preview.json"
    manifest.write_text(
        json.dumps(
            {
                "format": 1,
                "commit": commit,
                "release_version": identity.version,
                "build_number": identity.build_number,
                "channel": "release" if release else "preview",
                "ui_version": ui_version,
                "packages": packages,
                "files": files,
            },
            indent=2,
        )
        + "\n"
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--release", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args()))
    print(
        build(
            Path(__file__).resolve().parents[1],
            args.destination,
            args.ref,
            release=args.release,
        )
    )
