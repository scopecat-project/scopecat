"""Capture and materialize declared local source roots without importing user code."""

from __future__ import annotations

import base64
import platform
import shutil
import tempfile
from contextvars import ContextVar
from importlib.metadata import distributions
from pathlib import Path

from scopecat.kernel.content_identity import sha256_content_hash, sha256_json_hash
from scopecat.project import Project
from scopecat.records.author_revision import (
    AuthorRevisionBundle,
    AuthorRevisionManifest,
    AuthorRevisionRef,
)

loading_revision: ContextVar[AuthorRevisionRef | None] = ContextVar(
    "loading_author_revision", default=None
)

_EXCLUDED = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".scopecat",
    }
)


def capture_sources(project: Project) -> AuthorRevisionBundle:
    """Snapshot all declared roots, including helpers, analysis and local resources."""
    files: dict[str, bytes] = {"scopecat.toml": project.manifest.read_bytes()}
    for name in ("pyproject.toml", "uv.lock", "requirements.txt"):
        path = project.root / name
        if path.is_file():
            files[name] = path.read_bytes()
    for name in project.source_roots:
        source = project.root / name
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"source root must be a local directory: {name}")
        for directory, directories, names in source.walk():
            directories[:] = sorted(
                item for item in directories if item not in _EXCLUDED
            )
            if any((directory / item).is_symlink() for item in directories):
                raise ValueError(
                    f"source symlink is outside the snapshot contract: {directory}"
                )
            for filename in sorted(names):
                path = directory / filename
                if path.suffix in {".pyc", ".pyo"} or filename in _EXCLUDED:
                    continue
                if path.is_symlink():
                    raise ValueError(
                        f"source symlink is outside the snapshot contract: {path}"
                    )
                files[path.relative_to(project.root).as_posix()] = path.read_bytes()
    digests = {
        name: sha256_content_hash(content) for name, content in sorted(files.items())
    }
    maintenance = {
        name: digest
        for name, digest in digests.items()
        if not any(Path(name).is_relative_to(root) for root in project.refresh_roots)
    }
    manifest = AuthorRevisionManifest(
        files=digests,
        source_roots=project.source_roots,
        refresh_roots=project.refresh_roots,
        python=platform.python_version(),
        packages=environment_packages(),
        maintenance_hash=sha256_json_hash(maintenance),
    )
    return AuthorRevisionBundle(
        manifest=manifest,
        files={
            name: base64.b64encode(content).decode("ascii")
            for name, content in files.items()
        },
    )


def environment_packages() -> dict[str, str]:
    return {item.metadata["Name"]: item.version for item in distributions()}


def require_environment(manifest: AuthorRevisionManifest) -> None:
    if (
        manifest.python != platform.python_version()
        or manifest.packages != environment_packages()
    ):
        raise ValueError(
            "author revision requires its recorded Python and installed package "
            "versions; restore that environment before recovery"
        )


def materialize_sources(bundle: AuthorRevisionBundle, directory: Path) -> Path:
    """Verify each byte and atomically publish one immutable import tree."""
    manifest = bundle.manifest
    if set(bundle.files) != set(manifest.files):
        raise ValueError("author source inventory differs from manifest")
    target = directory / manifest.ref.content_hash.removeprefix("sha256:")
    directory.mkdir(parents=True, exist_ok=True)
    if target.exists():
        for name, digest in manifest.files.items():
            if sha256_content_hash((target / name).read_bytes()) != digest:
                raise ValueError(f"materialized author source changed: {name}")
        return target
    staged = Path(tempfile.mkdtemp(prefix=".source-", dir=directory))
    try:
        for name, encoded in bundle.files.items():
            content = base64.b64decode(encoded, validate=True)
            if sha256_content_hash(content) != manifest.files[name]:
                raise ValueError(f"author source checksum mismatch: {name}")
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            staged.rename(target)
        except FileExistsError:
            return materialize_sources(bundle, directory)
        return target
    finally:
        shutil.rmtree(staged, ignore_errors=True)
