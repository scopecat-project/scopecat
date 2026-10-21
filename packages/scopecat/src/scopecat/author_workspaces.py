"""Machine-local author bindings; registration is a trusted maintenance action."""

from pathlib import Path

from scopecat.application_locator import (
    LocalAuthorWorkspace as LocalAuthorWorkspace,
)
from scopecat.application_locator import (
    LocalAuthorWorkspaces as LocalAuthorWorkspaces,
)
from scopecat.application_locator import read_author_location
from scopecat.runtime_binding import load_runtime_binding


def author_bindings_path(root: Path) -> Path:
    return load_runtime_binding(root).coordination_root / "author-workspaces.json"


def local_author_workspaces(root: Path) -> tuple[LocalAuthorWorkspace, ...]:
    path = author_bindings_path(root)
    if not path.is_file():
        return ()
    return LocalAuthorWorkspaces.model_validate_json(path.read_bytes()).items


def author_workspace_id(root: Path) -> str:
    """Resolve an already registered local identity, never register on connection."""
    root = root.resolve()
    for item in local_author_workspaces(root):
        if item.root == root:
            return item.id
    raise ValueError(
        "This location is not registered with the deployment service workspace"
    )


def service_workspace_root(root: Path) -> Path:
    """Resolve process ownership independently of the registered source catalog."""
    location = read_author_location(root.resolve())
    if location is not None:
        # Revoking source membership must not grant that source ownership of the
        # application store. Maintenance still belongs to its recorded service.
        return location.application
    path = author_bindings_path(root)
    if not path.is_file():
        return root.resolve()
    return LocalAuthorWorkspaces.model_validate_json(path.read_bytes()).service_root
