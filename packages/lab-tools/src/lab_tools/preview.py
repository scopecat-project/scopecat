"""Fetch an explicitly pinned public preview and its workbench assets."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import urllib.request
import zipfile
from http.client import HTTPResponse
from pathlib import Path, PureWindowsPath
from typing import NotRequired, TypedDict, cast
from urllib.parse import urljoin, urlparse

from .bundle import file_hash
from .release_identity import ReleaseIdentity


class Preview(TypedDict):
    format: int
    commit: str
    packages: dict[str, str]
    files: dict[str, str]
    release_version: NotRequired[str]
    build_number: NotRequired[int]
    channel: NotRequired[str]
    ui_version: NotRequired[str]


def download(url: str, digest: str, destination: Path) -> None:
    if urlparse(url).scheme != "https" or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Preview downloads require HTTPS and a SHA-256 pin")
    with cast("HTTPResponse", urllib.request.urlopen(url, timeout=120)) as response:  # noqa: S310 - HTTPS checked
        content = response.read()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError(f"Preview artifact checksum mismatch: {url}")
    destination.write_bytes(content)


def read_preview(path: Path) -> Preview:
    document = cast("Preview", json.loads(path.read_text(encoding="utf-8")))
    if document["format"] != 1 or not re.fullmatch(r"[0-9a-f]{40}", document["commit"]):
        raise ValueError("Invalid public preview identity")
    if "channel" in document:
        if "release_version" not in document or "build_number" not in document:
            raise ValueError("Incomplete public release identity")
        if document["channel"] not in {"preview", "release"}:
            raise ValueError("Invalid public artifact channel")
        identity = ReleaseIdentity(
            document["release_version"], document["build_number"]
        )
        if document["channel"] == "release":
            identity.require_release()
    for name, digest in document["files"].items():
        if (
            Path(name).name != name
            or PureWindowsPath(name).drive
            or "\\" in name
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("Invalid preview artifact inventory")
    return document


def fetch_preview(pin: Path, cache: Path) -> Path:
    """Cache only verified assets; an interrupted download never becomes ready."""
    selection = cast("dict[str, str]", json.loads(pin.read_text(encoding="utf-8")))
    if not re.fullmatch(r"[0-9a-f]{64}", selection["sha256"]):
        raise ValueError("Invalid preview manifest pin")
    cache.mkdir(parents=True, exist_ok=True)
    ready = cache / selection["sha256"]
    if (ready / "preview.json").is_file():
        if file_hash(ready / "preview.json") != selection["sha256"]:
            raise ValueError("Cached preview manifest differs from its pin")
        metadata = read_preview(ready / "preview.json")
        if file_hash(ready / "scopecat-ui.zip") != metadata["files"]["scopecat-ui.zip"]:
            raise ValueError("Cached workbench differs from its preview")
        return ready
    with tempfile.TemporaryDirectory(prefix="preview-", dir=cache) as temporary:
        staging = Path(temporary)
        download(selection["url"], selection["sha256"], staging / "preview.json")
        metadata = read_preview(staging / "preview.json")
        download(
            urljoin(selection["url"], "scopecat-ui.zip"),
            metadata["files"]["scopecat-ui.zip"],
            staging / "scopecat-ui.zip",
        )
        staging.rename(ready)
    return ready


def unpack_gui(preview: Path, destination: Path) -> Preview:
    metadata = read_preview(preview / "preview.json")
    archive = preview / "scopecat-ui.zip"
    if file_hash(archive) != metadata["files"][archive.name]:
        raise ValueError("Workbench checksum mismatch")
    with zipfile.ZipFile(archive) as stream:
        for name in stream.namelist():
            if (
                Path(name).is_absolute()
                or PureWindowsPath(name).drive
                or ".." in Path(name).parts
                or "\\" in name
            ):
                raise ValueError("Invalid workbench archive path")
        stream.extractall(destination)
    return metadata
