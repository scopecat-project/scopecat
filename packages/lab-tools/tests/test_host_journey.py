"""One real host per home; durable work survives its HTTP owner's restart."""

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from lab_tools import host_client
from lab_tools.host_client import ensure_host
from lab_tools.host_operations import Command, Operation, Operations


def test_host_startup_through_interpreter_launcher(tmp_path: Path, monkeypatch) -> None:
    original = subprocess.Popen
    launchers = []

    def redirect(args, **kwargs):
        launcher = original(
            [
                sys.executable,
                "-c",
                "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))",
                *args,
            ],
            **kwargs,
        )
        launchers.append(launcher)
        return launcher

    monkeypatch.setattr(host_client.subprocess, "Popen", redirect)
    client = ensure_host(tmp_path, Path(__file__).resolve().parents[3])
    try:
        assert client.record.pid != launchers[0].pid
        assert client.state().operations == []
    finally:
        client.shutdown()
        launchers[0].wait(timeout=15)


def test_host_reuse_restart_and_worker_reconnection(tmp_path: Path) -> None:
    home = tmp_path / "中文 管理入口"
    source = Path(__file__).resolve().parents[3]
    with ThreadPoolExecutor(max_workers=2) as pool:
        clients = list(pool.map(lambda _: ensure_host(home, source), range(2)))
    client = clients[0]
    assert client.record.instance == clients[1].record.instance
    worker = None
    try:
        command = Command(action="stop", workspace="f" * 32)
        operation = client.submit(command)
        with pytest.raises(ValueError, match="未找到"):
            client.wait(operation)
        assert client.submit(command).command.id == command.id
        assert len(client.state().operations) == 1
        assert "ValueError" in str(
            client.request("GET", f"/api/operations/{command.id}/log")
        )

        # Use a controlled long worker to test an actual host crash, not just
        # reconstruct the Python client while all processes remain unchanged.
        command = Command(action="verify", topic="parameters")
        store = Operations(home)
        store.begin(command)
        release = home / "release-worker"
        code = """
import sys, time
from pathlib import Path
from lab_tools.host_operations import Operations
store = Operations(Path(sys.argv[1]))
operation = store.claim(sys.argv[2])
while not Path(sys.argv[3]).exists():
    time.sleep(0.05)
operation.status = 'succeeded'
operation.detail = 'completed after host restart'
store.save(operation)
"""
        worker = subprocess.Popen(  # noqa: S603 - fixed test worker
            [sys.executable, "-c", code, str(home), command.id, str(release)]
        )
        deadline = time.monotonic() + 15
        while (
            store.get(command.id).status == "starting" and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert store.get(command.id).status == "running"
        with pytest.raises(ValueError, match="尚未完成"):
            client.shutdown()
        previous = client.record.instance
        process = psutil.Process(client.record.pid)
        process.terminate()
        process.wait(timeout=15)
        client = ensure_host(home, source)
        assert client.record.instance != previous
        restored = Operation.model_validate(
            client.request("GET", f"/api/operations/{command.id}")
        )
        assert restored.status == "running"
        release.touch()
        worker.wait(timeout=15)
        assert client.wait(restored).detail == "completed after host restart"
        assert client.submit(command).status == "succeeded"
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=15)
        Operations(home).reconcile()
        client.shutdown()
