"""Content-addressed author resources for the source desktop (never a release)."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from importlib.metadata import version
from pathlib import Path

import psutil
from filelock import FileLock
from platformdirs import user_data_path

from scopecat_server.validation_process import terminate_validation_process_tree

from .bundle import target_identity, verify_bundle


def development_home(source: Path) -> Path:
    identity = os.path.normcase(str(source.resolve()))
    key = hashlib.sha256(os.fsencode(identity)).hexdigest()[:20]
    return user_data_path("Scopecat-Development", appauthor=False) / key


def source_identity(source: Path) -> str:
    """Hash actual package inputs, including dirty, untracked and ignored source.

    UI is served by Vite. Its source does not enter the frozen author payload.
    Only interpreter bytecode caches are excluded from package source trees.
    """
    paths = {source / name for name in ("uv.lock", "pyproject.toml", "release.toml")}
    for package in (source / "packages").iterdir():
        paths.update(package / name for name in ("pyproject.toml", "README.md"))
        paths.update(
            path
            for path in (package / "src").rglob("*")
            if "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
        )
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_file():
            digest.update(
                path.relative_to(source).as_posix().encode()
                + b"\0"
                + path.read_bytes()
                + b"\0"
            )
    return digest.hexdigest()


def resource_identity(source: Path) -> str:
    tools = {
        name: subprocess.check_output([name, "--version"], text=True).strip()  # noqa: S603
        for name in ("uv",)
    }
    value = {
        "source": source_identity(source),
        "target": target_identity(),
        "python": sys.version,
        "platform": platform.platform(),
        "libc": platform.libc_ver(),
        "uv_package": version("uv"),
        "tools": tools,
        "format": 1,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def prepare_resources(source: Path, cache: Path) -> Path:
    """Publish only verified complete payloads. Old identities remain untouched."""
    from .delivery import build_delivery
    from .toolchain import build

    cache.mkdir(parents=True, exist_ok=True)
    key = resource_identity(source)
    payload = cache / key
    with FileLock(cache / "resources.lock"):
        if not payload.exists():
            print(
                "Preparing locked author resources; "
                "this first build may take a few minutes.",
                flush=True,
            )
            with tempfile.TemporaryDirectory(
                prefix=".prepare-", dir=cache
            ) as directory:
                staging = Path(directory)
                # The backend serves only this diagnostic page. Product UI always
                # comes from Vite; author resources must not rebuild for GUI edits.
                gui = staging / "gui"
                gui.mkdir()
                (gui / "index.html").write_text(
                    "<!doctype html><title>Scopecat development backend</title>"
                    "<p>Open the development UI URL printed by the launcher.</p>",
                    encoding="utf-8",
                )
                delivery = build_delivery(staging / "delivery", source=source, gui=gui)
                complete = build(delivery, staging / "complete")
                if resource_identity(source) != key:
                    raise ValueError(
                        "Source changed while preparing resources; retry startup."
                    )
                verify_bundle(complete)
                complete.rename(payload)
        verify_bundle(payload)
    return payload


def prepare_native_dependency(payload: Path, cache: Path) -> None:
    """Use the same verified wheel repair as packaging, without editing the venv."""
    if sys.platform not in {"darwin", "win32"}:
        return
    if sys.platform == "darwin":
        from .cocoa_dependency import verify_wheel
    else:
        from .windows_dependency import verify_wheel
    verify_wheel(payload / "wheels")
    (wheel,) = (payload / "wheels").glob("pywebview-*.whl")
    key = hashlib.sha256(wheel.read_bytes()).hexdigest()
    target = cache / "native" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(target.parent / "native.lock"), zipfile.ZipFile(wheel) as archive:
        if not target.exists():
            with tempfile.TemporaryDirectory(dir=target.parent) as directory:
                staging = Path(directory) / "wheel"
                archive.extractall(staging)
                staging.rename(target)
        for name in archive.namelist():
            if not name.endswith("/") and (target / name).read_bytes() != archive.read(
                name
            ):
                raise ValueError(
                    "Development native dependency changed; preserve it for diagnosis."
                )
    sys.path.insert(0, str(target))


def prepare_resources_logged(source: Path, cache: Path, log: Path) -> Path:
    """Keep build output and isolate cancellable build tools from terminal signals."""
    key = resource_identity(source)
    payload = cache / key
    if payload.exists():
        return prepare_resources(source, cache)
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"Preparing author resources; build log: {log}", flush=True)
    with log.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(  # noqa: S603 - fixed owned resource builder
            [sys.executable, "-m", "lab_tools.dev_resources", str(source), str(cache)],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        owner = psutil.Process(process.pid)
        try:
            if process.wait():
                raise ValueError(f"Resource preparation failed; see {log}")
        except BaseException:
            if process.poll() is None:
                terminate_validation_process_tree(process, owner=owner)
            raise
    if resource_identity(source) != key:
        raise ValueError("Source changed during preparation; retry startup.")
    verify_bundle(payload)
    return payload


if __name__ == "__main__":
    prepare_resources(Path(sys.argv[1]), Path(sys.argv[2]))
