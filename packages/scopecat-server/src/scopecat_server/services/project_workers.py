"""Process wakeups for durable, project-owned GUI procedures."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Lock


class ProjectProcedureWorkers:
    def __init__(self, root: Callable[[], Path]) -> None:
        self.root = root
        self._lock = Lock()
        self._children: dict[str, subprocess.Popen[bytes]] = {}

    def dispatch(self, procedure_id: str) -> None:
        with self._lock:
            self._children = {
                key: child
                for key, child in self._children.items()
                if child.poll() is None
            }
            if procedure_id in self._children:
                return
            root = self.root()
            log_path = root / ".scopecat" / "console-worker.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as log:
                self._children[procedure_id] = subprocess.Popen(  # noqa: S603 - fixed interpreter and module; no shell
                    [
                        sys.executable,
                        "-m",
                        "scopecat.application.launch_worker",
                        str(root),
                        "--procedure",
                        procedure_id,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
