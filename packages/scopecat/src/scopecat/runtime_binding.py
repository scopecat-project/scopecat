"""Local deployment locations, separate from captured scientific source."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from scopecat.application_locator import (
    LocalAuthorWorkspaces,
    read_author_location,
    read_data_identity,
)
from scopecat.application_locator import (
    RuntimeBindingError as RuntimeBindingError,
)

RUNTIME_BINDING_NAME = "scopecat.runtime.toml"


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    workspace: Path
    data_root: Path
    deployment_root: Path
    settings_file: Path | None = None
    state_root: Path | None = None
    cache_root: Path | None = None

    @property
    def coordination_root(self) -> Path:
        return self.state_root or self.data_root

    @property
    def materialization_root(self) -> Path:
        return self.cache_root or self.data_root


def load_runtime_binding(
    root: str | Path,
    *,
    application: bool = True,
    discover: bool = True,
    target: str | Path | None = None,
) -> RuntimeBinding:
    """Resolve local paths without importing code, creating state or connecting."""
    workspace = Path(root).resolve()
    if application and (workspace / APPLICATION_INSTANCE_NAME).exists():
        return application_binding(workspace, read_application_layout(workspace))
    located = None
    if discover:
        located = _locate_author_binding(workspace, target=target)
    path = workspace / RUNTIME_BINDING_NAME
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return located or RuntimeBinding(
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
    if not required <= set(table) or set(table) - required - {
        "settings_file",
        "state_root",
        "cache_root",
    }:
        raise RuntimeBindingError(
            "[runtime] requires data_root and deployment_root; "
            "only settings_file, state_root and cache_root are optional"
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
    binding = RuntimeBinding(
        workspace,
        location("data_root"),
        location("deployment_root"),
        (workspace / settings_file).resolve()
        if isinstance(settings_file, str)
        else None,
        state_root=location("state_root") if "state_root" in table else None,
        cache_root=location("cache_root") if "cache_root" in table else None,
    )

    if located is not None and (
        binding.data_root != located.data_root
        or binding.deployment_root != located.deployment_root
        or binding.coordination_root != located.coordination_root
        or binding.materialization_root != located.materialization_root
        or binding.settings_file != located.settings_file
    ):
        raise RuntimeBindingError(
            "Legacy runtime binding conflicts with registered application; "
            "compare scopecat.runtime.toml with the intended application's "
            "registration and correct the conflict before reconnecting. "
            "Do not delete the binding or move data to bypass this check."
        )
    return binding


APPLICATION_INSTANCE_NAME = "scopecat.application.json"


def read_application_layout(root: Path) -> Literal["application", "legacy"]:
    document = cast(
        "object", json.loads((root / APPLICATION_INSTANCE_NAME).read_bytes())
    )
    if document == {"format": 1, "layout": "application"}:
        return "application"
    if document == {"format": 1, "layout": "legacy"}:
        return "legacy"
    raise ValueError("Unsupported application instance declaration")


def application_binding(
    root: Path, layout: Literal["application", "legacy"]
) -> RuntimeBinding:
    if layout == "legacy":
        return load_runtime_binding(root, application=False, discover=False)
    return RuntimeBinding(
        root,
        root / "data",
        root / "state",
        state_root=root / "state",
        cache_root=root / "cache",
    )


def _locate_author_binding(
    workspace: Path, *, target: str | Path | None = None
) -> RuntimeBinding | None:
    """Select an explicit target or saved default, never infer ownership."""
    override = target or os.environ.get("SCOPECAT_APPLICATION_ROOT") or None
    selected = Path(override).resolve() if override is not None else None
    location = read_author_location(workspace, selected)
    if location is not None and location.pending:
        raise RuntimeBindingError(
            "Author registration is incomplete; register again with this application"
        )
    if location is None and selected is None:
        return None
    if selected is None:
        assert location is not None
        selected = location.application
    target = selected
    if not target.is_dir():
        raise RuntimeBindingError(
            "Registered application is unavailable; restore or explicitly reconnect it"
        )
    try:
        binding = load_runtime_binding(target, discover=False)
        registry = LocalAuthorWorkspaces.model_validate_json(
            (binding.coordination_root / "author-workspaces.json").read_bytes()
        )
    except (OSError, ValueError) as error:
        raise RuntimeBindingError(
            f"Cannot verify registered application: {error}; check the selected "
            "application and add this folder in Settings → Author code if unregistered"
        ) from error
    entries = [item for item in registry.items if item.root == workspace]
    if (
        registry.service_root != target
        or len(entries) != 1
        or (location is not None and entries[0].id != location.workspace_id)
    ):
        raise RuntimeBindingError(
            "Author is not uniquely registered with this service workspace; "
            "explicitly register again"
        )
    project_id = read_data_identity(binding.data_root)
    if location is not None and project_id != location.project_id:
        raise RuntimeBindingError(
            "Registered application data identity has changed; restore the original "
            "data/application or explicitly register the intended target. "
            "Do not reconnect old jobs to a different data store."
        )
    return replace(binding, workspace=workspace)
