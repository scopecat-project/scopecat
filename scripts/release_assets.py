"""Assemble or verify a complete, immutable release inventory without networking."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path, PureWindowsPath
from typing import Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/lab-tools/src"))
from lab_tools.release_identity import read_release

PACKAGES = frozenset(
    {
        "scopecat",
        "scopecat-server",
        "scopecat-instruments",
        "scopecat-quantum",
        "scopecat-lab-tools",
        "scopecat-lab-teaching",
        "scopecat-testkit",
    }
)
NATIVE = {"macos": ("darwin", ".dmg"), "windows": ("win32", ".exe")}


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return cast("dict[str, object]", value)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, object]:
    return _object(
        cast("object", json.loads(path.read_bytes(), object_pairs_hook=_unique))
    )


def _asset(directory: Path, name: str) -> Path:
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or PureWindowsPath(name).drive
        or "\\" in name
    ):
        raise ValueError(f"Invalid asset filename: {name}")
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing asset or forbidden symlink: {name}")
    return path


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_hash(directory: Path, name: str, expected: object) -> str:
    actual = _hash(_asset(directory, name))
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError(f"Invalid SHA-256: {name}")
    if actual != expected:
        raise ValueError(f"Asset checksum mismatch: {name}")
    return actual


def inventory(directory: Path, commit: str, release_file: Path) -> dict[str, object]:
    """Validate constituent manifests before constructing their release identity."""
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Release requires a full commit SHA")
    identity = read_release(release_file, require_release=True)
    preview_path = _asset(directory, "preview.json")
    preview = _read(preview_path)
    if (
        preview.get("format") != 1
        or preview.get("commit") != commit
        or preview.get("release_version") != identity.version
        or preview.get("build_number") != identity.build_number
        or preview.get("channel") != "release"
    ):
        raise ValueError("Preview release identity mismatch")
    ui_version = preview.get("ui_version")
    ui_suffix = f"-scopecat.{identity.version.replace('-', '.')}.g{commit[:12]}"
    if not isinstance(ui_version, str) or not ui_version.endswith(ui_suffix):
        raise ValueError("UI release provenance mismatch")
    packages = _object(preview.get("packages"))
    if set(packages) != set(PACKAGES):
        raise ValueError("Release requires all seven public packages")
    files = _object(preview.get("files"))
    wheels = {name for name in files if name.endswith(".whl")}
    if len(wheels) != len(PACKAGES) or set(files) != wheels | {"scopecat-ui.zip"}:
        raise ValueError("Release requires exactly seven wheels and the UI archive")
    hashes = {
        name: _check_hash(directory, name, digest) for name, digest in files.items()
    }
    found: set[str] = set()
    for name in wheels:
        with zipfile.ZipFile(_asset(directory, name)) as archive:
            metadata = [
                item
                for item in archive.namelist()
                if item.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1:
                raise ValueError(f"Wheel requires one METADATA: {name}")
            message = BytesParser().parsebytes(archive.read(metadata[0]))
        package = re.sub(r"[-_.]+", "-", str(message["Name"])).lower()
        version = str(message["Version"])
        if package not in packages or package in found or version != packages[package]:
            raise ValueError(f"Wheel package/version mismatch: {name}")
        suffix = f"+scopecat.{identity.pep440}.g{commit[:12]}"
        if not version.endswith(suffix) or ".dev" not in version:
            raise ValueError(f"Wheel release provenance mismatch: {name}")
        found.add(package)
    if found != set(PACKAGES):
        raise ValueError("Incomplete wheel inventory")
    hashes["preview.json"] = _hash(preview_path)
    for platform, (target_platform, suffix) in NATIVE.items():
        manifest_name, bundle_name = (
            f"{platform}-native-release.json",
            f"{platform}-bundle.json",
        )
        native = _read(_asset(directory, manifest_name))
        bundle_path = _asset(directory, bundle_name)
        bundle = _read(bundle_path)
        if (
            native.get("format") != 1
            or bundle.get("format") != 1
            or _object(native.get("sources")).get("public") != commit
            or native.get("sources") != bundle.get("sources")
            or native.get("target") != bundle.get("target")
            or _object(bundle.get("target")).get("platform") != target_platform
            or native.get("runtime") != bundle.get("runtime")
            or native.get("build_id") != bundle.get("build_id")
            or bundle.get("release_version") != identity.version
            or bundle.get("build_number") != identity.build_number
        ):
            raise ValueError(f"Native release identity mismatch: {platform}")
        bundle_files = _object(bundle.get("files"))
        if any(bundle_files.get(f"wheels/{name}") != hashes[name] for name in wheels):
            raise ValueError(f"Native public wheel inventory mismatch: {platform}")
        installer = native.get("installer")
        if (
            not isinstance(installer, str)
            or not installer.endswith(suffix)
            or installer in hashes
        ):
            raise ValueError(f"Invalid or duplicate installer: {platform}")
        hashes[installer] = _check_hash(directory, installer, native.get("sha256"))
        hashes[bundle_name] = _check_hash(
            directory, bundle_name, native.get("bundle_sha256")
        )
        hashes[manifest_name] = _hash(directory / manifest_name)
    actual_files = {
        path.name for path in directory.iterdir() if path.name != "release.json"
    }
    if actual_files != set(hashes):
        raise ValueError("Release directory has unexpected or missing assets")
    return {
        "format": 1,
        "version": identity.version,
        "build_number": identity.build_number,
        "commit": commit,
        "preview": {"file": "preview.json", "sha256": hashes["preview.json"]},
        "files": dict(sorted(hashes.items())),
    }


def assemble(directory: Path, commit: str, release_file: Path) -> Path:
    destination = directory / "release.json"
    if destination.exists():
        raise FileExistsError("release.json already exists; never overwrite a release")
    document = inventory(directory, commit, release_file)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(document, indent=2) + "\n")
    return destination


def verify(directory: Path, commit: str, release_file: Path) -> Path:
    manifest = _asset(directory, "release.json")
    if _read(manifest) != inventory(directory, commit, release_file):
        raise ValueError("Release manifest differs from verified assets")
    return manifest


class Arguments(Protocol):
    command: str
    directory: Path
    commit: str


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("assemble", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--commit", required=True)
    args = cast("Arguments", cast("object", parser.parse_args()))
    operation = assemble if args.command == "assemble" else verify
    print(
        operation(
            args.directory,
            args.commit,
            Path(__file__).resolve().parents[1] / "release.toml",
        )
    )


if __name__ == "__main__":
    main()
