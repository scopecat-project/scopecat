"""Slow imports, reconnect and cancellation retain one operation identity."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx2
import psutil
import pytest
from fastapi.testclient import TestClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.preparation import (
    AuthorPreparationFailed,
    AuthorPreparationTimeout,
)
from scopecat.records.author_revision import (
    AuthorPreparation,
    AuthorPreparationRequest,
    AuthorRevisionBundle,
    AuthorRevisionState,
)

from scopecat_server.http.transport import create_app
from scopecat_server.services.application import DaemonApplication
from scopecat_server.services.author_revisions import AuthorRevisionService
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

type PreparationProject = tuple[Path, AuthorRevisionService, AuthorProject]


@pytest.fixture
def preparation_project(
    tmp_path: Path,
) -> Iterator[tuple[Path, AuthorRevisionService, AuthorProject]]:
    source = tmp_path / "src/authors/app.py"
    source.parent.mkdir(parents=True)
    (tmp_path / "scopecat.toml").write_text(
        '[lab]\napplication="authors.app:create_application"\n'
        '[authors]\nsource_roots=["src"]\nrefresh_roots=["src/authors"]\n'
    )
    source.write_text(
        "import os, time\nfrom scopecat.application import LabApplication\n"
        "def create_application(root):\n"
        "    with (root / 'loads').open('a') as f: f.write(str(os.getpid()) + '\\n')\n"
        "    while not (root / 'release').exists(): time.sleep(0.01)\n"
        "    return LabApplication()\n"
    )
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    service = AuthorRevisionService(tmp_path, store)
    application = cast(
        "DaemonApplication", cast("object", SimpleNamespace(author_revisions=service))
    )
    web = TestClient(create_app(application))

    def request(incoming: httpx2.Request) -> httpx2.Response:
        response = web.request(
            incoming.method,
            incoming.url.raw_path.decode(),
            content=incoming.content,
            headers=dict(incoming.headers),
        )
        return httpx2.Response(response.status_code, content=response.content)

    client = AuthorProject("http://testserver", transport=httpx2.MockTransport(request))
    try:
        yield tmp_path, service, client
    finally:
        service.close()
        client.close()
        web.close()
        store.close()


def loaded(root: Path) -> int:
    deadline = time.monotonic() + 30
    while not (root / "loads").exists():
        assert time.monotonic() < deadline, (
            "fixture validation worker never entered application"
        )
        time.sleep(0.01)
    return int((root / "loads").read_text().splitlines()[0])


def test_wait_timeout_reconnect_and_same_request_do_not_recapture(
    preparation_project: PreparationProject,
) -> None:
    root, service, client = preparation_project
    operation = client.begin_refresh(operation_id="once")
    loaded(root)
    captured = operation.status().code_revision
    with pytest.raises(AuthorPreparationTimeout) as error:
        operation.wait(timeout=0.02)
    assert error.value.operation.id == operation.id
    source = root / "src/authors/app.py"
    source.write_text(source.read_text() + "\n# changed after capture\n")
    assert client.begin_refresh(operation_id="once").status().code_revision == captured
    assert service.state(initialize=False).preparation_id == "once"
    (root / "release").touch()
    result = operation.reconnect(client).wait(timeout=30)
    assert result.generation == 1 and result.active == captured
    assert operation.cancel().status == "succeeded"
    assert len((root / "loads").read_text().splitlines()) == 1
    assert client.begin_refresh(operation_id="once").wait(timeout=1) == result
    assert service.repository.preparation("once").result == result


def test_cancel_reaps_running_candidate_and_queued_work(
    preparation_project: PreparationProject,
) -> None:
    root, service, client = preparation_project
    first = client.begin_refresh(operation_id="running")
    pid = loaded(root)
    second = client.begin_refresh(operation_id="queued")
    assert second.cancel().status == "cancelled"
    assert first.status().status == "running"
    assert first.cancel().status == "cancelling"
    for operation in (first, second):
        with pytest.raises(AuthorPreparationFailed) as error:
            operation.wait(timeout=15)
        assert error.value.operation.status == "cancelled"
    assert service.repository.state().active is None
    assert not psutil.pid_exists(pid)
    assert len((root / "loads").read_text().splitlines()) == 1


def test_shutdown_cancels_pending_import(
    preparation_project: PreparationProject,
) -> None:
    root, service, client = preparation_project
    operation = client.begin_refresh()
    pid = loaded(root)
    service.close()
    assert operation.status().status == "cancelled"
    assert not psutil.pid_exists(pid)


def test_success_is_durable_even_if_worker_adoption_fails(
    preparation_project: PreparationProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, service, client = preparation_project
    publish = service.repository.publish

    def publish_then_fail(
        bundle: AuthorRevisionBundle,
        *,
        expected_generation: int,
        operation: AuthorPreparation | None = None,
    ) -> AuthorRevisionState:
        publish(bundle, expected_generation=expected_generation, operation=operation)
        raise RuntimeError("synthetic failure after publication committed")

    monkeypatch.setattr(service.repository, "publish", publish_then_fail)
    (root / "release").touch()
    result = client.begin_refresh(operation_id="committed").wait(timeout=30)
    assert result == service.repository.state()
    assert service.repository.preparation("committed").status == "succeeded"


def test_restart_marks_unfinished_operation_interrupted(
    preparation_project: PreparationProject,
) -> None:
    root, service, client = preparation_project
    operation = client.begin_refresh(operation_id="original")
    loaded(root)
    service.close()
    record = service.repository.preparation(operation.id).model_copy(
        update={"status": "running", "error": None}
    )
    service.repository.save_preparation(record)
    restarted = AuthorRevisionService(root, service.repository.store)
    try:
        assert restarted.repository.preparation(operation.id).status == "interrupted"
        assert (
            restarted.start(
                AuthorPreparationRequest(operation_id="original", expected_generation=0)
            ).status
            == "interrupted"
        )
        assert len((root / "loads").read_text().splitlines()) == 1
    finally:
        restarted.close()


def test_lost_submission_response_keeps_original_request(
    preparation_project: PreparationProject,
) -> None:
    from scopecat.daemon.preparation import AuthorPreparationSubmissionUncertain

    root, service, _ = preparation_project
    submissions = 0

    def lose_response(request: httpx2.Request) -> httpx2.Response:
        nonlocal submissions
        if request.method == "POST":
            submissions += 1
            service.start(AuthorPreparationRequest.model_validate_json(request.content))
            raise httpx2.ReadTimeout("response lost after acceptance")
        return httpx2.Response(
            200, content=service.state(initialize=False).model_dump_json()
        )

    with (
        AuthorProject(
            "http://testserver", transport=httpx2.MockTransport(lose_response)
        ) as disconnected,
        pytest.raises(AuthorPreparationSubmissionUncertain) as error,
    ):
        disconnected.begin_refresh()
    operation = error.value.operation
    accepted = service.repository.preparation(operation.id)
    assert accepted.expected_generation == error.value.request.expected_generation
    assert submissions == 1
    source = root / "src/authors/app.py"
    source.write_text(source.read_text() + "\n# after uncertain submission\n")
    assert service.start(error.value.request).code_revision == accepted.code_revision
    service.cancel(operation.id)


def test_observation_disconnect_retains_handle() -> None:
    from scopecat.daemon.preparation import AuthorPreparationDisconnected

    def disconnected(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("daemon unavailable")

    with AuthorProject(
        "http://testserver", transport=httpx2.MockTransport(disconnected)
    ) as client:
        operation = client.preparation("accepted-before-disconnect")
        with pytest.raises(AuthorPreparationDisconnected) as error:
            operation.wait()
        assert error.value.operation is operation
