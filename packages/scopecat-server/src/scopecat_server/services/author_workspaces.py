"""Resolve registered publication owners without changing deployment ownership."""

from pathlib import Path
from threading import RLock
from typing import cast

from scopecat.author_workspaces import LocalAuthorWorkspace, local_author_workspaces
from scopecat.records.author_workspace import (
    AuthorWorkspaceCatalog,
    AuthorWorkspaceSummary,
)
from scopecat.runtime_binding import load_runtime_binding

from scopecat_server.services.author_revisions import AuthorRevisionService
from scopecat_server.services.revision_workers import RevisionWorkers
from scopecat_server.storage.sqlite.author_revision_repository import (
    AuthorRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


class AuthorWorkspaceServices:
    def __init__(self, root: Path, store: SQLiteProjectStore) -> None:
        self.store = store
        self.root = root
        self.workers = RevisionWorkers()
        self.services: dict[str, AuthorRevisionService] = {}
        self.unavailable: dict[str, str] = {}
        self.binding = load_runtime_binding(root)
        self._load_lock = RLock()
        try:
            self._refresh()
        except BaseException:
            self.close()
            raise

    def _refresh(self) -> None:
        """Discover explicitly registered folders without replacing live services."""
        with self._load_lock:
            for item in local_author_workspaces(self.root):
                if item.id in self.services or item.id in self.unavailable:
                    continue
                with self.store.sqlite.write_transaction() as connection:
                    connection.execute(
                        "INSERT INTO author_workspaces VALUES (?, ?) "
                        "ON CONFLICT(workspace_id) DO UPDATE SET name=excluded.name",
                        (item.id, item.name),
                    )
                try:
                    self._load(item)
                except (OSError, ValueError) as error:
                    self.unavailable[item.id] = str(error)

    def _load(self, item: LocalAuthorWorkspace) -> AuthorRevisionService:
        binding = load_runtime_binding(item.root)
        if (binding.data_root, binding.deployment_root) != (
            self.binding.data_root,
            self.binding.deployment_root,
        ):
            raise ValueError(
                "Registered author workspace has a different runtime binding"
            )
        service = AuthorRevisionService(
            item.root,
            self.store,
            workspace_id=item.id,
            workers=self.workers,
            python=item.python,
        )
        if service.baseline is None:
            service.close()
            raise ValueError("Registered author workspace has no source roots")
        self.services[item.id] = service
        self.unavailable.pop(item.id, None)
        return service

    def catalog(self) -> AuthorWorkspaceCatalog:
        """Discover new registrations, then list retained publication owners."""
        self._refresh()
        with self.store.sqlite.read_connection() as connection:
            rows = cast(
                "list[tuple[str, str]]",
                connection.execute(
                    "SELECT workspace_id, name FROM author_workspaces "
                    "ORDER BY workspace_id"
                ).fetchall(),
            )
        return AuthorWorkspaceCatalog(
            items=tuple(
                AuthorWorkspaceSummary(
                    id=identity,
                    name=name,
                    available=identity in self.services,
                    unavailable_reason=(
                        None
                        if identity in self.services
                        else self.unavailable.get(
                            identity,
                            "Author workspace is not registered for this deployment",
                        )
                    ),
                )
                for identity, name in rows
            )
        )

    def get(self, identity: str) -> AuthorRevisionService:
        self._refresh()
        with self._load_lock:
            if identity in self.unavailable:
                for item in local_author_workspaces(self.root):
                    if item.id == identity:
                        return self._load(item)
                raise ValueError(self.unavailable[identity])
        try:
            return self.services[identity]
        except KeyError:
            raise ValueError(
                "Author workspace is not registered for this deployment"
            ) from None

    def repository(self, identity: str) -> AuthorRevisionRepository:
        with self.store.sqlite.read_connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM author_workspaces WHERE workspace_id=?", (identity,)
                ).fetchone()
                is None
            ):
                raise ValueError("Unknown retained author workspace")
        return AuthorRevisionRepository(self.store, identity)

    @property
    def roots(self) -> dict[str, str]:
        # Endpoint verification must recognize a new registration before its
        # first catalog request, without importing source from a health probe.
        return {str(item.root): item.id for item in local_author_workspaces(self.root)}

    def close(self) -> None:
        self.request_stop()
        for service in self.services.values():
            service.close()
        self.workers.close()

    def request_stop(self) -> None:
        for service in self.services.values():
            service.request_stop()
