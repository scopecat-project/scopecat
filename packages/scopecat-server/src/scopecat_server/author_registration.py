"""Explicit local author registration, independent of the application lifecycle."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import ExitStack, closing
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout
from scopecat.application_locator import (
    AuthorLocation,
    author_location_path,
    read_author_location,
    read_data_identity,
)
from scopecat.author_workspaces import (
    LocalAuthorWorkspace,
    LocalAuthorWorkspaces,
    author_bindings_path,
    local_author_workspaces,
    service_workspace_root,
)
from scopecat.project import open_project
from scopecat.runtime_binding import (
    RUNTIME_BINDING_NAME,
    RuntimeBinding,
    load_runtime_binding,
)

from scopecat_server.author_environment import capture, check


def register_author_workspace(
    service: Path,
    workspace: Path,
    *,
    name: str | None = None,
    identity: str | None = None,
    python: Path | None = None,
) -> LocalAuthorWorkspace:
    from scopecat.application_instance import open_instance

    owner = open_instance(service)
    project = open_project(workspace, resolve_adapter=False)
    if service_workspace_root(owner.root) != owner.root:
        raise ValueError("Registration requires the deployment service workspace")
    binding = owner.runtime_binding
    original = load_runtime_binding(project.root, discover=False)
    locator = author_location_path(project.root)
    locator.parent.mkdir(parents=True, exist_ok=True)
    binding.deployment_root.mkdir(parents=True, exist_ok=True)
    binding.data_root.mkdir(parents=True, exist_ok=True)
    try:
        with ExitStack() as locks:
            locks.enter_context(FileLock(locator.with_suffix(".lock"), timeout=0))
            previous_location = read_author_location(project.root)
            if (
                previous_location is not None
                and previous_location.application != owner.root
            ):
                raise ValueError(
                    "Workspace already belongs to another application; "
                    "explicit transfer is required"
                )
            candidates = {
                binding.coordination_root / "author-workspaces.lock",
            }
            if original.deployment_root != binding.deployment_root and (
                original.deployment_root.exists() or original.data_root.exists()
            ):
                candidates.add(original.deployment_root / "deployment.lock")
                candidates.add(original.data_root / "daemon.lock")
            for path in sorted(candidates):
                path.parent.mkdir(parents=True, exist_ok=True)
                locks.enter_context(FileLock(path, timeout=0))
            binding.coordination_root.mkdir(parents=True, exist_ok=True)
            entries = list(local_author_workspaces(owner.root))
            previous = next(
                (item for item in entries if item.root == project.root), None
            )
            if previous_location is not None and (
                (previous is None and not previous_location.pending)
                or (
                    previous is not None
                    and previous.id != previous_location.workspace_id
                )
                or (identity is not None and identity != previous_location.workspace_id)
            ):
                raise ValueError(
                    "Existing locator and application registry disagree; "
                    "restore the registered owner"
                )
            interpreter = python or (
                previous.python if previous else Path(sys.executable).absolute()
            )
            candidate = capture(project.root, interpreter)
            check(candidate.manifest, interpreter)
            location = project.root / RUNTIME_BINDING_NAME
            if location.exists() and (
                original.data_root != binding.data_root
                or original.deployment_root != binding.deployment_root
                or original.coordination_root != binding.coordination_root
                or original.materialization_root != binding.materialization_root
                or original.settings_file != binding.settings_file
            ):
                raise ValueError(
                    "Workspace already has a different explicit runtime binding"
                )
            if (
                owner.root != project.root
                and not location.exists()
                and (original.data_root / "control.sqlite3").exists()
            ):
                raise ValueError(
                    "Workspace has its own scientific store; "
                    "do not rebind it implicitly"
                )
            if identity is not None:
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
            if (
                previous is not None
                and identity is not None
                and previous.id != identity
            ):
                raise ValueError(
                    "This location already belongs to another source owner"
                )
            if previous_location is not None and (
                read_data_identity(binding.data_root) != previous_location.project_id
            ):
                raise ValueError("Registered application data identity has changed")
            project_id = _registration_data_identity(binding)
            selected = LocalAuthorWorkspace(
                id=identity
                or (
                    previous.id
                    if previous
                    else previous_location.workspace_id
                    if previous_location
                    else uuid4().hex
                ),
                name=name or project.root.name,
                root=project.root,
                python=interpreter,
                retained_pythons=tuple(
                    dict.fromkeys((previous.python, *previous.retained_pythons))
                )
                if previous
                else (),
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
            association = None
            temporary_locator = locator.with_suffix(".tmp")
            if project.root != owner.root:
                association = AuthorLocation(
                    workspace=project.root,
                    application=owner.root,
                    workspace_id=selected.id,
                    project_id=project_id,
                )
                # Publish an owner fence before the registry. Interrupted commits
                # cannot let a second application claim this author directory.
                temporary_locator.write_text(
                    association.model_copy(update={"pending": True}).model_dump_json(
                        indent=2
                    ),
                    encoding="utf-8",
                )
                temporary_locator.replace(locator)
            temporary.replace(path)
            if association is not None:
                temporary_locator.write_text(
                    association.model_dump_json(indent=2), encoding="utf-8"
                )
                temporary_locator.replace(locator)
            return selected
    except Timeout as error:
        raise ValueError(
            "Source registration is busy or the source owns another running application"
        ) from error


def _registration_data_identity(binding: RuntimeBinding) -> str:
    """Explicit registration initializes only its selected empty owner, never source."""
    from scopecat_server.storage.sqlite.connection import SQLiteDatabase
    from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

    database = binding.data_root / "control.sqlite3"
    if not database.exists():
        with (
            FileLock(binding.deployment_root / "deployment.lock", timeout=0),
            FileLock(binding.data_root / "daemon.lock", timeout=0),
        ):
            # Another starter may have initialized the store before we took locks.
            if not database.exists():
                store = SQLiteProjectStore(
                    SQLiteDatabase(database), binding.data_root / "objects"
                )
                try:
                    store.bootstrap()
                finally:
                    store.close()
    return read_data_identity(binding.data_root)
