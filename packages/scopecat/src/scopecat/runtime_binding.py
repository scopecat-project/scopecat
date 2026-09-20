"""Local deployment locations, separate from captured scientific source."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

RUNTIME_BINDING_NAME = "scopecat.runtime.toml"


class RuntimeBindingError(ValueError):
    """A workspace's local execution binding is invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    workspace: Path
    data_root: Path
    deployment_root: Path
    settings_file: Path | None = None


def load_runtime_binding(root: str | Path) -> RuntimeBinding:
    """Resolve local paths without importing code, creating state or connecting."""
    workspace = Path(root).resolve()
    path = workspace / RUNTIME_BINDING_NAME
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RuntimeBinding(
            workspace, workspace / ".scopecat", workspace / ".scopecat"
        )
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeBindingError(
            f"cannot read runtime binding {path}: {error}"
        ) from error
    value = document.get("runtime")
    if set(document) != {"runtime"} or not isinstance(value, dict):
        raise RuntimeBindingError("runtime binding requires only a [runtime] table")
    table = cast("dict[str, object]", value)
    required = {"data_root", "deployment_root"}
    if not required <= set(table) or set(table) - required - {"settings_file"}:
        raise RuntimeBindingError(
            "[runtime] requires data_root and deployment_root; "
            "only settings_file is optional"
        )

    def location(name: str) -> Path:
        item = table[name]
        if not isinstance(item, str) or not item.strip():
            raise RuntimeBindingError(f"runtime.{name} must be a nonempty path")
        selected = (workspace / item).resolve()
        if workspace.is_relative_to(selected):
            raise RuntimeBindingError(f"runtime.{name} cannot contain the workspace")
        return selected

    settings_file = table.get("settings_file")
    if settings_file is not None and (
        not isinstance(settings_file, str) or not settings_file.strip()
    ):
        raise RuntimeBindingError("runtime.settings_file must be a nonempty path")
    return RuntimeBinding(
        workspace,
        location("data_root"),
        location("deployment_root"),
        (workspace / settings_file).resolve()
        if isinstance(settings_file, str)
        else None,
    )
