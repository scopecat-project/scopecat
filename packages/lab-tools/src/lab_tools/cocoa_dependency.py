"""Reproducible, version-locked Cocoa repair for Darwin delivery wheels.

This is a build-time dependency transformation, never a runtime monkeypatch.
The upstream license is retained. Source development still uses the upstream wheel.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

UPSTREAM_VERSION = "6.2.1"
VERSION = "6.2.1+scopecat.1"
UPSTREAM_SHA256 = "9d07275f53894ab4d5e2e0e996227193e7187dec276d9b624dccbce029216b46"
SOURCE_SHA256 = "27a5e95be0816f0bba7bed910eeda11b92de4598419a275f9346023e24ba27af"
PATCH_SHA256 = "01b5f91c4eaa9e16537eff465b03567a2062e9f68c8001fb7575fb7ac2b08456"
RESULT_SHA256 = "2fe47f99c36beb0bc5d156b3006321ff94c0f101d4fffc1ff49a5d2bad5abd39"
WHEEL_SHA256 = "69e66d40be74245e21569a8f83e98dd1b4013cd703233da19f78942bd0f976a9"
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
                "scope": "Darwin packaged host; private store shared per host",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()
    record = info + "RECORD"
    del files[record]
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        writer.writerow((name, "sha256=" + digest.decode(), str(len(data))))
    writer.writerow((record, "", ""))
    files[record] = rows.getvalue().encode()
    destination = wheels / f"pywebview-{VERSION}-py3-none-any.whl"
    # Fixed timestamps and uncompressed members make the wheel bytes reproducible
    # across build hosts, independent of their zlib version.
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    _require_hash(destination.read_bytes(), WHEEL_SHA256)
    source.unlink()
    return destination


def verify_wheel(wheels: Path) -> None:
    """Native Mac hosts must not silently install an unpatched dependency."""
    wheel = wheels / f"pywebview-{VERSION}-py3-none-any.whl"
    _require_hash(wheel.read_bytes(), WHEEL_SHA256)
