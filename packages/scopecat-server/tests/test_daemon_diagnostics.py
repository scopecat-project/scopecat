"""Parent-side evidence survives a controlled post-start process exit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scopecat_server._daemon_diagnostics import capture, observe_spawn


def test_failure_snapshot_keeps_identity_exit_code_and_bounded_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "evidence"
    monkeypatch.setenv("SCOPECAT_STARTUP_DIAGNOSTICS", str(directory))
    root = tmp_path / "project"
    state = root / ".scopecat"
    state.mkdir(parents=True)
    log = state / "daemon.log"
    log.write_bytes(b"x" * 70000 + b"before failure\n")
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read(); sys.exit(7)"],
        stdin=subprocess.PIPE,
    )
    try:
        observe_spawn(root, child)
        capture("failed call test_endpoint")
        [record] = directory.glob("*.jsonl")
        rows = [json.loads(line) for line in record.read_text().splitlines()]
        failed = rows[-1]
        assert failed["phase"] == "failed call test_endpoint"
        assert failed["pid"] == child.pid
        assert failed["create_time"] is not None
        assert failed["returncode"] is None
        failed_log = directory / failed["log_tail"]
        assert failed_log.stat().st_size == 65536
        assert failed_log.read_bytes().endswith(b"before failure\n")
        assert child.stdin is not None
        child.stdin.close()
        assert child.wait(timeout=5) == 7
        log.write_bytes(b"after exit\n")
        capture("before_stop", root=root)
        exited = json.loads(record.read_text().splitlines()[-1])
        assert exited["returncode"] == 7
        assert exited["pid"] == failed["pid"]
        assert exited["create_time"] == failed["create_time"]
        assert (directory / exited["log_tail"]).read_bytes() == b"after exit\n"
        assert failed_log.read_bytes().endswith(b"before failure\n")
        previous = record.read_bytes()
        capture("session_finish")
        assert record.read_bytes() == previous  # Exited handle has been retired.
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        capture("test_cleanup", root=root)
