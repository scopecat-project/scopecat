"""Owned vendor SDK process, with bounded calls and no implicit retry."""

from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import ExitStack, suppress
from queue import Empty, Queue
from threading import Lock, Thread
from typing import BinaryIO, cast

import psutil

from .sdk_wire import VERSION, mapping, receive, send


class SDKProcessError(RuntimeError):
    """SDK outcome may be unknown; hardware recovery must remain explicit."""


class SDKProcess:
    def __init__(self, command: tuple[str, ...], *, timeout: float = 30.0) -> None:
        if not command or timeout <= 0:
            raise ValueError("SDK command and positive timeout are required")
        self.timeout = timeout
        self._lock = Lock()
        self._write_lock = Lock()
        self._next = 0
        self._pending: dict[int, Queue[object]] = {}
        self._ready: Queue[object] = Queue()
        self._failure: SDKProcessError | None = None
        self._closed = False
        self.diagnostics = ""
        self._resources = ExitStack()
        # ExitStack owns this file for the process lifetime, beyond __init__.
        self._log = self._resources.enter_context(tempfile.TemporaryFile(mode="w+b"))  # noqa: SIM115
        environment = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            environment.pop(key, None)
        try:
            self._process = subprocess.Popen(  # noqa: S603 - explicitly selected local SDK command, no shell
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._log,
                env=environment,
                bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except BaseException:
            self._resources.close()
            raise
        self._owner = psutil.Process(self._process.pid)
        self._reader = Thread(target=self._read, name="sdk-response", daemon=True)
        self._reader.start()
        try:
            ready = mapping(self._wait(self._ready))
            if ready.get("version") != VERSION:
                raise SDKProcessError("Incompatible SDK bridge protocol")
            names = ready["methods"]
            if not isinstance(names, list) or any(
                not isinstance(name, str) for name in cast("list[object]", names)
            ):
                raise SDKProcessError("Invalid SDK capabilities")
            self.methods = tuple(cast("list[str]", names))
            self.constants = mapping(ready["constants"])
            self.python = str(ready["python"])
        except BaseException:
            self.close()
            raise

    @property
    def pid(self) -> int:
        return self._process.pid

    def _read(self) -> None:
        assert self._process.stdout is not None
        try:
            self._ready.put(receive(cast("BinaryIO", self._process.stdout)))
            while True:
                response = mapping(receive(cast("BinaryIO", self._process.stdout)))
                identity = response.get("id")
                if type(identity) is not int:
                    raise ValueError("Invalid SDK response")
                with self._lock:
                    pending = self._pending.get(identity)
                if pending is None:
                    raise ValueError("Unexpected SDK response identity")
                pending.put(response)
        except Exception as error:
            failure = SDKProcessError(f"SDK connection lost: {error}")
            with self._lock:
                self._failure = failure
                self._ready.put(failure)
                for pending in self._pending.values():
                    pending.put(failure)

    def _wait(self, pending: Queue[object]) -> object:
        try:
            response = pending.get(timeout=self.timeout)
        except Empty as error:
            self.close()
            raise SDKProcessError(
                "SDK call timed out; hardware state requires recovery"
            ) from error
        if isinstance(response, SDKProcessError):
            self._log.flush()
            self._log.seek(max(0, self._log.seek(0, 2) - 8192))
            self.diagnostics = self._log.read().decode("utf-8", errors="replace")
            raise SDKProcessError(f"{response}\n{self.diagnostics}".rstrip())
        return response

    def call(self, method: str, **kwargs: object) -> object:
        if method not in self.methods:
            raise ValueError(f"SDK does not provide {method}")
        with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._closed:
                raise SDKProcessError("SDK process is closed")
            self._next += 1
            identity = self._next
            pending: Queue[object] = Queue()
            self._pending[identity] = pending
        try:
            sent: Queue[object] = Queue()

            def write() -> None:
                try:
                    with self._write_lock:
                        assert self._process.stdin is not None
                        send(
                            cast("BinaryIO", self._process.stdin),
                            {
                                "version": VERSION,
                                "id": identity,
                                "method": method,
                                "kwargs": kwargs,
                            },
                        )
                    sent.put(None)
                except Exception as error:
                    sent.put(error)

            # A stopped reader must not make a large upload wait forever before
            # the response timeout even starts. Closing kills only this owner.
            writer = Thread(target=write, name="sdk-request", daemon=True)
            writer.start()
            outcome = self._wait(sent)
            if isinstance(outcome, Exception):
                raise outcome
            response = cast("dict[str, object]", self._wait(pending))
            if "error" in response:
                raise SDKProcessError(str(response["error"]))
            return response["result"]
        except (OSError, EOFError) as error:
            self.close()
            raise SDKProcessError(
                "SDK transport failed; operation was not retried"
            ) from error
        finally:
            with self._lock:
                self._pending.pop(identity, None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            failed_before_close = self._failure is not None
        inspection_error: psutil.Error | None = None
        try:
            descendants = self._owner.children(recursive=True)
        except psutil.NoSuchProcess:
            descendants = []
        except psutil.Error as error:
            descendants = []
            inspection_error = error
        forced = False
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                forced = True
                self._process.terminate()
                try:
                    self._process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
            # Reap descendants captured while their parent still existed.
            for process in reversed(descendants):
                with suppress(psutil.NoSuchProcess):
                    process.terminate()
            _, alive = psutil.wait_procs(descendants, timeout=1)
            for process in alive:
                with suppress(psutil.NoSuchProcess):
                    process.kill()
            _, alive = psutil.wait_procs(alive, timeout=1)
            if alive:
                raise SDKProcessError("SDK descendants did not stop")
            if inspection_error is not None:
                raise SDKProcessError(
                    "SDK stopped, but descendant inspection failed"
                ) from inspection_error
        finally:
            self._reader.join(timeout=2)
            if self._process.stdout is not None:
                self._process.stdout.close()
            self._log.seek(max(0, self._log.seek(0, 2) - 8192))
            self.diagnostics = self._log.read().decode("utf-8", errors="replace")
            self._resources.close()
        if not forced and self._process.returncode and not failed_before_close:
            raise SDKProcessError(f"SDK cleanup failed:\n{self.diagnostics}".rstrip())
