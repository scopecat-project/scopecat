"""Machine-local author bindings; registration is a trusted maintenance action."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from scopecat.runtime_binding import load_runtime_binding

LEGACY_AUTHOR_WORKSPACE = "legacy"


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
        return LEGACY_AUTHOR_WORKSPACE
    registry = LocalAuthorWorkspaces.model_validate_json(path.read_bytes())
    if root == registry.service_root:
        return LEGACY_AUTHOR_WORKSPACE
    for item in registry.items:
        if item.root == root:
            return item.id
    raise ValueError(
        "This location is not registered with the deployment service workspace"
    )
