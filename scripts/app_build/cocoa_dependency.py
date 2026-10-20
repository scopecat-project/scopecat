"""Reproducible, version-locked Cocoa repair for Darwin delivery wheels.

This is a build-time dependency transformation, never a runtime monkeypatch.
The upstream license is retained. The native source host and packaged host use
the same repaired wheel.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from scripts.app_build.dependency_wheel import write_wheel

UPSTREAM_VERSION = "6.2.1"
VERSION = "6.2.1+scopecat.2"
UPSTREAM_SHA256 = "9d07275f53894ab4d5e2e0e996227193e7187dec276d9b624dccbce029216b46"
SOURCE_SHA256 = "27a5e95be0816f0bba7bed910eeda11b92de4598419a275f9346023e24ba27af"
PATCH_SHA256 = "2d49791f7a236d748a679475d228c1a4e1984d63ea259253f35473bce9cc8014"
RESULT_SHA256 = "af84442e09b10f943b700e3081ff7682869c85ac27e6784c9b1e76fed13a50ee"
WHEEL_SHA256 = "0e4067847e2276d707a650fe8cd2b3cddc44026f3bc72b9664658de24c1117ed"
COCOA = "webview/platforms/cocoa.py"


def _require_hash(data: bytes, expected: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(
            "Cocoa dependency input/output differs from the reviewed patch"
        )


def patch_cocoa(source: bytes) -> bytes:
    """Apply only the reviewed source change, failing closed on any mismatch."""
    _require_hash(source, SOURCE_SHA256)
    patch = Path(__file__).with_name("patches") / "pywebview-6.2.1-cocoa.patch"
    _require_hash(patch.read_bytes(), PATCH_SHA256)
    patch_tool = shutil.which("patch")
    if patch_tool is None:
        raise RuntimeError("Darwin delivery requires the patch build tool")
    with tempfile.TemporaryDirectory(prefix="scopecat-cocoa-") as temporary:
        cocoa = Path(temporary) / "cocoa.py"
        _ = cocoa.write_bytes(source)
        _ = subprocess.run(  # noqa: S603 - fixed checked dependency patch
            [patch_tool, "-t", "-N", str(cocoa), str(patch.resolve())],
            check=True,
            capture_output=True,
            text=True,
        )
        source = cocoa.read_bytes()
    _require_hash(source, RESULT_SHA256)
    return source


def patch_wheel(wheels: Path) -> Path:
    """Replace exactly the reviewed upstream wheel in a disposable build folder."""
    source = wheels / f"pywebview-{UPSTREAM_VERSION}-py3-none-any.whl"
    _require_hash(source.read_bytes(), UPSTREAM_SHA256)
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files[COCOA] = patch_cocoa(files[COCOA])
    old_info = f"pywebview-{UPSTREAM_VERSION}.dist-info/"
    info = f"pywebview-{VERSION}.dist-info/"
    files = {name.replace(old_info, info): data for name, data in files.items()}
    files[info + "METADATA"] = files[info + "METADATA"].replace(
        f"Version: {UPSTREAM_VERSION}\n".encode(), f"Version: {VERSION}\n".encode(), 1
    )
    files[info + "scopecat-cocoa-patch.json"] = (
        json.dumps(
            {
                "upstream_version": UPSTREAM_VERSION,
                "upstream_wheel_sha256": UPSTREAM_SHA256,
                "upstream_cocoa_sha256": SOURCE_SHA256,
                "patch_sha256": PATCH_SHA256,
                "cocoa_sha256": RESULT_SHA256,
                "scope": (
                    "Darwin native hosts; shared private store and "
                    "closed-window JS cancellation"
                ),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()
    destination = wheels / f"pywebview-{VERSION}-py3-none-any.whl"
    write_wheel(files, destination, info + "RECORD")
    _require_hash(destination.read_bytes(), WHEEL_SHA256)
    source.unlink()
    return destination


def verify_wheel(wheels: Path) -> None:
    """Native Mac hosts must not silently install an unpatched dependency."""
    wheel = wheels / f"pywebview-{VERSION}-py3-none-any.whl"
    _require_hash(wheel.read_bytes(), WHEEL_SHA256)
