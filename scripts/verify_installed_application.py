"""Bounded real-process application lifecycle check, run by the installed Python.

The caller supplies a fresh evidence directory and the retained delivery GUI.
No repository imports, instruments or historical measurement stores are needed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx2

from lab_teaching.project import create_project
from lab_tools.host_client import ensure_host
from lab_tools.host_operations import Command
from lab_tools.services import Services
from scopecat.project import open_project
from scopecat_server.lifecycle import (  # noqa: TID251 - installed server qualification
    inspect_daemon,
    stop_project,
)


def files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def verify(home: Path, destination: Path, gui: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination / "实验 服务"
    # This delivery contains the compute-only lab, not the optional instrument
    # packages used by the server starter. Register it as an ordinary service;
    # virtual instrument execution is qualified by verify_pilot_bundle separately.
    create_project(root)
    project = open_project(root)
    # Owner-held material must survive maintenance, even when outside the store.
    (root / "owner-notes.txt").write_text("保留实验记录\n", encoding="utf-8")
    store = Services(home)
    service = store.register(
        root, Path(sys.executable), name="安装验收", static_dir=gui
    )
    assert inspect_daemon(project).state == "stopped"
    manager = ensure_host(home, None)
    operations: list[str] = []

    def complete(command: Command) -> None:
        operation = manager.wait(manager.submit(command))
        assert manager.submit(command) == operation
        log = manager.request("GET", f"/api/operations/{command.id}/log")
        assert isinstance(log, dict)
        operations.append(command.id)

    def check_workbench() -> None:
        view = next(v for v in manager.state().services if v.service.id == service.id)
        assert view.service == service
        assert view.state == "running" and view.url is not None
        with httpx2.Client(base_url=view.url, trust_env=False, timeout=30) as client:
            page = client.get("/")
            assert page.status_code == 200
            assert page.content == (gui / "index.html").read_bytes()
            runs = client.get("/api/v1/runs")
            assert runs.status_code == 200
            assert runs.json()["items"] == []

    try:
        complete(Command(action="service_start", service=service.id))
        check_workbench()
        complete(Command(action="service_stop", service=service.id))
        assert inspect_daemon(project).state == "stopped"
        retained = files(root)
        complete(Command(action="service_recheck", service=service.id))
        assert store.get(service.id) == service
        assert inspect_daemon(project).state == "stopped"
        assert files(root) == retained
        previous = manager.record.instance
        manager.shutdown()
        manager = ensure_host(home, None)
        assert manager.record.instance != previous
        assert store.get(service.id) == service
        for identity in operations:
            result = manager.request("GET", f"/api/operations/{identity}")
            assert isinstance(result, dict) and result["status"] == "succeeded"
        complete(Command(action="service_start", service=service.id))
        check_workbench()
        complete(Command(action="service_stop", service=service.id))
        retained = files(root)
        complete(Command(action="service_remove", service=service.id))
        assert all(item.id != service.id for item in store.list())
        assert files(root) == retained
        (destination / "acceptance.json").write_text(
            json.dumps(
                {
                    "software": "passed",
                    "human": "not-evaluated",
                    "physical": "not-evaluated",
                    "service_id": service.id,
                    "operations": operations,
                    "registered_environment": service.environment,
                    "workbench_without_acquisition": "passed",
                    "stopped_recheck_preserves_files_and_identity": "passed",
                    "manager_restart_preserves_registration_and_history": "passed",
                    "service_restart": "passed",
                    "unregister_preserves_files": "passed",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        # Failure evidence remains on disk; only this check's processes are stopped.
        stop_project(project)
        manager.shutdown()


if __name__ == "__main__":
    verify(
        Path(sys.argv[1]).resolve(),
        Path(sys.argv[2]).resolve(),
        Path(sys.argv[3]).resolve(),
    )
