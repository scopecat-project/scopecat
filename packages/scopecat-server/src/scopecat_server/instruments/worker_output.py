"""Process-lifetime vendor diagnostics, separate from the worker control channel.

Native descriptors are process-wide: their contexts are sampled at read time,
never proof that an overlapping or already-completed request emitted the bytes.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import traceback
from collections.abc import Buffer, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock, Thread
from typing import BinaryIO, TextIO, cast, override

from filelock import FileLock, Timeout

MAX_GENERATIONS = 8
MAX_LOG_BYTES = 256 * 1024
CHUNK_BYTES = 2048
_CONTEXT: ContextVar[dict[str, str | int] | None] = ContextVar(
    "vendor_call", default=None
)
_capture: WorkerOutput | None = None


def diagnostic_path(project: Path, generation: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        raise ValueError("invalid worker generation")
    return project / ".scopecat" / "worker-diagnostics" / f"{generation}.jsonl"


class WorkerOutput:
    def __init__(self, project: Path, generation: str) -> None:
        path = diagnostic_path(project, generation)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.generation = generation
        self._lock = Lock()
        self._active: dict[int, dict[str, str | int]] = {}
        self._size = 0
        self._full = False
        self._file: BinaryIO | None = None
        self._lease = FileLock(str(path) + ".lock", thread_local=False)
        # Serialize allocation/pruning and never unlink an active generation.
        # If all retained slots are active, new output is drained but not retained.
        with FileLock(str(path.parent / ".retention.lock")):
            existing = sorted(
                path.parent.glob("*.jsonl"), key=lambda item: item.stat().st_mtime_ns
            )
            count = len(existing)
            for old in existing:
                if count < MAX_GENERATIONS:
                    break
                old_lease = FileLock(str(old) + ".lock")
                try:
                    with old_lease.acquire(timeout=0):
                        old.unlink(missing_ok=True)
                    Path(old_lease.lock_file).unlink(missing_ok=True)
                    count -= 1
                except Timeout, PermissionError:
                    continue
            if count < MAX_GENERATIONS:
                self._lease.acquire()
                self._file = path.open("wb", buffering=0)
            else:
                self._full = True

    @property
    def retained(self) -> bool:
        return self._file is not None

    @contextmanager
    def operation(self, context: dict[str, str | int]) -> Generator[None]:
        request_id = int(context["request_id"])
        with self._lock:
            self._active[request_id] = context
        token = _CONTEXT.set(context)
        try:
            yield
        finally:
            _CONTEXT.reset(token)
            with self._lock:
                self._active.pop(request_id)

    def write(self, stream: str, data: bytes, *, native: bool) -> None:
        for offset in range(0, len(data), CHUNK_BYTES):
            with self._lock:
                if self._full:
                    return
                assert self._file is not None
                context = _CONTEXT.get()
                record = {
                    "generation": self.generation,
                    "pid": os.getpid(),
                    "stream": stream,
                    "attribution": "sampled_active_requests"
                    if native
                    else "python_context",
                    "contexts": list(self._active.values())
                    if native
                    else ([] if context is None else [context]),
                    "bytes_base64": base64.b64encode(
                        data[offset : offset + CHUNK_BYTES]
                    ).decode("ascii"),
                }
                encoded = (json.dumps(record, ensure_ascii=True) + "\n").encode("ascii")
                try:
                    if self._size + len(encoded) > MAX_LOG_BYTES - 128:
                        self._file.write(b'{"truncated":true}\n')
                        self._full = True
                        return
                    self._file.write(encoded)
                    self._size += len(encoded)
                except OSError:
                    # A failed diagnostic disk must not stop the pipe reader and
                    # block vendor calls. The retained prefix may be incomplete.
                    self._full = True
                    return

    def close(self) -> None:
        with self._lock:
            self._full = True
            if self._file is not None:
                self._file.close()
                self._lease.release()


class _BinaryOutput(io.RawIOBase):
    def __init__(self, capture: WorkerOutput, stream: str, descriptor: int) -> None:
        super().__init__()
        self._capture = capture
        self._stream = stream
        self._descriptor = descriptor
        self.name = stream

    @override
    def writable(self) -> bool:
        return True

    @override
    def fileno(self) -> int:
        return self._descriptor

    @override
    def write(self, data: Buffer) -> int:
        value = bytes(data)
        self._capture.write(self._stream, value, native=False)
        return len(value)


@contextmanager
def capture_worker_output(project: Path, generation: str) -> Generator[None]:
    global _capture
    capture = WorkerOutput(project, generation)
    _capture = capture
    readers: list[Thread] = []
    original_streams = sys.stdout, sys.stderr
    streams: list[TextIO] = []

    def drain(descriptor: int, stream: str) -> None:
        try:
            while data := os.read(descriptor, CHUNK_BYTES):
                # Keep draining even after retention is full. Never block the vendor
                # on a queue whose consumer stopped because its log quota was hit.
                capture.write(stream, data, native=True)
        finally:
            os.close(descriptor)

    try:
        for descriptor, stream in ((1, "stdout"), (2, "stderr")):
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, descriptor)
            os.close(write_fd)
            reader = Thread(target=drain, args=(read_fd, stream), daemon=True)
            reader.start()
            readers.append(reader)
            streams.append(
                io.TextIOWrapper(
                    _BinaryOutput(capture, stream, descriptor),
                    encoding="utf-8",
                    errors="backslashreplace",
                    write_through=True,
                )
            )
        sys.stdout, sys.stderr = streams
        yield
    finally:
        for stream in streams:
            stream.flush()
        sys.stdout, sys.stderr = original_streams
        # Do not restore inherited daemon descriptors: native C/atexit buffers
        # flushed after capture ends must not leak into daemon output. Such bytes
        # were never delivered during capture and are intentionally not retained.
        discard = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(discard, 1)
            os.dup2(discard, 2)
        finally:
            os.close(discard)
        for reader in readers:
            reader.join(timeout=1)
        _capture = None
        capture.close()


@contextmanager
def worker_operation(context: dict[str, str | int]) -> Generator[None]:
    if _capture is None:
        yield
    else:
        with _capture.operation(context):
            yield


def record_problem(code: str, message: str) -> None:
    if _capture is not None:
        _capture.write(
            "stderr",
            f"{code}: {message}\n".encode("utf-8", errors="backslashreplace"),
            native=False,
        )


def record_exception(error: BaseException) -> None:
    # Tracebacks stay in capped diagnostics, not in control response text.
    if _capture is not None:
        _capture.write(
            "stderr",
            "".join(traceback.format_exception(error)).encode(
                "utf-8", errors="backslashreplace"
            ),
            native=False,
        )


def diagnostic_reference(
    context: Mapping[str, str | int] | None = None,
) -> dict[str, str | int] | None:
    if _capture is None:
        return None
    return {
        "generation": _capture.generation,
        "retention": "retained" if _capture.retained else "unavailable_active_quota",
        **(context if context is not None else (_CONTEXT.get() or {})),
    }


MAX_DISPLAY_BYTES = 64 * 1024


def read_diagnostic(project: Path, generation: str, *, raw: bool = False) -> bytes:
    """Return a bounded retained prefix; raw JSONL preserves original byte values."""
    path = diagnostic_path(project, generation)
    with path.open("rb") as source:
        content = source.read(MAX_LOG_BYTES)
    if raw:
        return content
    lines = [
        "Worker diagnostics: retained prefix (at most 256 KiB encoded raw evidence).",
        "Display is UTF-8 with replacement; raw evidence retains original bytes.",
        "Native request contexts are read-time samples, not attribution proof.",
        "Download raw JSONL by adding ?raw=true to this URL.\n",
    ]
    remaining = MAX_DISPLAY_BYTES
    output = "\n".join(lines).encode("utf-8")
    remaining -= len(output)
    for line in content.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            output += b"\n[Incomplete final diagnostic record]\n"
            break
        record = cast("dict[str, object]", json.loads(line))
        if record.get("truncated"):
            rendered = (
                "\n[Retention quota reached; later output was drained and discarded]\n"
            )
        else:
            raw_bytes = base64.b64decode(cast("str", record["bytes_base64"]))
            rendered = (
                f"\n[{record['generation']} {record['stream']} "
                f"{record['attribution']} {record['contexts']}]\n"
                + raw_bytes.decode("utf-8", errors="replace")
            )
        encoded = rendered.encode("utf-8")
        if len(encoded) > remaining - 64:
            output += (
                encoded[: max(0, remaining - 64)]
                .decode("utf-8", errors="ignore")
                .encode("utf-8")
            )
            output += b"\n[Display limit reached; see bounded raw JSONL]\n"
            break
        output += encoded
        remaining -= len(encoded)
    return output
