"""Validate author changes in fresh processes and atomically publish complete code."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import psutil
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
from scopecat_server.validation_process import terminate_validation_process_tree
from scopecat_server.worker_diagnostics import diagnostic_excerpt

_LOGGER = logging.getLogger(__name__)


class AuthorValidationTimeout(TimeoutError):
    """Validation did not publish a revision; bounded evidence is in daemon.log."""


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
        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter and internal validation worker
            [
                sys.executable,
                "-m",
                "scopecat_server.author_worker",
                str(self.root),
                "--validate",
                str(code_root),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        owner: psutil.Process | None = None
        try:
            with suppress(psutil.NoSuchProcess):
                owner = psutil.Process(process.pid)
            _, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired as error:
            elapsed = time.monotonic() - started
            # Keep the launcher alive until its owned descendants are captured.
            captured_stderr = _stop_validation(process, owner, stderr=error.stderr)
            stage, evidence = diagnostic_excerpt(captured_stderr)
            _LOGGER.error(  # noqa: TRY400 - retain bounded worker evidence only
                "Author source validation timed out: revision=%s stage=%s "
                "elapsed=%.3fs\n%s",
                bundle.manifest.ref.content_hash,
                stage,
                elapsed,
                evidence,
            )
            raise AuthorValidationTimeout(
                f"Author source validation timed out after 60 seconds during {stage}. "
                "This attempt did not publish a revision. Inspect bounded validation "
                "evidence in .scopecat/daemon.log. Ask the project maintainer to check "
                "source imports and environment before explicitly refreshing again. "
                "A running daemon does not mean the author catalog is ready."
            ) from error
        except BaseException:
            _stop_validation(process, owner, stderr=None)
            raise
        if process.returncode:
            raise ValueError(stderr.strip() or "author validation failed")
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


def _stop_validation(
    process: subprocess.Popen[str],
    owner: psutil.Process | None,
    *,
    stderr: str | bytes | None,
) -> str | bytes | None:
    """Never mask the original timeout/interruption with cleanup or pipe errors."""
    try:
        terminate_validation_process_tree(process, owner=owner)
    except RuntimeError as cleanup_error:
        _LOGGER.error("%s", str(cleanup_error)[:4096])  # noqa: TRY400
    try:
        _, captured = process.communicate(timeout=5)
        return captured
    except subprocess.TimeoutExpired as error:
        _LOGGER.error("Validation stderr drain timed out after cleanup")  # noqa: TRY400
        return error.stderr if error.stderr is not None else stderr
    except (OSError, UnicodeError) as error:
        _LOGGER.error("Validation stderr drain failed: %s", str(error)[:4096])  # noqa: TRY400
        return stderr
