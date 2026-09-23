"""Bounded process management for explicitly dispatched GUI procedures.

Only ready durable procedures may start. Waiting input consumes no process;
failed processes are paused until an explicit dispatch, never retried in a loop.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Literal, cast

from scopecat.daemon.procedure_views import (
    ProcedureDispatchView,
    ProcedureWorkerFailure,
)
from scopecat.kernel.interaction_timing import record_timing
from scopecat.runtime_binding import load_runtime_binding

_LOG = logging.getLogger(__name__)


class ProcedureDispatchError(RuntimeError):
    """One admitted procedure could not reach its worker."""


class ProjectProcedureWorkers:
    def __init__(
        self,
        root: Callable[[], Path],
        state: Callable[[str], str],
        *,
        max_workers: int = 2,
        resolve_root: Callable[[str], Path] | None = None,
    ) -> None:
        self.root = root
        self.resolve_root = resolve_root
        self.state = state
        self.max_workers = max_workers
        self._lock = Lock()
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._managed: dict[str, str] | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    def _path(self) -> Path:
        return load_runtime_binding(self.root()).data_root / "console-procedures.json"

    def _worker_dir(self, procedure_id: str) -> Path:
        key = sha256(procedure_id.encode("utf-8")).hexdigest()
        return self._path().parent / "procedure-workers" / key

    def _failure(self, procedure_id: str) -> ProcedureWorkerFailure | None:
        path = self._worker_dir(procedure_id) / "failure.json"
        return (
            ProcedureWorkerFailure.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def _record_failure(
        self, procedure_id: str, failure: ProcedureWorkerFailure
    ) -> None:
        path = self._worker_dir(procedure_id) / "failure.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(failure.model_dump_json(), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _exit_failure(code: int) -> ProcedureWorkerFailure:
        return ProcedureWorkerFailure(
            kind="process_exit",
            message=f"Worker exited with code {code}.",
            observed_at=datetime.now(UTC),
            exit_code=code,
        )

    def _load(self) -> dict[str, str]:
        if self._managed is None:
            path = self._path()
            self._managed = (
                cast("dict[str, str]", json.loads(path.read_text(encoding="utf-8")))
                if path.exists()
                else {}
            )
        return self._managed

    def _save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._managed), encoding="utf-8")
        temporary.replace(path)

    def start(self) -> None:
        self._stop.clear()
        self._thread = Thread(target=self._run, name="console-procedures", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        # Do not kill hardware workers. Daemon shutdown owns run finalization;
        # disconnected workers exit through their normal execution error path.

    def _run(self) -> None:
        while not self._stop.wait(1):
            try:
                self.tick()
            except Exception:
                _LOG.exception("Console procedure management failed")

    def snapshot(self, procedure_id: str) -> ProcedureDispatchView:
        with self._lock:
            management = self._load().get(procedure_id, "unmanaged")
            child = self._children.get(procedure_id)
            code = child.poll() if child is not None else None
            failure = self._failure(procedure_id)
            # Report an observed failed exit immediately; tick persists it.
            if child is not None and code is not None and code != 0:
                management = "paused"
                failure = self._exit_failure(code)
            log_path = self._worker_dir(procedure_id) / "worker.log"
            return ProcedureDispatchView(
                management=cast("Literal['unmanaged', 'active', 'paused']", management),
                worker_running=child is not None and code is None,
                failure=failure,
                log_path=str(log_path) if log_path.exists() else None,
            )

    def dispatch(self, procedure_id: str) -> None:
        with self._lock:
            managed = self._load()
            child = self._children.get(procedure_id)
            if child is not None and child.poll() is not None:
                del self._children[procedure_id]
            managed[procedure_id] = "active"
            (self._worker_dir(procedure_id) / "failure.json").unlink(missing_ok=True)
            self._save()
            errors = self._tick()
            error = errors.get(procedure_id)
            if error is not None:
                raise ProcedureDispatchError(str(error)) from error

    def tick(self) -> None:
        with self._lock:
            self._load()
            self._tick()

    def manage(self, procedure_id: str) -> None:
        """Recover task handoffs without clearing a failed worker's pause."""
        with self._lock:
            managed = self._load()
            if procedure_id not in managed:
                managed[procedure_id] = "active"
                self._save()
            # The worker loop scans once per tick, not once per queued task stage.

    def _tick(self) -> dict[str, Exception]:
        managed = self._load()
        errors: dict[str, Exception] = {}
        for key, child in tuple(self._children.items()):
            code = child.poll()
            if code is not None:
                del self._children[key]
                if code != 0 and key in managed:
                    self._record_failure(key, self._exit_failure(code))
                    managed[key] = "paused"
                    self._save()
        for key in tuple(managed):
            try:
                state = self.state(key)
                if (
                    state == "ready"
                    and managed[key] != "paused"
                    and key not in self._children
                ):
                    if len(self._children) >= self.max_workers:
                        break
                    self._spawn(key)
            except Exception as error:
                # A broken workspace or one failed spawn must not prevent
                # independent admitted procedures from reaching their workers.
                if managed[key] == "paused":
                    continue
                managed[key] = "paused"
                self._record_failure(
                    key,
                    ProcedureWorkerFailure(
                        kind="dispatch",
                        message=str(error),
                        observed_at=datetime.now(UTC),
                    ),
                )
                self._save()
                errors[key] = error
                _LOG.exception("Procedure worker dispatch paused: %s", key)
                continue
            if state in {"closed", "attention_required"}:
                del managed[key]
                self._save()
        return errors

    def _spawn(self, procedure_id: str) -> None:
        root = self.resolve_root(procedure_id) if self.resolve_root else self.root()
        log_path = self._worker_dir(procedure_id) / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record_timing("procedure_dispatch", procedure_id=procedure_id)
        with log_path.open("ab") as log:
            self._children[procedure_id] = subprocess.Popen(  # noqa: S603 - fixed interpreter and module; no shell
                [
                    sys.executable,
                    "-m",
                    "scopecat_server.procedure_worker",
                    str(root),
                    procedure_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
