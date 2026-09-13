"""Bounded revision-pinned author processes; requests never retry implicitly."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from queue import Empty, Queue
from typing import Literal, TextIO, cast

import psutil
from scopecat.records.launch_request import LaunchRequest

from scopecat_server.retained_request import AnalysisCall, ComparisonCall
from scopecat_server.validation_process import terminate_validation_process_tree


class _Worker:
    def __init__(self, root: Path, revision: str, module: str) -> None:
        self.stderr: TextIO = tempfile.TemporaryFile(mode="w+", encoding="utf-8")  # noqa: SIM115 - worker owns lifetime
        self.process: subprocess.Popen[str] = subprocess.Popen(  # noqa: S603 - fixed internal worker, no shell
            [
                sys.executable,
                "-m",
                module,
                str(root),
                "--serve",
                revision,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.owner = psutil.Process(self.process.pid)
        self.responses: Queue[str] = Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        stdout = cast("TextIO", self.process.stdout)
        try:
            for line in stdout:
                self.responses.put(line)
        finally:
            self.responses.put("")

    def diagnostics(self) -> str:
        self.stderr.seek(0)
        return self.stderr.read()[-8192:]

    def call(
        self, command: LaunchRequest | AnalysisCall | ComparisonCall, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        assert self.process.stdin is not None
        self.stderr.seek(0)
        self.stderr.truncate()
        try:
            self.process.stdin.write(command.model_dump_json() + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            self.process.wait(timeout=5)
            return subprocess.CompletedProcess(
                self.process.args, self.process.returncode or 1, "", self.diagnostics()
            )
        try:
            response = self.responses.get(timeout=timeout)
        except Empty:
            raise subprocess.TimeoutExpired(
                self.process.args, timeout, stderr=self.diagnostics()
            ) from None
        if not response:
            self.process.wait(timeout=5)
        return subprocess.CompletedProcess(
            self.process.args,
            0 if response else self.process.returncode or 1,
            response,
            self.diagnostics(),
        )

    def close(self) -> None:
        if self.process.stdin is not None:
            with suppress(BrokenPipeError):
                self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            terminate_validation_process_tree(self.process, owner=self.owner)
        self.reader.join(timeout=2)
        if self.process.stdout is not None:
            self.process.stdout.close()
        self.stderr.close()


class RevisionWorkers:
    """Serialize short author calls and retain at most two isolated revisions.

    Each invocation opens its own lab connection. A failed process is discarded;
    the caller receives the original failure, including ambiguous submissions.
    """

    def __init__(
        self,
        module: Literal[
            "scopecat_server.launch_worker", "scopecat_server.retained_worker"
        ] = "scopecat_server.launch_worker",
    ) -> None:
        self._module = module
        self._workers: OrderedDict[str, _Worker] = OrderedDict()
        self._lock = threading.Lock()

    def call(
        self,
        root: Path,
        command: LaunchRequest | AnalysisCall | ComparisonCall,
        *,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[str]:
        assert command.code_revision is not None
        key = command.code_revision.content_hash
        started = time.monotonic()
        if not self._lock.acquire(timeout=timeout):
            raise subprocess.TimeoutExpired("author worker queue", timeout)
        try:
            worker = self._workers.get(key)
            if worker is None:
                if len(self._workers) == 2:
                    _, evicted = self._workers.popitem(last=False)
                    evicted.close()
                worker = _Worker(root, key, self._module)
                self._workers[key] = worker
            self._workers.move_to_end(key)
            try:
                result = worker.call(
                    command, max(0.001, timeout - (time.monotonic() - started))
                )
            except BaseException:
                self._workers.pop(key).close()
                raise
            if result.returncode:
                self._workers.pop(key).close()
            return result
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            for worker in self._workers.values():
                worker.close()
            self._workers.clear()
