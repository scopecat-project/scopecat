"""Application exit preserves external services and checks actual process identity."""

import sys
from pathlib import Path

import pytest

from lab_tools.host_client import ensure_host, process_alive
from lab_tools.host_models import Command
from lab_tools.services import Services
from scopecat_server.lifecycle import initialize_project, inspect_daemon, stop_project


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
