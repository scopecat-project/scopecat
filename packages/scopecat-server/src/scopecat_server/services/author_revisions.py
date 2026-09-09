"""Validate author changes in fresh processes and atomically publish complete code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scopecat.project import load_project
from scopecat.project_sources import (
    capture_sources,
    materialize_sources,
    require_environment,
)
from scopecat.records.author_revision import (
    AuthorRevisionBundle,
    AuthorRevisionRef,
    AuthorRevisionState,
)

from scopecat_server.storage.sqlite.author_revision_repository import (
    AuthorRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


class AuthorRevisionService:
    def __init__(self, root: Path, store: SQLiteProjectStore) -> None:
        self.root = root
        self.repository = AuthorRevisionRepository(store)
        manifest = root / "scopecat.toml"
        self.project = load_project(manifest) if manifest.is_file() else None
        self.baseline = (
            capture_sources(self.project)
            if self.project is not None and self.project.source_roots
            else None
        )

    def state(self, *, initialize: bool = True) -> AuthorRevisionState:
        if self.baseline is None:
            return AuthorRevisionState()
        state = self.repository.state()
        if initialize and state.active is None:
            try:
                return self.refresh(expected_generation=0)
            except ValueError:
                # A concurrent first reader may have published while validating.
                state = self.repository.state()
                if state.active is None:
                    raise
        return state

    def refresh(self, *, expected_generation: int) -> AuthorRevisionState:
        if self.project is None or self.baseline is None:
            raise ValueError("project has no configured author source roots")
        bundle = capture_sources(self.project)
        self._require_maintenance(bundle)
        code_root = materialize_sources(bundle, self.root / ".scopecat" / "code")
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and internal validation worker
            [
                sys.executable,
                "-m",
                "scopecat_server.author_worker",
                str(self.root),
                "--validate",
                str(code_root),
            ],
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            raise ValueError(completed.stderr.strip() or "author validation failed")
        return self.repository.publish(bundle, expected_generation=expected_generation)

    def get(self, ref: AuthorRevisionRef) -> AuthorRevisionBundle:
        bundle = self.repository.get(ref)
        self._require_maintenance(bundle)
        require_environment(bundle.manifest)
        return bundle

    def _require_maintenance(self, bundle: AuthorRevisionBundle) -> None:
        if (
            self.baseline is None
            or bundle.manifest.maintenance_hash
            != self.baseline.manifest.maintenance_hash
        ):
            raise ValueError(
                "compiler, driver or maintained composition changed; "
                "restart with the matching maintained source and environment "
                "before using this revision"
            )
