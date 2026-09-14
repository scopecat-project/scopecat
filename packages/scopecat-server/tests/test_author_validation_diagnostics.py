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
from unittest.mock import patch

import psutil
import pytest
from scopecat.daemon.endpoint import DAEMON_URL_ENV

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
        # Popen.wait observes the Windows exit handle; its PID may remain
        # visible briefly. psutil.wait also waits for PID disappearance (as
        # cleanup already does for descendants), before is_running is sampled.
        owner.wait(timeout=5)
        assert all(not item.is_running() for item in owned), [
            (item.pid, item.is_running()) for item in owned
        ]
        assert process.returncode is not None
        assert "slow descendant evidence" in stderr
    finally:
        if actual_child is not None and actual_child.is_running():
            actual_child.kill()
            actual_child.wait(timeout=5)
        if process.poll() is None:
            terminate_validation_process_tree(process, owner=owner)
