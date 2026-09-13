"""Bounded author validation diagnostics, independently of acquisition."""

from __future__ import annotations

import faulthandler
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import psutil
import pytest
from fastapi.testclient import TestClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV

from scopecat_server.http.transport import create_app
from scopecat_server.services.application import DaemonApplication
from scopecat_server.services.author_revisions import (
    AuthorRevisionService,
    AuthorValidationTimeout,
)
from scopecat_server.services.revision_workers import _Worker
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.validation_process import terminate_validation_process_tree
from scopecat_server.worker_diagnostics import DIAGNOSTIC_LIMIT, diagnostic_excerpt


def test_diagnostic_excerpt_keeps_stage_and_bounded_tail() -> None:
    stage, evidence = diagnostic_excerpt(
        b"Scopecat worker stage: application import\n" + b"x" * 30_000 + b"\nstack end"
    )
    assert stage == "application import"
    assert evidence.endswith("stack end")
    assert "earlier stderr omitted" in evidence
    assert len(evidence.encode()) < DIAGNOSTIC_LIMIT + 100
    assert diagnostic_excerpt(None)[0] == "worker startup (no stage received)"


def test_helper_import_does_not_schedule_or_emit_validation_diagnostics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scopecat_server import author_worker

    with patch.object(faulthandler, "dump_traceback_later") as timer:
        importlib.reload(author_worker)
        timer.assert_not_called()
    assert capsys.readouterr().err == ""


def test_validation_cancels_timer_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat.project import load_project

    from scopecat_server import author_worker

    (tmp_path / "scopecat.toml").write_text("[lab]\n")
    project = load_project(tmp_path / "scopecat.toml")
    # Validation runs in a child in production. In this direct unit call, own
    # and restore its environment mutation so session-scoped lab fixtures keep
    # their endpoint regardless of test order or shard assignment.
    monkeypatch.setenv(DAEMON_URL_ENV, "http://validation-fixture.invalid")
    with (
        patch.object(faulthandler, "dump_traceback_later") as timer,
        patch.object(faulthandler, "cancel_dump_traceback_later") as cancel,
        patch("scopecat.project.Project.load_application") as load,
    ):
        load.return_value = SimpleNamespace(authors=None)
        author_worker.validate(project.root, project.root)
        assert timer.call_args.args == (30,)
        cancel.assert_called_once()
        load.side_effect = ValueError("invalid synthetic application")
        with pytest.raises(ValueError, match="invalid synthetic"):
            author_worker.validate(project.root, project.root)
        assert cancel.call_count == 2


@pytest.mark.parametrize("refresh", [False, True])
def test_validation_timeout_is_supported_http_error(refresh: bool) -> None:
    service = Mock()
    service.state.side_effect = AuthorValidationTimeout("validation phase evidence")
    service.refresh.side_effect = AuthorValidationTimeout("validation phase evidence")
    application = cast(
        "DaemonApplication",
        cast("object", SimpleNamespace(author_revisions=service)),
    )
    client = TestClient(create_app(application))
    response = (
        client.post("/api/v1/author-revisions/refresh", json={"expected_generation": 0})
        if refresh
        else client.get("/api/v1/author-revisions")
    )
    assert response.status_code == 504
    assert response.json()["detail"] == "validation phase evidence"


