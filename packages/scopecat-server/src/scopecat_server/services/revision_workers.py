"""Bounded revision-pinned author processes; requests never retry implicitly."""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from queue import Empty, Queue
from typing import Literal, TextIO, cast

import psutil
from scopecat.records.author_revision import AuthorRevisionRef, AuthorRevisionState
from scopecat.records.launch_request import LaunchRequest

from scopecat_server.retained_request import AnalysisCall, ComparisonCall
from scopecat_server.validation_process import terminate_validation_process_tree


class _Worker:
    def __init__(
        self, root: Path, revision: str, module: str, *, code_root: Path | None = None
    ) -> None:
        self.stderr: TextIO = tempfile.TemporaryFile(mode="w+", encoding="utf-8")  # noqa: SIM115 - worker owns lifetime
        self.process: subprocess.Popen[str] = subprocess.Popen(  # noqa: S603 - fixed internal worker, no shell
            [
                sys.executable,
                "-m",
                module,
                str(root),
                *(
                    ["--serve", revision]
                    if code_root is None
                    else ["--validate-serve", str(code_root), revision]
                ),
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
        return self.receive(timeout)

    def receive(self, timeout: float) -> subprocess.CompletedProcess[str]:
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
    """Serialize calls per revision and retain at most two isolated revisions.

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
        self._condition = threading.Condition(threading.Lock())
        self._busy: set[str] = set()
        self._validation_lock = threading.Lock()

    def publish_validated(
        self,
        root: Path,
        code_root: Path,
        ref: AuthorRevisionRef,
        publish: Callable[[], AuthorRevisionState],
        *,
        timeout: float = 60,
    ) -> AuthorRevisionState:
        """Own one unpublished candidate; transfer only after successful CAS.

        Validation does not hold the pool lock. Publication and adoption do,
        so a first caller cannot race us into loading the published source again.
        An equivalent warm worker wins over the freshly validated candidate.
        """
        started = time.monotonic()
        if not self._validation_lock.acquire(timeout=timeout):
            raise subprocess.TimeoutExpired("author validation queue", timeout)
        candidate = None
        try:
            candidate = _Worker(
                root,
                ref.content_hash,
                "scopecat_server.validation_worker",
                code_root=code_root,
            )
            result = candidate.receive(
                max(0.001, timeout - (time.monotonic() - started))
            )
            if result.returncode:
                raise ValueError(result.stderr.strip() or "author validation failed")
            logging.getLogger(__name__).info(
                "Validated author candidate revision=%s seconds=%.3f diagnostics=%s",
                ref.content_hash,
                time.monotonic() - started,
                result.stderr.strip(),
            )
            if AuthorRevisionRef.model_validate_json(result.stdout) != ref:
                raise ValueError(
                    "validated worker returned a different source revision"
                )
            if not self._condition.acquire(
                timeout=max(0, timeout - (time.monotonic() - started))
            ):
                raise subprocess.TimeoutExpired(
                    "author publication queue", timeout, stderr=result.stderr
                )
            try:
                key = ref.content_hash
                if not self._condition.wait_for(
                    lambda: self._has_capacity(key),
                    timeout=max(0, timeout - (time.monotonic() - started)),
                ):
                    raise subprocess.TimeoutExpired(
                        "author publication queue", timeout, stderr=result.stderr
                    )
                state = publish()
                if key not in self._workers:
                    self._evict_idle()
                    self._workers[key] = candidate
                    candidate = None
                self._workers.move_to_end(key)
                return state
            finally:
                self._condition.release()
        finally:
            try:
                if candidate is not None:
                    candidate.close()
            except RuntimeError as error:
                logging.getLogger(__name__).error(  # noqa: TRY400 - bounded cleanup evidence
                    "Candidate cleanup failed: %s", str(error)[:4096]
                )
            finally:
                self._validation_lock.release()

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
        if not self._condition.acquire(timeout=timeout):
            raise subprocess.TimeoutExpired("author worker queue", timeout)
        try:
            if not self._condition.wait_for(
                lambda: key not in self._busy and self._has_capacity(key),
                timeout=max(0, timeout - (time.monotonic() - started)),
            ):
                raise subprocess.TimeoutExpired("author worker queue", timeout)
            worker = self._workers.get(key)
            if worker is None:
                self._evict_idle()
                worker = _Worker(root, key, self._module)
                self._workers[key] = worker
            self._workers.move_to_end(key)
            self._busy.add(key)
        finally:
            self._condition.release()

        discard = True
        try:
            result = worker.call(
                command, max(0.001, timeout - (time.monotonic() - started))
            )
            discard = result.returncode != 0
            return result
        finally:
            try:
                if discard:
                    worker.close()
            finally:
                with self._condition:
                    if discard:
                        del self._workers[key]
                    self._busy.remove(key)
                    self._condition.notify_all()

    def _has_capacity(self, key: str) -> bool:
        # Called with the pool lock held; busy slots remain owned through cleanup.
        return (
            key in self._workers
            or len(self._workers) < 2
            or any(revision not in self._busy for revision in self._workers)
        )

    def _evict_idle(self) -> None:
        # The caller has waited for capacity. Never close an active pipe.
        if len(self._workers) == 2:
            key = next(key for key in self._workers if key not in self._busy)
            self._workers.pop(key).close()

    def close(self) -> None:
        with self._validation_lock, self._condition:
            self._condition.wait_for(lambda: not self._busy)
            for worker in self._workers.values():
                worker.close()
            self._workers.clear()
