"""Vendor byte output cannot become control output or exceed retained quotas."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from scopecat_server.instruments.worker_output import (
    MAX_DISPLAY_BYTES,
    MAX_GENERATIONS,
    MAX_LOG_BYTES,
    WorkerOutput,
    diagnostic_path,
    read_diagnostic,
)


def _emit(
    project: Path,
    generation: str,
    operation: str,
    *,
    overflow: bool = False,
    disk_failure: bool = False,
    fixture_failure: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    script = """
import ctypes, os, sys
from unittest.mock import Mock
import scopecat_server.instruments.worker_output as output
from pathlib import Path
from scopecat_server.instruments.worker_output import (
    capture_worker_output, worker_operation,
)
control = os.dup(1)
libc = ctypes.CDLL("ucrtbase" if sys.platform == "win32" else None)
# UCRT printf is an inline header wrapper, not a DLL export. Use its exported
# FILE* functions with explicit pointer signatures, in the same CRT as Python.
fdopen = libc._fdopen if sys.platform == "win32" else libc.fdopen
fdopen.argtypes = (ctypes.c_int, ctypes.c_char_p)
fdopen.restype = ctypes.c_void_p
libc.fwrite.argtypes = (
    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p,
)
libc.fwrite.restype = ctypes.c_size_t
libc.fflush.argtypes = (ctypes.c_void_p,)
libc.fflush.restype = ctypes.c_int
with capture_worker_output(Path(sys.argv[1]), sys.argv[2]):
    if sys.argv[5] == "disk-failure":
        original = output._capture._file
        output._capture._file = Mock(wraps=original)
        output._capture._file.write.side_effect = OSError("disk full")
    context = {"request_id": 1, "instrument_id": sys.argv[3], "operation": "collect"}
    with worker_operation(context):
        print("python-" + sys.argv[3], flush=True)
        native_stdout = fdopen(1, b"wb")
        assert native_stdout
        message = b"native-C-stdio"
        assert libc.fwrite(message, 1, len(message), native_stdout) == len(message)
        assert libc.fflush(native_stdout) == 0
        os.write(1, b"native-stdout-\\xff\\n")
        os.write(2, b"native-stderr-\\xfe\\n")
        if sys.argv[4] == "overflow":
            for _ in range(1024):
                os.write(2, b"x" * 4096)
        tail = b"unflushed-vendor-tail"
        assert libc.fwrite(tail, 1, len(tail), native_stdout) == len(tail)
# Flush the remaining FILE buffer only after capture: descriptor 1 must now discard.
assert libc.fflush(native_stdout) == 0
if sys.argv[6] == "fixture-failure":
    raise RuntimeError("fixture error after capture shutdown")
os.write(control, b"control-clean\\n")
os.close(control)
"""
    # The fixture must report exceptions outside redirected descriptors even when
    # capture teardown routes fd 2 to devnull or its diagnostic disk has failed.
    script = (
        "import os, traceback\nerror_channel = os.dup(2)\ntry:\n"
        + textwrap.indent(script, "    ")
        + "except Exception:\n"
        + "    os.write(error_channel, traceback.format_exc().encode('utf-8'))\n"
        + "    raise SystemExit(1)\n"
        + "finally:\n    os.close(error_channel)\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter/script, test-owned paths.
        [
            sys.executable,
            "-c",
            script,
            str(project),
            generation,
            operation,
            "overflow" if overflow else "normal",
            "disk-failure" if disk_failure else "normal",
            "fixture-failure" if fixture_failure else "normal",
        ],
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"native fixture exit={result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    return result


def test_native_invalid_bytes_and_python_output_from_two_workers(
    tmp_path: Path,
) -> None:
    generations = uuid4().hex, uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [
            pool.submit(_emit, tmp_path, generation, instrument)
            for generation, instrument in zip(
                generations, ("alpha", "beta"), strict=True
            )
        ]
        results = [future.result() for future in pending]
    for generation, instrument, result in zip(
        generations, ("alpha", "beta"), results, strict=True
    ):
        assert result.stdout.splitlines() == [b"control-clean"]
        assert result.stderr == b""
        records = [
            json.loads(line)
            for line in diagnostic_path(tmp_path, generation).read_bytes().splitlines()
        ]
        assert {record["generation"] for record in records} == {generation}
        raw = b"".join(base64.b64decode(record["bytes_base64"]) for record in records)
        assert b"native-C-stdio" in raw
        assert b"unflushed-vendor-tail" not in raw
        assert b"native-stdout-\xff" in raw
        assert b"native-stderr-\xfe" in raw
        python = [
            record for record in records if record["attribution"] == "python_context"
        ]
        assert all(
            record["contexts"][0]["instrument_id"] == instrument for record in python
        )
        assert any(
            record["attribution"] == "sampled_active_requests" for record in records
        )


@pytest.mark.parametrize("disk_failure", [False, True])
def test_full_log_keeps_draining_native_output(
    tmp_path: Path, disk_failure: bool
) -> None:
    generation = uuid4().hex
    result = _emit(
        tmp_path, generation, "flood", overflow=True, disk_failure=disk_failure
    )
    assert result.stdout.splitlines() == [b"control-clean"]
    assert result.stderr == b""
    retained = diagnostic_path(tmp_path, generation).read_bytes()
    assert len(read_diagnostic(tmp_path, generation)) <= MAX_DISPLAY_BYTES
    assert len(retained) <= MAX_LOG_BYTES
    if disk_failure:
        assert retained == b""
    else:
        assert retained.endswith(b'{"truncated":true}\n')


def test_concurrent_retention_keeps_active_generations_and_closes_to_discard(
    tmp_path: Path,
) -> None:
    generations = [uuid4().hex for _ in range(MAX_GENERATIONS)]
    with ThreadPoolExecutor(max_workers=MAX_GENERATIONS) as pool:
        pending = [
            pool.submit(WorkerOutput, tmp_path, generation)
            for generation in generations
        ]
        captures = [future.result() for future in pending]
    denied = WorkerOutput(tmp_path, uuid4().hex)
    try:
        assert not denied.retained
        denied.write("stderr", b"still drained", native=True)
        assert all(
            diagnostic_path(tmp_path, generation).exists() for generation in generations
        )
        first = captures[0]
        first.close()
        first.write("stderr", b"late inherited descriptor bytes", native=True)
        replacement = WorkerOutput(tmp_path, uuid4().hex)
        try:
            assert replacement.retained
            assert (
                len(
                    list(
                        (tmp_path / ".scopecat" / "worker-diagnostics").glob("*.jsonl")
                    )
                )
                == MAX_GENERATIONS
            )
        finally:
            replacement.close()
    finally:
        denied.close()
        for capture in captures[1:]:
            capture.close()


def test_native_fixture_reports_errors_after_capture_shutdown(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="fixture error after capture shutdown"):
        _emit(tmp_path, uuid4().hex, "diagnostic", fixture_failure=True)
