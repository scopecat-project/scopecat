"""Capture loaded driver package bytes without opening an instrument."""

from __future__ import annotations

import sys
from pathlib import Path

from scopecat.installed_authors import capture_installed_authors
from scopecat.kernel.content_identity import sha256_content_hash, sha256_json_hash


def backend_artifact_hash(
    provider: object, installed_packages: tuple[tuple[str, str], ...] = ()
) -> str:
    if installed_packages:
        return sha256_json_hash(
            {
                name: package.model_dump(mode="json")
                for name, package in capture_installed_authors(
                    installed_packages
                ).items()
            }
        )
    # In-process tests and explicitly selected source development use package
    # bytes too. A class/version string would miss edits in imported helpers.
    qualified_name = type(provider).__module__
    module = sys.modules[qualified_name]
    if module.__file__ is None:
        raise ValueError("driver provider requires a source module")
    root = Path(module.__file__)
    # Include the nearest regular package and its helpers. Namespace parents
    # need not have __file__ (for example independently installed providers).
    parent = root.parent
    while (parent / "__init__.py").is_file():
        root = parent
        parent = parent.parent
    files = sorted(root.rglob("*")) if root.is_dir() else [root]
    return sha256_json_hash(
        {
            item.relative_to(root.parent).as_posix(): sha256_content_hash(
                item.read_bytes()
            )
            for item in files
            if item.is_file()
            and "__pycache__" not in item.parts
            and item.suffix not in {".pyc", ".pyo"}
        }
    )
