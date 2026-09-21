"""Explicit stopped-deployment author registration; never an HTTP path mutation."""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import ExitStack, closing
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout
from scopecat.author_workspaces import (
    LocalAuthorWorkspace,
    LocalAuthorWorkspaces,
    author_bindings_path,
    author_workspace_id,
    laboratory_adapter,
    local_author_workspaces,
)
from scopecat.project import load_project, open_project
from scopecat.project_sources import (
    capture_sources,
    require_environment,
    require_shared_composition,
)
from scopecat.runtime_binding import RUNTIME_BINDING_NAME


def register_author_workspace(
    service: Path,
    workspace: Path,
    *,
    name: str | None = None,
    identity: str | None = None,
) -> LocalAuthorWorkspace:
    owner = open_project(service)
    project = open_project(workspace, resolve_adapter=False)
    project = load_project(
        project.manifest,
        lab_adapter=laboratory_adapter(owner.root) if project.author_only else None,
    )
    if author_workspace_id(owner.root) != "legacy":
        raise ValueError("Registration requires the deployment service workspace")
    if owner.root == project.root:
        raise ValueError(
            "The service workspace already owns the legacy author identity"
        )
    binding = owner.runtime_binding
    binding.deployment_root.mkdir(parents=True, exist_ok=True)
    binding.data_root.mkdir(parents=True, exist_ok=True)
    try:
        with ExitStack() as locks:
            candidates = {
                binding.deployment_root / "deployment.lock",
                binding.data_root / "daemon.lock",
                project.runtime_binding.deployment_root / "deployment.lock",
                project.runtime_binding.data_root / "daemon.lock",
            }
            for path in sorted(candidates):
                path.parent.mkdir(parents=True, exist_ok=True)
                locks.enter_context(FileLock(path, timeout=0))
            baseline = capture_sources(owner)
            candidate = capture_sources(project)
            require_environment(candidate.manifest)
            require_shared_composition(owner, project, baseline, candidate)
            location = project.root / RUNTIME_BINDING_NAME
            if location.exists() and (
                project.runtime_binding.data_root != binding.data_root
                or project.runtime_binding.deployment_root != binding.deployment_root
            ):
                raise ValueError(
                    "Workspace already has a different explicit runtime binding"
                )
            if (
                not location.exists()
                and (project.runtime_binding.data_root / "control.sqlite3").exists()
            ):
                raise ValueError(
                    "Workspace has its own scientific store; "
                    "do not rebind it implicitly"
                )
            if identity is not None:
                if identity == "legacy":
                    raise ValueError(
                        "The legacy source owner cannot be rebound as an "
                        "author workspace"
                    )
                with closing(
                    sqlite3.connect(
                        (binding.data_root / "control.sqlite3").resolve().as_uri()
                        + "?mode=ro",
                        uri=True,
                    )
                ) as database:
                    if (
                        database.execute(
                            "SELECT 1 FROM author_workspaces WHERE workspace_id=?",
                            (identity,),
                        ).fetchone()
                        is None
                    ):
                        raise ValueError(
                            "The workspace identity is not in this scientific store"
                        )
            entries = list(local_author_workspaces(owner.root))
            previous = next(
                (item for item in entries if item.root == project.root), None
            )
            if (
                previous is not None
                and identity is not None
                and previous.id != identity
            ):
                raise ValueError(
                    "This location already belongs to another source owner"
                )
            selected = LocalAuthorWorkspace(
                id=identity or (previous.id if previous else uuid4().hex),
                name=name or project.root.name,
                root=project.root,
                python=Path(sys.executable).absolute(),
            )
            # Location binding is local configuration, excluded from captured source.
            location.write_text(
                "[runtime]\n"
                f"data_root = {json.dumps(str(binding.data_root))}\n"
                f"deployment_root = {json.dumps(str(binding.deployment_root))}\n",
                encoding="utf-8",
            )
            entries = [item for item in entries if item.id != selected.id]
            entries.append(selected)
            path = author_bindings_path(owner.root)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                LocalAuthorWorkspaces(
                    service_root=owner.root, items=tuple(entries)
                ).model_dump_json(indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
            return selected
    except Timeout as error:
        raise ValueError(
            "Stop the deployment before registering an author workspace"
        ) from error
