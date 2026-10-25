"""Prepare the exact portable build runtime before resolving delivery dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from importlib.metadata import distribution
from pathlib import Path
from typing import Protocol, cast

from filelock import FileLock
from uv import find_uv_bin

from scopecat_app.bundle import (
    file_hash,
    inventory,
    resolve_delivery,
    target_identity,
    verify_bundle,
)


def baseline(repository: Path) -> str:
    version = (repository / ".python-version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"3\.\d+\.\d+t?", version):
        raise ValueError(
            "Packaging requires an exact stable Python patch in .python-version"
        )
    actual = platform.python_version()
    if target_identity()["free_threaded"] == "True":
        actual += "t"
    if actual != version or target_identity()["implementation"] != "cpython":
        raise ValueError(
            f"Build Python drift: .python-version requires CPython {version}, "
            f"got {actual}"
        )
    return version


def select_runtime(repository: Path) -> dict[str, str]:
    """Check the pinned catalog without downloading an interpreter."""
    version = baseline(repository)
    # Custom catalogs/mirrors would invalidate the recorded built-in source URL.
    for name in ("UV_PYTHON_INSTALL_MIRROR", "UV_PYTHON_DOWNLOADS_JSON_URL"):
        if os.environ.get(name):
            raise ValueError(
                "Portable runtime provenance requires the pinned uv catalog; "
                f"unset {name}"
            )
    uv = find_uv_bin()
    package = distribution("uv")
    locked = cast(
        "dict[str, list[dict[str, object]]]",
        tomllib.loads((repository / "uv.lock").read_text(encoding="utf-8")),
    )
    versions = [item["version"] for item in locked["package"] if item["name"] == "uv"]
    if versions != [package.version]:
        raise ValueError(
            f"Build uv drift: lock requires {versions}, got {package.version}"
        )
    catalog = subprocess.run(  # noqa: S603 - owned uv and exact repository baseline
        [
            uv,
            "python",
            "list",
            version,
            "--only-downloads",
            "--output-format",
            "json",
            "--no-config",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    candidates = cast("list[dict[str, str]]", json.loads(catalog.stdout))
    if len(candidates) != 1:
        raise ValueError(
            f"Portable Python supply unavailable: CPython {version}, "
            f"target={target_identity()}, uv={package.version}; "
            f"expected one managed distribution, found {len(candidates)}"
        )
    selected = candidates[0]
    if selected["version"] != version.removesuffix("t") or selected["variant"] != (
        "freethreaded" if version.endswith("t") else "default"
    ):
        raise ValueError(
            f"Portable Python supply changed version/variant: {selected['key']}"
        )
    target = target_identity()
    expected = {
        "os": {"darwin": "macos", "win32": "windows"}.get(
            target["platform"], target["platform"]
        ),
        "arch": {"arm64": "aarch64", "amd64": "x86_64"}.get(
            target["machine"], target["machine"]
        ),
        "libc": (
            {"glibc": "gnu", "musl": "musl"}.get(platform.libc_ver()[0], "unknown")
            if target["platform"] == "linux"
            else "none"
        ),
        "implementation": "cpython",
    }
    if any(selected.get(name) != value for name, value in expected.items()):
        raise ValueError(f"Portable Python supply changed target: {selected['key']}")
    return selected


@contextmanager
def _download(repository: Path, destination: Path) -> Generator[Path]:
    """Use a clean managed base for both dependency preparation and the archive."""
    selected = select_runtime(repository)
    version = baseline(repository)
    uv = find_uv_bin()
    package = distribution("uv")
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(
        prefix=".scopecat-runtime-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        managed = staging / "managed"
        try:
            subprocess.run(  # noqa: S603 - exact key from pinned uv catalog
                [
                    uv,
                    "python",
                    "install",
                    "--install-dir",
                    str(managed),
                    "--no-bin",
                    "--no-registry",
                    "--no-config",
                    selected["key"],
                ],
                check=True,
            )
        except subprocess.CalledProcessError as error:
            raise ValueError(
                f"Portable Python download failed: {selected['key']}, "
                f"uv={package.version}, source={selected['url']}"
            ) from error
        relative = "python.exe" if os.name == "nt" else "bin/python3"
        root = managed / selected["key"]
        portable = staging / "python"
        shutil.copytree(root, portable, symlinks=False)
        python = portable / relative
        _probe(python, version, selected["key"])
        archive_path = destination / "python.tar"
        with tarfile.open(archive_path, "w") as archive:
            archive.add(portable, arcname=".")
        identity = {
            "python": version,
            "target": target_identity(),
            "distribution": selected,
            "distribution_build": (portable / "BUILD")
            .read_text(encoding="utf-8")
            .strip(),
            "archive_sha256": file_hash(archive_path),
            "uv": package.version,
        }
        (destination / "runtime.json").write_text(
            json.dumps(identity, indent=2) + "\n", encoding="utf-8"
        )
        shutil.copy2(uv, destination / ("uv.exe" if os.name == "nt" else "uv"))
        for name in ("LICENSE-APACHE", "LICENSE-MIT"):
            files = [
                file
                for file in package.files or ()
                if file.name == name and "licenses" in file.parts
            ]
            if len(files) != 1:
                raise ValueError(f"uv distribution lacks license: {name}")
            shutil.copy2(str(package.locate_file(files[0])), destination / name)
        yield python


def _cache_root(uv: str) -> Path:
    return (
        Path(
            subprocess.check_output(  # noqa: S603 - owned uv
                [uv, "cache", "dir"], text=True
            ).strip()
        )
        / "scopecat-portable-v1"
    )


@contextmanager
def prepare(repository: Path, destination: Path) -> Generator[Path]:
    """Reuse a hashed pristine archive, extracting a fresh base for each build."""
    version = baseline(repository)
    uv = find_uv_bin()
    # Include lock, executable and target: a tool/catalog update gets a new cache.
    key = hashlib.sha256(
        json.dumps(
            [
                version,
                file_hash(repository / "uv.lock"),
                file_hash(Path(uv)),
                target_identity(),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cache_root = _cache_root(uv)
    cache_root.mkdir(parents=True, exist_ok=True)
    cached = cache_root / key
    with FileLock(str(cache_root / (key + ".lock"))):
        valid = False
        try:
            expected = cast(
                "object",
                json.loads((cached / "cache.json").read_text(encoding="utf-8")),
            )
            actual = {
                name: value
                for name, value in inventory(cached, (".",)).items()
                if name != "cache.json"
            }
            valid = expected == actual and bool(actual)
        except OSError, ValueError:
            pass
        if not valid:
            with tempfile.TemporaryDirectory(
                prefix=".runtime-cache-", dir=cache_root
            ) as temporary:
                candidate = Path(temporary) / "toolchain"
                with _download(repository, candidate):
                    pass
                (candidate / "cache.json").write_text(
                    json.dumps(inventory(candidate, (".",))), encoding="utf-8"
                )
                if cached.exists():
                    shutil.rmtree(cached)
                candidate.rename(cached)
        shutil.copytree(
            cached, destination, ignore=shutil.ignore_patterns("cache.json")
        )
    with tempfile.TemporaryDirectory(
        prefix=".build-runtime-", dir=destination.parent
    ) as temporary:
        portable = Path(temporary) / "python"
        with tarfile.open(destination / "python.tar") as archive:
            archive.extractall(portable, filter="data")
        python = portable / ("python.exe" if os.name == "nt" else "bin/python3")
        _probe(python, version, key)
        yield python


def build(source: Path, destination: Path) -> Path:
    """Retain the existing CLI; reuse the runtime prepared with the delivery."""
    source = resolve_delivery(source)
    verify_bundle(source)
    if not (source / "toolchain/python.tar").is_file():
        raise ValueError(
            "Delivery lacks its prepared runtime; rebuild the delivery before packaging"
        )
    destination = destination.resolve()
    if destination.is_relative_to(source):
        raise ValueError("输出目录不能位于源交付目录内")
    shutil.copytree(source, destination)
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


def _probe(python: Path, version: str, key: str) -> None:
    portable = python.parents[0 if os.name == "nt" else 1]
    result = subprocess.run(  # noqa: S603 - freshly relocated managed Python
        [
            str(python),
            "-I",
            "-B",
            "-c",
            (
                "import json,sys,sysconfig,platform; "
                "print(json.dumps([platform.python_version(),sys.prefix,"
                "sys.implementation.name,"
                "bool(sysconfig.get_config_var('Py_GIL_DISABLED')),"
                "sys.platform,platform.machine().lower()]))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if json.loads(result.stdout) != [
        version.removesuffix("t"),
        str(portable),
        "cpython",
        version.endswith("t"),
        target_identity()["platform"],
        target_identity()["machine"],
    ]:
        raise ValueError(f"Portable Python relocation/identity check failed: {key}")


if __name__ == "__main__":
    main()
