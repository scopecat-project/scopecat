"""Resolve registered publication owners without changing deployment ownership."""

import sys
from pathlib import Path
from typing import cast

from scopecat.author_workspaces import local_author_workspaces
from scopecat.project_sources import require_environment
from scopecat.records.author_workspace import (
    SERVICE_AUTHOR_WORKSPACE,
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
        self.workers = RevisionWorkers()
        original = AuthorRevisionService(root, store, workers=self.workers)
        self.services = {SERVICE_AUTHOR_WORKSPACE: original}
        self.unavailable: dict[str, str] = {}
        binding = load_runtime_binding(root)
        try:
            for item in local_author_workspaces(root):
                with store.sqlite.write_transaction() as connection:
                    connection.execute(
                        "INSERT INTO author_workspaces VALUES (?, ?) "
                        "ON CONFLICT(workspace_id) DO UPDATE SET name=excluded.name",
                        (item.id, item.name),
                    )
                service = None
                try:
                    other_binding = load_runtime_binding(item.root)
                    if item.python != Path(sys.executable).absolute() or (
                        other_binding.data_root,
                        other_binding.deployment_root,
                    ) != (binding.data_root, binding.deployment_root):
                        raise ValueError(
                            "Registered author workspace has a different runtime "
                            "binding"
                        )
                    service = AuthorRevisionService(
                        item.root, store, workspace_id=item.id, workers=self.workers
                    )
                    if (
                        original.baseline is None
                        or service.baseline is None
                        or original.baseline.manifest.maintenance_hash
                        != service.baseline.manifest.maintenance_hash
                    ):
                        raise ValueError(
                            "Registered author workspace is unavailable or has a "
                            "different maintained composition"
                        )
                    require_environment(service.baseline.manifest)
                    self.services[item.id] = service
                except (OSError, ValueError) as error:
                    if service is not None:
                        service.close()
                    self.unavailable[item.id] = str(error)
        except BaseException:
            self.close()
            raise

    def catalog(self) -> AuthorWorkspaceCatalog:
        """List retained owners without reading, importing or publishing source."""
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
        if identity in self.unavailable:
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
        return {
            str(service.root): identity for identity, service in self.services.items()
        }

    def close(self) -> None:
        for service in self.services.values():
            service.request_stop()
        for service in self.services.values():
            service.close()
        self.workers.close()
