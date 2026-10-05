"""Local byte transport and owned launcher for an explicit driver interpreter."""

from __future__ import annotations

import json
import os
import secrets
import select
import socket
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import cast

import psutil


class ByteConnection:
    def __init__(self, connection: socket.socket) -> None:
        # Send frame headers and bodies promptly across the local TCP boundary.
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection = connection

    def send_bytes(self, buf: bytes | memoryview) -> None:
        self.connection.sendall(len(buf).to_bytes(4, "big"))
        self.connection.sendall(buf)

    def _read(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            value = self.connection.recv(min(size - len(result), 1024 * 1024))
            if not value:
                raise EOFError("driver process closed its connection")
            result.extend(value)
        return bytes(result)

    def recv_bytes(self, maxlength: int | None = None) -> bytes:
        size = int.from_bytes(self._read(4), "big")
        if size > (maxlength if maxlength is not None else 512 * 1024 * 1024):
            raise OSError("driver frame exceeds its declared limit")
        return self._read(size)

    def poll(self, timeout: float | None = 0.0) -> bool:
        return bool(select.select([self.connection], [], [], timeout)[0])

    def close(self) -> None:
        with suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()


class WorkerProcess:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.pid = process.pid
        self.owner = psutil.Process(process.pid)
        self.descendants: list[psutil.Process] = []

    def is_alive(self) -> bool:
        return self.process.poll() is None

    @property
    def exitcode(self) -> int | None:
        return self.process.poll()

    def join(self, timeout: float | None = None) -> None:
        with suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=timeout)

    def terminate(self) -> None:
        try:
            self.descendants = self.owner.children(recursive=True)
        except psutil.NoSuchProcess:
            pass
        finally:
            self.process.terminate()
        for child in reversed(self.descendants):
            with suppress(psutil.NoSuchProcess):
                child.terminate()

    def kill(self) -> None:
        self.process.kill()
        for child in reversed(self.descendants):
            with suppress(psutil.NoSuchProcess):
                child.kill()

    def close(self) -> None:
        self.process.wait(timeout=0)
        for child in reversed(self.descendants):
            with suppress(psutil.NoSuchProcess):
                child.kill()
        _, alive = psutil.wait_procs(self.descendants, timeout=2)
        if any(child.status() != psutil.STATUS_ZOMBIE for child in alive):
            raise RuntimeError("Driver descendants did not stop")


def launch(
    python: Path,
    configuration: dict[str, object],
    *,
    timeout: float,
) -> tuple[ByteConnection, WorkerProcess]:
    token = secrets.token_hex(32)
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(key, None)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(timeout)
        process = subprocess.Popen(  # noqa: S603 - trusted explicit interpreter and fixed module
            [
                str(python),
                "-m",
                "scopecat_server.instruments.worker_process",
                str(cast("tuple[str, int]", listener.getsockname())[1]),
                token,
                json.dumps(configuration),
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        owner = WorkerProcess(process)
        connection: ByteConnection | None = None
        try:
            channel = listener.accept()[0]
            channel.settimeout(timeout)
            connection = ByteConnection(channel)
            if not secrets.compare_digest(connection.recv_bytes(128), token.encode()):
                raise ValueError("Driver process identity handshake failed")
            channel.settimeout(None)
            return connection, owner
        except BaseException:
            if connection is not None:
                connection.close()
            owner.kill()
            owner.join()
            owner.close()
            raise