def test_slow_validation_retains_stack_and_reaps_worker(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "src/authors").mkdir(parents=True)
    (tmp_path / "scopecat.toml").write_text(
        '[lab]\napplication="authors.slow:create_application"\n'
        '[authors]\nsource_roots=["src"]\nrefresh_roots=["src/authors"]\n'
    )
    pid_file = tmp_path / "worker.pid"
    owned: list[psutil.Process] = []
    launched: list[subprocess.Popen[str]] = []
    receive_timeouts: list[float] = []
    receive = _Worker.receive

    def observe_receive(
        worker: _Worker, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        receive_timeouts.append(timeout)
        launched.append(worker.process)
        deadline = time.monotonic() + timeout
        while not pid_file.exists():
            assert worker.process.poll() is None
            assert time.monotonic() < deadline, "slow fixture never became ready"
            time.sleep(0.01)
        return receive(worker, 0.1)

    def observe_cleanup(
        process: subprocess.Popen[str], *, owner: psutil.Process | None
    ) -> tuple[psutil.Process, ...]:
        result = terminate_validation_process_tree(process, owner=owner)
        owned.extend(result)
        return result

    (tmp_path / "src/authors/slow.py").write_text(
        "import faulthandler, json, os, sys, time, psutil\n"
        "def create_application(root):\n"
        "    faulthandler.dump_traceback(file=sys.stderr)\n"
        f"    output = open({str(pid_file)!r}, 'w')\n"
        "    output.write(json.dumps({'pid': os.getpid(), "
        "'created': psutil.Process().create_time()}))\n"
        "    output.close()\n"
        "    time.sleep(120)\n"
    )
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    try:
        service = AuthorRevisionService(tmp_path, store)
        with (
            patch.object(_Worker, "receive", observe_receive),
            patch(
                "scopecat_server.services.revision_workers.subprocess.Popen",
                wraps=subprocess.Popen,
            ) as launch,
            patch(
                "scopecat_server.services.revision_workers.terminate_validation_process_tree",
                side_effect=observe_cleanup,
            ),
            pytest.raises(
                AuthorValidationTimeout, match="application import"
            ) as caught,
        ):
            service.refresh(expected_generation=0)
        launch.assert_called_once()
        assert len(receive_timeouts) == 1
        assert 0 < receive_timeouts[0] <= 60
        assert "did not publish" in str(caught.value)
        assert service.repository.state().active is None
        worker = json.loads(pid_file.read_text())
        matching = [
            item
            for item in owned
            if item.pid == worker["pid"] and item.create_time() == worker["created"]
        ]
        assert matching, [(item.pid, item.create_time()) for item in owned]
        assert all(not item.is_running() for item in owned)
        assert launched[0].returncode is not None
        assert launched[0].poll() == launched[0].returncode
        assert "owned process identities" in caplog.text
        assert "slow.py" in caplog.text
        assert "create_application" in caplog.text
        assert "stage=application import" in caplog.text
    finally:
        store.close()


def test_timeout_cleanup_includes_launcher_descendants(tmp_path: Path) -> None:
    """An explicit waiting launcher reproduces the extra process boundary everywhere."""
    marker = tmp_path / "child.json"
    child_code = (
        "import json, os, sys, time, psutil; "
        f"output = open({str(marker.with_suffix('.tmp'))!r}, 'w'); "
        "output.write(json.dumps({'pid': os.getpid(), "
        "'created': psutil.Process().create_time()})); output.close(); "
        f"os.replace({str(marker.with_suffix('.tmp'))!r}, {str(marker)!r}); "
        "print('slow descendant evidence', file=sys.stderr, flush=True); "
        "time.sleep(120)"
    )
    launcher_code = (
        "import subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "child.wait()"
    )
    process = subprocess.Popen(  # noqa: S603 - controlled fixture code
        [sys.executable, "-c", launcher_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    owner = psutil.Process(process.pid)
    actual_child: psutil.Process | None = None
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "controlled descendant did not start"
        child = json.loads(marker.read_text())
        actual_child = psutil.Process(child["pid"])
        assert actual_child.create_time() == child["created"]
        assert child["pid"] != process.pid
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(timeout=0.1)
        owned = terminate_validation_process_tree(process, owner=owner)
        _, stderr = process.communicate(timeout=5)
        assert any(
            item.pid == child["pid"] and item.create_time() == child["created"]
            for item in owned
        )
        assert all(not item.is_running() for item in owned)
        assert process.returncode is not None
        assert "slow descendant evidence" in stderr
    finally:
        if actual_child is not None and actual_child.is_running():
            actual_child.kill()
            actual_child.wait(timeout=5)
        if process.poll() is None:
            terminate_validation_process_tree(process, owner=owner)


def test_interrupted_validation_cleans_up_and_preserves_interrupt(
    tmp_path: Path,
) -> None:
    (tmp_path / "src/authors").mkdir(parents=True)
    (tmp_path / "scopecat.toml").write_text(
        '[lab]\n[authors]\nsource_roots=["src"]\nrefresh_roots=["src/authors"]\n'
    )
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    worker = Mock(spec=_Worker)
    interruption = KeyboardInterrupt()
    worker.receive.side_effect = interruption
    try:
        service = AuthorRevisionService(tmp_path, store)
        with (
            patch(
                "scopecat_server.services.revision_workers._Worker", return_value=worker
            ) as launch,
            pytest.raises(KeyboardInterrupt) as caught,
        ):
            service.refresh(expected_generation=0)
        assert caught.value is interruption
        launch.assert_called_once()
        worker.close.assert_called_once()
        assert service.repository.state().active is None
    finally:
        store.close()
