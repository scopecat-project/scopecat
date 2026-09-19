"""One real host per home; durable work survives its HTTP owner's restart."""

import hashlib
import secrets
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import psutil
import pytest
from fastapi.testclient import TestClient

from lab_tools import app_host, host_client
from lab_tools.host_client import HostRecord, ensure_host
from lab_tools.host_operations import Command, Operation, Operations, execute
from lab_tools.services import Services
from scopecat_server.lifecycle import (
    initialize_project,
    inspect_daemon,
    start_project,
    stop_project,
)


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


def test_registered_project_reuses_environment_and_real_workbench(
    tmp_path: Path,
) -> None:
    root = tmp_path / "实验 project"
    project = initialize_project(root)
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>real workbench fixture</html>")
    home = tmp_path / "home"
    store = Services(home)
    service = store.register(root, Path(sys.executable), name="实验台", static_dir=gui)
    alias = store.register(
        root / "scopecat.toml", Path(sys.executable), name="实验台", static_dir=gui
    )
    assert alias == service
    assert inspect_daemon(project).state == "stopped"
    assert store.views()[0].url is None
    try:
        command = Command(action="service_start", service=service.id)
        api_only = start_project(project)
        with pytest.raises(ValueError, match="GUI"):
            execute(home, None, command)
        assert inspect_daemon(project).record == api_only
        stop_project(project)
        assert execute(home, None, command) is None
        first = inspect_daemon(project)
        assert first.record is not None
        assert execute(home, None, command) is None
        assert inspect_daemon(project).record == first.record
        state = store.views()[0]
        assert state.state == "running"
        assert state.url == first.record.base_url
        record = HostRecord(
            instance="test",
            pid=1,
            process_time=1,
            url="http://127.0.0.1:8912",
            token=secrets.token_urlsafe(32),
            runtime="test",
            python=sys.executable,
        )
        with TestClient(
            app_host.application(home, None, record, lambda: None), base_url=record.url
        ) as client:
            client.headers["Authorization"] = f"Bearer {record.token}"
            assert (
                client.get("/api/state").json()["services"][0]["service"]["id"]
                == service.id
            )
            assert (
                client.post(
                    "/api/operations",
                    json={
                        "action": "service_start",
                        "service": service.id,
                        "python": "/arbitrary",
                    },
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/operations",
                    json={"action": "service_start", "service": "f" * 32},
                ).status_code
                == 409
            )
            assert "实验服务" in client.get("/").text
        # Opening the workbench only starts the service, not an experiment.
        with httpx2.Client(base_url=first.record.base_url) as client:
            response = client.get("/api/v1/runs")
            assert response.status_code == 200
            assert response.json()["items"] == []
        from lab_tools.host_operations import launch

        with pytest.raises(ValueError, match="先停止"):
            store.remove(service.id, operation_id="not-admitted")

        def finish(command: Command) -> Operation:
            operation = launch(home, None, command)
            deadline = time.monotonic() + 20
            while (
                operation.status in ("starting", "running")
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
                operation = Operations(home).get(command.id)
            assert operation.status == "succeeded", operation.detail
            return operation

        stopped = finish(Command(action="service_stop", service=service.id))
        assert "科学记录保留" in stopped.detail
        assert inspect_daemon(project).state == "stopped"
        retained = {
            p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file()
        }
        removal = Command(action="service_remove", service=service.id)
        removed = finish(removal)
        assert "没有删除" in removed.detail
        assert store.list() == []
        assert launch(home, None, removal) == removed
        assert {
            p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file()
        } == retained
        replacement = store.register(
            root, Path(sys.executable), name="实验台", static_dir=gui
        )
        assert replacement.id != service.id
        assert inspect_daemon(project).state == "stopped"
        assert Operations(home).get(removal.id) == removed
    finally:
        stop_project(project)
