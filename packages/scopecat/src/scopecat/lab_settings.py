"""Explicit local adapter settings, separate from scientific configuration.

Keep the selected JSON file outside captured author source roots. Its identity
belongs to deployment admission; resulting scientific configuration is recorded
through the normal configuration registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ValidationError

from scopecat.runtime_binding import load_runtime_binding


class LabSettingsError(ValueError):
    """A configured local adapter settings file cannot be used."""


@dataclass(frozen=True, slots=True)
class _SettingsDocument:
    path: Path
    data: dict[str, object]
    identity: str


def _load_document(project_root: str | Path) -> _SettingsDocument | None:
    path = load_runtime_binding(project_root).settings_file
    return None if path is None else _read_document(path)


def _read_document(path: Path) -> _SettingsDocument:
    try:
        content = path.read_bytes()
        value = cast("object", json.loads(content))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LabSettingsError(f"cannot read lab settings {path}: {error}") from error
    if not isinstance(value, dict):
        raise LabSettingsError(f"lab settings {path} must contain a JSON object")
    identity = (
        "sha256:" + sha256(str(path).encode("utf-8") + b"\0" + content).hexdigest()
    )
    return _SettingsDocument(path, cast("dict[str, object]", value), identity)


def read_lab_settings[T: BaseModel](project_root: str | Path, model: type[T]) -> T:
    """Read once and validate with the adapter's settings model.

    No selected file supplies an empty object, allowing the adapter to own its
    defaults. Configured missing or invalid files never fall back to defaults.
    """
    document = _load_document(project_root)
    try:
        return model.model_validate({} if document is None else document.data)
    except ValidationError as error:
        source = (
            "defaults (no settings file selected)"
            if document is None
            else document.path
        )
        raise LabSettingsError(f"invalid lab settings {source}: {error}") from error


def lab_settings_identity(project_root: str | Path) -> str | None:
    """Identify the exact local file and contents without importing adapter code."""
    document = _load_document(project_root)
    return None if document is None else document.identity


def validate_lab_settings_file(path: str | Path) -> Path:
    """Validate a selected JSON object before saving its local binding."""
    return _read_document(Path(path).resolve()).path


__all__ = [
    "LabSettingsError",
    "lab_settings_identity",
    "read_lab_settings",
    "validate_lab_settings_file",
]
