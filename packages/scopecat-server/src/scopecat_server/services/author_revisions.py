"""Validate author changes in fresh processes and atomically publish complete code."""

from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scopecat.project import load_project
from scopecat.project_sources import (
    capture_sources,
    materialize_sources,
    require_environment,
)
from scopecat.records.author_revision import (
    AuthorPreparation,
    AuthorPreparationRequest,
    AuthorRevisionBundle,
    AuthorRevisionRef,
    AuthorRevisionState,
)
from scopecat.runtime_binding import load_runtime_binding

from scopecat_server.services.revision_workers import (
    AuthorValidationCancelled,
    AuthorWorkerBinding,
    RevisionWorkers,
)
from scopecat_server.storage.sqlite.author_revision_repository import (
    AuthorRevisionConflict,
    AuthorRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

_LOGGER = logging.getLogger(__name__)


class AuthorRevisionService:
    def __init__(
        self,
        root: Path,
        store: SQLiteProjectStore,
        *,
        workspace_id: str = "legacy",
        workers: RevisionWorkers | None = None,
    ) -> None:
        self.worker_binding = AuthorWorkerBinding(
            root.resolve(), Path(sys.executable).absolute()
        )
        self._owns_workers = workers is None
        self.workers = workers or RevisionWorkers()
        self.root = root
        self.repository = AuthorRevisionRepository(store, workspace_id)
        self._operation_lock = threading.RLock()
        self._operations: dict[str, tuple[threading.Event, Future[None]]] = {}
        self._closing = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="author-preparation"
        )
        for operation in self.repository.preparations(pending_only=True):
            if not operation.terminal:
                self.repository.save_preparation(
                    operation.model_copy(
                        update={
                            "status": "interrupted",
                            "phase": "daemon restarted",
                            "error": (
                                "Daemon restarted before preparation completed; "
                                "explicitly start a new operation."
                            ),
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
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
        if state.active is None:
            latest = self.repository.latest_preparation(state.generation)
            if latest is not None:
                state = state.model_copy(update={"preparation_id": latest.operation_id})
        if initialize and state.active is None:
            if state.preparation_id is not None:
                return self.wait(state.preparation_id)
            assert self.project is not None
            bundle = capture_sources(self.project)
            operation = self._start(
                bundle,
                AuthorPreparationRequest(
                    operation_id="initialize-"
                    + bundle.manifest.ref.content_hash.replace(":", "-"),
                    expected_generation=0,
                ),
            )
            try:
                return self.wait(operation.operation_id)
            except ValueError:
                state = self.repository.state()
                if state.active is None:
                    raise
        return state

    def start(self, request: AuthorPreparationRequest) -> AuthorPreparation:
        # Resolve retry identity before touching today's mutable source tree.
        with self._operation_lock:
            try:
                operation = self.repository.preparation(request.operation_id)
            except KeyError:
                pass
            else:
                if operation.expected_generation != request.expected_generation:
                    raise AuthorRevisionConflict(
                        "operation identity already has another request"
                    )
                return operation
            if self.project is None or self.baseline is None:
                raise ValueError("project has no configured author source roots")
            return self._start(capture_sources(self.project), request)

    def _start(
        self, bundle: AuthorRevisionBundle, request: AuthorPreparationRequest
    ) -> AuthorPreparation:
        with self._operation_lock:
            if self._closing:
                raise ValueError("daemon is shutting down")
            try:
                return self.repository.preparation(request.operation_id)
            except KeyError:
                pass
            self._require_maintenance(bundle)
            now = datetime.now(UTC)
            operation = AuthorPreparation(
                operation_id=request.operation_id,
                expected_generation=request.expected_generation,
                code_revision=bundle.manifest.ref,
                status="queued",
                phase="queued",
                created_at=now,
                updated_at=now,
            )
            self.repository.save_preparation(operation)
            cancelled = threading.Event()
            future = self._executor.submit(self._execute, operation, bundle, cancelled)
            self._operations[operation.operation_id] = (cancelled, future)
            return operation

    def _execute(
        self,
        operation: AuthorPreparation,
        bundle: AuthorRevisionBundle,
        cancelled: threading.Event,
    ) -> None:
        identity = operation.operation_id

        def progress(phase: str) -> None:
            with self._operation_lock:
                current = self.repository.preparation(identity)
                if not current.terminal and current.status != "cancelling":
                    self.repository.save_preparation(
                        current.model_copy(
                            update={
                                "status": "running",
                                "phase": phase,
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )

        def publish() -> AuthorRevisionState:
            # Cancellation and publication share one decision lock. Persist the
            # success receipt in the same SQLite transaction as the active pointer.
            with self._operation_lock:
                if cancelled.is_set():
                    raise AuthorValidationCancelled("Author preparation cancelled")
                return self.repository.publish(
                    bundle,
                    expected_generation=operation.expected_generation,
                    operation=operation,
                )

        try:
            if cancelled.is_set():
                raise AuthorValidationCancelled("Author preparation cancelled")
            progress("materializing captured source")
            code_root = materialize_sources(
                bundle, load_runtime_binding(self.root).data_root / "code"
            )
            self.workers.publish_validated(
                self.worker_binding,
                code_root,
                bundle.manifest.ref,
                publish,
                cancelled=cancelled,
                on_progress=progress,
            )
        except Exception as error:
            with self._operation_lock:
                current = self.repository.preparation(identity)
                if not current.terminal:
                    self.repository.save_preparation(
                        current.model_copy(
                            update={
                                "status": "cancelled"
                                if isinstance(error, AuthorValidationCancelled)
                                else "failed",
                                "phase": "cancelled"
                                if isinstance(error, AuthorValidationCancelled)
                                else current.phase,
                                "error": str(error)[-8192:],
                                "error_type": type(error).__name__,
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    )
            _LOGGER.info(
                "Author preparation %s ended: %s", identity, str(error)[-8192:]
            )
        finally:
            with self._operation_lock:
                self._operations.pop(identity, None)

    def cancel(self, operation_id: str) -> AuthorPreparation:
        with self._operation_lock:
            operation = self.repository.preparation(operation_id)
            if not operation.terminal:
                cancelled, future = self._operations[operation_id]
                queued = future.cancel()
                operation = operation.model_copy(
                    update={
                        "status": "cancelled" if queued else "cancelling",
                        "phase": "cancelled" if queued else operation.phase,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self.repository.save_preparation(operation)
                cancelled.set()
                if queued:
                    self._operations.pop(operation_id)
            return operation

    def wait(self, operation_id: str) -> AuthorRevisionState:
        while True:
            operation = self.repository.preparation(operation_id)
            if operation.result is not None:
                with self._operation_lock:
                    pending = self._operations.get(operation_id)
                if pending is not None:
                    pending[1].result()
                return operation.result
            if operation.terminal:
                if operation.error_type == "AuthorRevisionConflict":
                    raise AuthorRevisionConflict(
                        operation.error or "Concurrent author publication"
                    )
                raise ValueError(operation.error or operation.status)
            time.sleep(0.05)

    def refresh(self, *, expected_generation: int) -> AuthorRevisionState:
        operation = self.start(
            AuthorPreparationRequest(
                operation_id=uuid4().hex,
                expected_generation=expected_generation,
            )
        )
        return self.wait(operation.operation_id)

    def request_stop(self) -> None:
        """Release preparation waiters before HTTP graceful shutdown waits for them."""
        with self._operation_lock:
            self._closing = True
            threads = tuple(self._operations.values())
            for cancelled, _ in threads:
                cancelled.set()

    def close(self) -> None:
        self.request_stop()
        self._executor.shutdown(wait=True)
        if self._owns_workers:
            self.workers.close()

    def get(self, ref: AuthorRevisionRef) -> AuthorRevisionBundle:
        try:
            bundle = self.repository.get(ref)
        except KeyError as error:
            raise ValueError(
                "Source revision does not belong to the selected author workspace"
            ) from error
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
