"""Capture and materialize declared local source roots without importing user code."""

from __future__ import annotations

import base64
import platform
import shutil
import tempfile
import tomllib
from contextvars import ContextVar
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path
from typing import Protocol, cast

from scopecat.kernel.content_identity import (
    content_fingerprint,
    sha256_content_hash,
    sha256_json_hash,
)
from scopecat.records.author_revision import (
    AuthorRevisionBundle,
    AuthorRevisionManifest,
    AuthorRevisionRef,
)
from scopecat.records.author_workspace import SERVICE_AUTHOR_WORKSPACE

loading_workspace: ContextVar[str] = ContextVar(
    "loading_author_workspace", default=SERVICE_AUTHOR_WORKSPACE
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


class SourceProject(Protocol):
    @property
    def root(self) -> Path: ...
    @property
    def manifest(self) -> Path: ...
    @property
    def source_roots(self) -> tuple[str, ...]: ...
    @property
    def refresh_roots(self) -> tuple[str, ...]: ...
    @property
    def installed_packages(self) -> tuple[tuple[str, str], ...]: ...
    @property
    def dependencies(self) -> tuple[str, ...] | None: ...


def capture_sources(project: SourceProject) -> AuthorRevisionBundle:
    """Snapshot all declared roots, including helpers, analysis and local resources."""
    files: dict[str, bytes] = {"scopecat.toml": project.manifest.read_bytes()}
    for name in (
        ()
        if project.dependencies is not None
        else ("pyproject.toml", "uv.lock", "requirements.txt")
    ):
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
    # Catalog selection belongs to this author revision. Keep its exact bytes in
    # files/ref, but compare laboratory maintenance independently of that choice.
    document = tomllib.loads(files["scopecat.toml"].decode("utf-8"))
    authors = cast("dict[str, object]", document.get("authors", {}))
    authors.pop("modules", None)
    maintenance["scopecat.toml"] = sha256_json_hash(content_fingerprint(document))
    from scopecat.execution_environment import execution_packages
    from scopecat.installed_authors import capture_installed_authors

    installed = capture_installed_authors(project.installed_packages)

    packages = (
        environment_packages()
        if project.dependencies is None
        else execution_packages(
            (*project.dependencies, *(name for _, name in project.installed_packages))
        )
    )
    maintained_environment = (
        {} if project.dependencies is None else {"packages": packages}
    )
    manifest = AuthorRevisionManifest(
        files=digests,
        source_roots=project.source_roots,
        refresh_roots=project.refresh_roots,
        python=platform.python_version(),
        packages=packages,
        installed_authors=installed,
        maintenance_hash=sha256_json_hash(
            {
                **maintained_environment,
                "files": maintenance,
                "installed_authors": {
                    name: item.model_dump(mode="json")
                    for name, item in installed.items()
                },
            }
        ),
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
    from scopecat.installed_authors import capture_installed_authors

    actual = capture_installed_authors(
        tuple(
            (name, item.distribution)
            for name, item in manifest.installed_authors.items()
        )
    )
    if actual != manifest.installed_authors:
        raise ValueError(
            "installed author package content changed; restore the recorded "
            "installed artifacts before recovery"
        )
    if manifest.python != platform.python_version():
        raise ValueError("author revision requires its recorded Python version")
    for name, expected in manifest.packages.items():
        try:
            actual_version = version(name)
        except PackageNotFoundError:
            actual_version = "not installed"
        if actual_version != expected:
            raise ValueError(
                f"author revision requires installed package {name}=={expected}; "
                f"found {actual_version}. Restore the recorded execution environment"
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
        except OSError:
            if not target.is_dir():
                raise
            return materialize_sources(bundle, directory)
        return target
    finally:
        shutil.rmtree(staged, ignore_errors=True)
