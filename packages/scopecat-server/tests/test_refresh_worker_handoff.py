"""Publication owns the validated import namespace, including rejected candidates."""

from __future__ import annotations

import json
from pathlib import Path

import psutil
import pytest
from scopecat.application.launch import LaunchCatalog
from scopecat.records.launch_request import LaunchRequest

from scopecat_server.services.author_revisions import AuthorRevisionService
from scopecat_server.storage.sqlite.author_revision_repository import (
    AuthorRevisionConflict,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def test_refresh_hands_off_exact_application_and_closes_rejected_candidates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src/authors/app.py"
    source.parent.mkdir(parents=True)
    (tmp_path / "scopecat.toml").write_text(
        '[lab]\napplication="authors.app:create_application"\n'
        '[authors]\nsource_roots=["src"]\nrefresh_roots=["src/authors"]\n'
    )
    text = (
        "import json, os\nfrom scopecat.application import LabApplication\n"
        "def create_application(root):\n"
        '    with (root / "loads.jsonl").open("a") as output:\n'
        '        value = {"pid": os.getpid(), "file": __file__}\n'
        '        output.write(json.dumps(value) + "\\n")\n'
        "    return LabApplication()\n"
    )
    source.write_text(text)
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    service = AuthorRevisionService(tmp_path, store)
    owners: list[psutil.Process] = []

    def loads() -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (tmp_path / "loads.jsonl").read_text().splitlines()
        ]

    try:
        first = service.refresh(expected_generation=0)
        assert first.active is not None
        owners.append(psutil.Process(int(str(loads()[-1]["pid"]))))
        command = LaunchRequest(action="list", code_revision=first.active)
        # No daemon is running: a discarded validation process would require
        # revision_project's HTTP restore and this call would fail.
        result = service.workers.call(service.worker_binding, command)
        assert result.returncode == 0, result.stderr
        assert (
            LaunchCatalog.model_validate_json(result.stdout).code_revision
            == first.active
        )
        assert len(loads()) == 1
        assert str(tmp_path / ".scopecat/code") in str(loads()[0]["file"])

        source.write_text(text + "\n# second revision\n")
        second = service.refresh(expected_generation=1)
        assert second.active != first.active
        owners.append(psutil.Process(int(str(loads()[-1]["pid"]))))
        assert service.workers.call(service.worker_binding, command).returncode == 0
        assert (
            service.workers.call(
                service.worker_binding,
                command.model_copy(update={"code_revision": second.active}),
            ).returncode
            == 0
        )
        assert len(loads()) == 2

        # A rejected CAS neither activates nor adopts its freshly validated source.
        source.write_text(text + "\n# rejected revision\n")
        with pytest.raises(AuthorRevisionConflict):
            service.refresh(expected_generation=1)
        assert service.repository.state() == second
        assert not psutil.pid_exists(int(str(loads()[-1]["pid"])))
        assert all(owner.is_running() for owner in owners)

        # An unchanged refresh still validates, but keeps the existing warm worker.
        source.write_text(text + "\n# second revision\n")
        unchanged = service.refresh(expected_generation=2)
        assert unchanged.active == second.active
        assert not psutil.pid_exists(int(str(loads()[-1]["pid"])))
        assert all(owner.is_running() for owner in owners)

        source.write_text("def invalid(:\n")
        with pytest.raises(ValueError, match="SyntaxError"):
            service.refresh(expected_generation=3)
        assert service.repository.state() == unchanged
        assert service.workers.call(service.worker_binding, command).returncode == 0
        source.write_text(text + "\n# third published revision\n")
        service.refresh(expected_generation=3)
        owners.append(psutil.Process(int(str(loads()[-1]["pid"]))))
        assert owners[0].is_running()  # the recently requested old revision survives
        assert not owners[1].is_running()  # adoption obeys the same two-slot LRU
    finally:
        service.close()
        store.close()
    assert all(not owner.is_running() for owner in owners)
