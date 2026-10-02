"""Ad-hoc sealing of preview applications; this does not grant Gatekeeper trust."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .bundle import MANIFEST, file_hash, verify_bundle


def native_files(app: Path) -> list[Path]:
    """Find Mach-O code, including Python extensions outside bundle code folders."""
    magic = {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
    result: list[Path] = []
    for path in app.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if any(part.endswith(".dSYM") for part in path.parts):
            continue
        with path.open("rb") as stream:
            header = stream.read(4)
        if header in magic:
            result.append(path)
    return sorted(result, key=lambda path: (-len(path.parts), str(path)))


def _codesign(*arguments: str) -> None:
    _ = subprocess.run(  # noqa: S603 - fixed system tool, explicit package paths
        ["/usr/bin/codesign", *arguments], check=True
    )


def verify(app: Path) -> None:
    # --deep alone does not discover extension modules under Resources/python.
    for path in native_files(app):
        _codesign("--verify", "--strict", str(path))
    _codesign("--verify", "--deep", "--strict", str(app))


def sign(app: Path) -> None:
    """Sign inner code first, update delivery checksums, then seal the outer app."""
    payload = app / "Contents/Resources/payload"
    document = verify_bundle(payload)
    for path in native_files(app):
        _codesign("--force", "--sign", "-", "--timestamp=none", str(path))
        if path.is_relative_to(payload):
            document["files"][path.relative_to(payload).as_posix()] = file_hash(path)
    # The embedded uv executable is part of the checked offline delivery too.
    (payload / MANIFEST).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    _ = verify_bundle(payload)
    _codesign("--force", "--sign", "-", "--timestamp=none", str(app))
    verify(app)
