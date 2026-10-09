"""Validate an exact-commit acceptance build before reusing its browser assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import cast

from lab_tools.preview import read_preview


class Arguments(argparse.Namespace):
    commit: str = ""
    bundle: Path = Path()


def verify_bundle(bundle: Path, commit: str) -> dict[str, object]:
    manifest = read_preview(bundle / "preview.json")
    if manifest["commit"] != commit:
        raise ValueError(
            "Framework artifacts and browser checkout must use the same commit"
        )
    if "scopecat-ui.zip" not in manifest["files"]:
        raise ValueError("Framework artifact is missing its GUI")
    for name, digest in manifest["files"].items():
        with (bundle / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"Framework checksum mismatch: {name}")
    with zipfile.ZipFile(bundle / "scopecat-ui.zip") as archive:
        identity = cast("object", json.loads(archive.read("build-info.json")))
    if not manifest.get("ui_version") or identity != {
        "source_commit": commit,
        "ui_version": manifest.get("ui_version"),
    }:
        raise ValueError("GUI build identity differs from the framework manifest")
    return {"source_commit": commit, "files": manifest["files"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(namespace=Arguments())
    commit = args.commit
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        parser.error("commit must be a full SHA")
    result = verify_bundle(args.bundle, commit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
