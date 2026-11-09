"""Reproducible, version-locked WebView2 and WinForms repairs for Windows.

This is a build-time dependency transformation, never a runtime monkeypatch.
The upstream license is retained. Native source and packaged hosts use the same wheel.
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
VERSION = "6.2.1+scopecat.windows.2"
UPSTREAM_SHA256 = "9d07275f53894ab4d5e2e0e996227193e7187dec276d9b624dccbce029216b46"
SOURCE_SHA256 = "f3f9168ecb39f7447566112fd14c5ec55830eddf02053eafef0d86fac509a3a4"
PATCH_SHA256 = "5fafd1f716fc66184e6aee8a35677e53fb716935cd3cffd33c19c0ea5eacab81"
RESULT_SHA256 = "b30da5ece042cfe154bf808c0bdb89a6cf68b30f1cff924d784308bfdccad979"
WHEEL_SHA256 = "9a0b73c39b3f959866045f59a2e619565bad286a436d06d404f0f09185b246c5"
WINFORMS_SOURCE_SHA256 = (
    "e3962a59e74532d9266f08d1efb086519877b2d322ea83900036f59794db4b4d"
)
WINFORMS_PATCH_SHA256 = (
    "4ba7e28b5b3131040690a6868b4f4c49bbab39b38b3ad64104d502d415eb8e85"
)
WINFORMS_SHA256 = "dc42082a8d6ae25189a958fcfec8310131c4f2c834a8cd81508fd913054b2d49"
EDGE = "webview/platforms/edgechromium.py"
WINFORMS = "webview/platforms/winforms.py"


def _require_hash(data: bytes, expected: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(
            "Windows dependency input/output differs from the reviewed patch"
        )


def patch_windows(source: bytes) -> bytes:
    """Apply only the reviewed source change, failing closed on any mismatch."""
    return _apply_patch(
        source,
        filename="edgechromium.py",
        patch_name="pywebview-6.2.1-windows.patch",
        source_hash=SOURCE_SHA256,
        patch_hash=PATCH_SHA256,
        result_hash=RESULT_SHA256,
    )


def patch_winforms(source: bytes) -> bytes:
    """Preserve host SIGINT ownership and consume fallback exit signals once."""
    return _apply_patch(
        source,
        filename="winforms.py",
        patch_name="pywebview-6.2.1-winforms.patch",
        source_hash=WINFORMS_SOURCE_SHA256,
        patch_hash=WINFORMS_PATCH_SHA256,
        result_hash=WINFORMS_SHA256,
    )


def _apply_patch(
    source: bytes,
    *,
    filename: str,
    patch_name: str,
    source_hash: str,
    patch_hash: str,
    result_hash: str,
) -> bytes:
    _require_hash(source, source_hash)
    patch = Path(__file__).with_name("patches") / patch_name
    _require_hash(patch.read_bytes(), patch_hash)
    patch_tool = shutil.which("git")
    if patch_tool is None:
        raise RuntimeError("Windows delivery requires the git build tool")
    with tempfile.TemporaryDirectory(prefix="scopecat-windows-") as temporary:
        windows = Path(temporary) / filename
        _ = windows.write_bytes(source)
        _ = subprocess.run(  # noqa: S603 - fixed checked dependency patch
            [
                patch_tool,
                "-c",
                "core.autocrlf=false",
                "apply",
                "--no-index",
                str(patch.resolve()),
            ],
            cwd=temporary,
            check=True,
            capture_output=True,
            text=True,
        )
        source = windows.read_bytes()
    _require_hash(source, result_hash)
    return source


def patch_wheel(wheels: Path) -> Path:
    """Replace exactly the reviewed upstream wheel in a disposable build folder."""
    source = wheels / f"pywebview-{UPSTREAM_VERSION}-py3-none-any.whl"
    _require_hash(source.read_bytes(), UPSTREAM_SHA256)
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files[WINFORMS] = patch_winforms(files[WINFORMS])
    files[EDGE] = patch_windows(files[EDGE])
    old_info = f"pywebview-{UPSTREAM_VERSION}.dist-info/"
    info = f"pywebview-{VERSION}.dist-info/"
    files = {name.replace(old_info, info): data for name, data in files.items()}
    files[info + "METADATA"] = files[info + "METADATA"].replace(
        f"Version: {UPSTREAM_VERSION}\n".encode(), f"Version: {VERSION}\n".encode(), 1
    )
    files[info + "scopecat-windows-patch.json"] = (
        json.dumps(
            {
                "upstream_version": UPSTREAM_VERSION,
                "upstream_wheel_sha256": UPSTREAM_SHA256,
                "upstream_windows_sha256": SOURCE_SHA256,
                "patch_sha256": PATCH_SHA256,
                "windows_sha256": RESULT_SHA256,
                "upstream_winforms_sha256": WINFORMS_SOURCE_SHA256,
                "winforms_patch_sha256": WINFORMS_PATCH_SHA256,
                "winforms_sha256": WINFORMS_SHA256,
                "scope": (
                    "Windows native hosts; private cookie initialization once per "
                    "host/profile; preserve application SIGINT and one-shot fallback"
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
    """Native Windows hosts must not silently install an unpatched dependency."""
    wheel = wheels / f"pywebview-{VERSION}-py3-none-any.whl"
    _require_hash(wheel.read_bytes(), WHEEL_SHA256)
