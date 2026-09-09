"""Bounded author validation diagnostics, independently of acquisition."""

from __future__ import annotations

import faulthandler
import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import psutil
import pytest
from fastapi.testclient import TestClient

from scopecat_server.http.transport import create_app
from scopecat_server.services.application import DaemonApplication
from scopecat_server.services.author_revisions import (
    AuthorRevisionService,
    AuthorValidationTimeout,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
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


def test_validation_cancels_timer_on_success_and_failure(tmp_path: Path) -> None:
    from scopecat.project import load_project

    from scopecat_server import author_worker

    (tmp_path / "scopecat.toml").write_text("[lab]\n")
    project = load_project(tmp_path / "scopecat.toml")
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
    (tmp_path / "src/authors/slow.py").write_text(
        "import os, time\ndef create_application(root):\n"
        f"    open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(120)\n"
    )
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    try:
        service = AuthorRevisionService(tmp_path, store)
        with (
            patch(
                "scopecat_server.services.author_revisions.subprocess.run",
                wraps=subprocess.run,
            ) as run,
            pytest.raises(
                AuthorValidationTimeout, match="application import"
            ) as caught,
        ):
            service.refresh(expected_generation=0)
        run.assert_called_once()
        assert run.call_args.kwargs["timeout"] == 60
        assert "did not publish" in str(caught.value)
        assert service.repository.state().active is None
        assert not psutil.pid_exists(int(pid_file.read_text()))
        assert "slow.py" in caplog.text
        assert "create_application" in caplog.text
        assert "stage=application import" in caplog.text
    finally:
        store.close()
