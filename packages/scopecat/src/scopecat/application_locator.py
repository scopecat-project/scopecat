"""Read-only discovery of an explicitly registered local application owner."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RuntimeBindingError(ValueError):
    """A workspace's local execution binding is invalid."""


class AuthorLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: int = Field(default=1, ge=1, le=1)
    workspace: Path
    application: Path
    workspace_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    pending: bool = False


def locator_home() -> Path:
    """Per-user persistent metadata; never an installation or source directory."""
    if override := os.environ.get("SCOPECAT_LOCATOR_HOME"):
        path = Path(override)
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeBindingError(
                "LOCALAPPDATA is required for application discovery"
            )
        path = Path(local) / "Scopecat" / "author-locations"
    elif sys.platform == "darwin":
        path = Path.home() / "Library/Application Support/Scopecat/author-locations"
    else:
        path = (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
            / "Scopecat/author-locations"
        )
    if not path.is_absolute():
        raise RuntimeBindingError("Application locator home must be absolute")
    return path


def author_location_path(workspace: Path) -> Path:
    key = os.path.normcase(str(workspace.resolve()))
    return locator_home() / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")


def read_author_location(workspace: Path) -> AuthorLocation | None:
    try:
        location = AuthorLocation.model_validate_json(
            author_location_path(workspace).read_bytes()
        )
    except FileNotFoundError:
        return None
    except (OSError, ValidationError) as error:
        raise RuntimeBindingError(
            f"Cannot read author application locator: {error}"
        ) from error
    if location.workspace != workspace or not location.application.is_absolute():
        raise RuntimeBindingError(
            "Author application locator has a conflicting workspace"
        )
    return location


class LocalAuthorWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1)
    root: Path
    python: Path
    retained_pythons: tuple[Path, ...] = ()


class LocalAuthorWorkspaces(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    service_root: Path
    items: tuple[LocalAuthorWorkspace, ...] = ()


def read_data_identity(data_root: Path) -> str:
    """Read the existing scientific identity without creating a database."""
    try:
        with closing(
            sqlite3.connect(
                (data_root / "control.sqlite3").resolve().as_uri() + "?mode=ro",
                uri=True,
            )
        ) as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT identity FROM project_identity WHERE singleton = 1"
                ).fetchone(),
            )
        if row is None or not isinstance(row[0], str) or not row[0]:
            raise RuntimeBindingError("Application data identity is missing")
        return row[0]
    except sqlite3.Error as error:
        raise RuntimeBindingError(
            f"Cannot read application data identity: {error}"
        ) from error
