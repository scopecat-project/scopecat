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
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

_LOG = logging.getLogger(__name__)


class ProcedureDispatchView(BaseModel):
    """Observation of existing process management, not execution authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    management: Literal["unmanaged", "active", "paused"]
    worker_running: bool


class ProjectProcedureWorkers:
    def __init__(
        self,
        root: Callable[[], Path],
        state: Callable[[str], str],
        *,
        max_workers: int = 2,
    ) -> None:
        self.root = root
        self.state = state
        self.max_workers = max_workers
        self._lock = Lock()
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._managed: dict[str, str] | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    def _path(self) -> Path:
        return self.root() / ".scopecat" / "console-procedures.json"

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
            # Report an observed failed exit immediately; tick persists it.
            if child is not None and code is not None and code != 0:
                management = "paused"
            return ProcedureDispatchView(
                management=cast("Literal['unmanaged', 'active', 'paused']", management),
                worker_running=child is not None and code is None,
            )

    def dispatch(self, procedure_id: str) -> None:
        with self._lock:
            managed = self._load()
            child = self._children.get(procedure_id)
            if child is not None and child.poll() is not None:
                del self._children[procedure_id]
            managed[procedure_id] = "active"
            self._save()
            self._tick()

    def tick(self) -> None:
        with self._lock:
            self._load()
            self._tick()

    def _tick(self) -> None:
        managed = self._load()
        for key, child in tuple(self._children.items()):
            code = child.poll()
            if code is not None:
                del self._children[key]
                if code != 0 and key in managed:
                    managed[key] = "paused"
                    self._save()
        for key in tuple(managed):
            state = self.state(key)
            if state in {"closed", "attention_required"}:
                del managed[key]
                self._save()
                continue
            if state != "ready" or managed[key] == "paused" or key in self._children:
                continue
            if len(self._children) >= self.max_workers:
                break
            try:
                self._spawn(key)
            except OSError:
                managed[key] = "paused"
                self._save()
                raise

    def _spawn(self, procedure_id: str) -> None:
        root = self.root()
        log_path = root / ".scopecat" / "console-worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            self._children[procedure_id] = subprocess.Popen(  # noqa: S603 - fixed interpreter and module; no shell
                [
                    sys.executable,
                    "-m",
                    "scopecat_server.launch_worker",
                    str(root),
                    "--procedure",
                    procedure_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
