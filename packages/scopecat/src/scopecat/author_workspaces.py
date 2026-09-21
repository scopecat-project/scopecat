"""Machine-local author bindings; registration is a trusted maintenance action."""

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.author_workspace import SERVICE_AUTHOR_WORKSPACE
from scopecat.runtime_binding import load_runtime_binding

if TYPE_CHECKING:
    from scopecat.installed_adapter import AdapterReference


LABORATORY_MANIFEST_NAME = "scopecat.laboratory.toml"


class LocalAuthorWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1)
    root: Path
    python: Path


class LocalAuthorWorkspaces(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    service_root: Path
    items: tuple[LocalAuthorWorkspace, ...] = ()


def author_bindings_path(root: Path) -> Path:
    return load_runtime_binding(root).data_root / "author-workspaces.json"


def local_author_workspaces(root: Path) -> tuple[LocalAuthorWorkspace, ...]:
    path = author_bindings_path(root)
    if not path.is_file():
        return ()
    return LocalAuthorWorkspaces.model_validate_json(path.read_bytes()).items


def author_workspace_id(root: Path) -> str:
    """Resolve an already registered local identity, never register on connection."""
    root = root.resolve()
    path = author_bindings_path(root)
    if not path.is_file():
        return SERVICE_AUTHOR_WORKSPACE
    registry = LocalAuthorWorkspaces.model_validate_json(path.read_bytes())
    if root == registry.service_root:
        return SERVICE_AUTHOR_WORKSPACE
    for item in registry.items:
        if item.root == root:
            return item.id
    raise ValueError(
        "This location is not registered with the deployment service workspace"
    )


def laboratory_adapter(root: Path) -> AdapterReference:
    """Require an installed laboratory without project-local composition additions."""
    from scopecat.installed_adapter import parse_adapter_reference

    document = tomllib.loads((root / "scopecat.toml").read_text(encoding="utf-8"))
    lab = document.get("lab")
    if not isinstance(lab, dict) or set(cast("dict[str, object]", lab)) != {"adapter"}:
        raise ValueError(
            "Author-only folders require a laboratory with only [lab.adapter]; "
            "move maintained capabilities into the installed adapter"
        )
    return parse_adapter_reference(cast("dict[str, object]", lab)["adapter"])


def bound_lab_adapter(root: Path) -> AdapterReference:
    """Resolve only an explicitly registered author's laboratory declaration."""
    path = author_bindings_path(root)
    if not path.is_file():
        raise ValueError("Author-only folder is not registered with a laboratory")
    registry = LocalAuthorWorkspaces.model_validate_json(path.read_bytes())
    if not any(item.root == root for item in registry.items):
        raise ValueError("Author-only folder is not registered with this laboratory")
    owner = load_runtime_binding(registry.service_root)
    source = load_runtime_binding(root)
    if (owner.data_root, owner.deployment_root) != (
        source.data_root,
        source.deployment_root,
    ):
        raise ValueError("Author and laboratory runtime bindings differ")
    return laboratory_adapter(registry.service_root)
