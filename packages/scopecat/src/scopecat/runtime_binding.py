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
    if set(table) != {"data_root", "deployment_root"}:
        raise RuntimeBindingError("[runtime] requires data_root and deployment_root")

    def location(name: str) -> Path:
        item = table[name]
        if not isinstance(item, str) or not item.strip():
            raise RuntimeBindingError(f"runtime.{name} must be a nonempty path")
        selected = (workspace / item).resolve()
        if workspace.is_relative_to(selected):
            raise RuntimeBindingError(f"runtime.{name} cannot contain the workspace")
        return selected

    return RuntimeBinding(workspace, location("data_root"), location("deployment_root"))
