"""Build immutable public wheels and GUI from one Git commit, never the worktree."""
# ruff: noqa: S603, S607 -- fixed maintainer tools, explicit argument lists

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import Protocol, cast


class Arguments(Protocol):
    destination: Path
    ref: str


PACKAGES = (
    "packages/scopecat",
    "packages/scopecat-server",
    "packages/scopecat-instruments",
    "packages/scopecat-quantum",
    "packages/lab-tools",
    "packages/lab-teaching",
    "testing/scopecat-testkit",
)


def build(repository: Path, destination: Path, ref: str = "HEAD") -> Path:
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
        packages: dict[str, str] = {}
        for directory in PACKAGES:
            project = source / directory / "pyproject.toml"
            content = project.read_text()
            metadata = cast("dict[str, dict[str, str]]", tomllib.loads(content))[
                "project"
            ]
            version = f"{metadata['version']}.dev{timestamp}+g{commit[:12]}"
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
        subprocess.run(["pnpm", "run", "build"], cwd=ui, check=True)
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
    args = cast("Arguments", cast("object", parser.parse_args()))
    print(build(Path(__file__).resolve().parents[1], args.destination, args.ref))
