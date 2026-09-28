"""Application exit preserves external services and checks actual process identity."""

import sys
from pathlib import Path

import psutil
import pytest
from filelock import FileLock

from lab_tools.host_client import ensure_host, process_alive
from lab_tools.host_models import Command
from lab_tools.services import Services
from scopecat_server.lifecycle import (
    initialize_project,
    inspect_daemon,
    stop_project,
    write_daemon_endpoint_record,
)


def test_killed_daemon_record_is_reconciled_before_environment_recheck(tmp_path):
    project = initialize_project(tmp_path / "project")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>fixture</html>")
    store = Services(tmp_path / "home")
    service = store.register(
        project.root, Path(sys.executable), name="test", static_dir=gui
    )
    evidence = project.runtime_binding.data_root / "keep-scientific-data"
    try:
        store.start(service.id)
        evidence.write_bytes(b"retained")
        record = inspect_daemon(project).record
        assert record is not None
        process = psutil.Process(record.pid)
        process.kill()
        process.wait(timeout=10)
        assert inspect_daemon(project).state == "stale"
        with FileLock(project.runtime_binding.data_root / "daemon.lock"):
            with pytest.raises(ValueError, match="仍被进程占用"):
                store.recheck(service.id, operation_id="recheck-while-locked")
            assert inspect_daemon(project).state == "stale"
        store.recheck(service.id, operation_id="recheck-after-kill")
        assert inspect_daemon(project).state == "stopped"
        assert evidence.read_bytes() == b"retained"
        store.start(service.id)
        assert inspect_daemon(project).state == "running"
    finally:
        stop_project(project)


def test_environment_conflict_can_be_stopped_and_restarted_in_manager(tmp_path):
    project = initialize_project(tmp_path / "project")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>fixture</html>")
    store = Services(tmp_path / "home")
    service = store.register(
        project.root, Path(sys.executable), name="test", static_dir=gui
    )
    try:
        store.start(service.id)
        record = inspect_daemon(project).record
        assert record is not None
        assert record.python == Path(sys.executable).absolute()
        # An already-running release with unknown interpreter remains recoverable.
        write_daemon_endpoint_record(record.model_copy(update={"python": None}))
        with pytest.raises(ValueError, match="停止服务"):
            store.start(service.id)
        assert inspect_daemon(project).state == "running"
        view = store.views()[0]
        assert view.state == "degraded"
        assert view.url is None
        assert "后台服务仍在运行" in view.detail
        store.stop(service.id)
        assert inspect_daemon(project).state == "stopped"
        store.start(service.id)
        assert inspect_daemon(project).state == "running"
    finally:
        stop_project(project)


@pytest.mark.parametrize("stop_started", [False, True])
def test_exit_owns_only_services_started_in_this_session(tmp_path, stop_started):
    home = tmp_path / "application"
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>fixture</html>")
    projects = [initialize_project(tmp_path / name) for name in ("external", "owned")]
    store = Services(home)
    registered = [
        store.register(project.root, Path(sys.executable), name=str(i), static_dir=gui)
        for i, project in enumerate(projects)
    ]
    client = None
    try:
        store.start(registered[0].id)
        external = inspect_daemon(projects[0]).record
        client = ensure_host(home, None)
        for service in registered:
            command = Command(action="service_start", service=service.id)
            client.wait(client.submit(command))
            # The HTTP host supplies the session, and retry remains idempotent.
            assert client.submit(command).status == "succeeded"
        plan = client.request("GET", "/api/exit")
        assert plan == {
            "services": [
                {"id": service.id, "name": service.name, "owned": index == 1}
                for index, service in enumerate(registered)
            ]
        }
        client.exit(stop_started=stop_started)
        assert not process_alive(client.record)
        assert inspect_daemon(projects[0]).record == external
        assert inspect_daemon(projects[1]).state == (
            "stopped" if stop_started else "running"
        )
    finally:
        for project in projects:
            stop_project(project)
        if client is not None and process_alive(client.record):
            client.shutdown()


def test_restarted_service_is_not_owned_by_old_session(tmp_path):
    project = initialize_project(tmp_path / "project")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>fixture</html>")
    store = Services(tmp_path / "home")
    service = store.register(
        project.root, Path(sys.executable), name="test", static_dir=gui
    )
    try:
        store.start(service.id, session="a" * 32)
        assert store.owned("a" * 32) == [service]
        store.stop(service.id)
        store.start(service.id)
        store.stop_owned("a" * 32)
        assert inspect_daemon(project).state == "running"
    finally:
        stop_project(project)
