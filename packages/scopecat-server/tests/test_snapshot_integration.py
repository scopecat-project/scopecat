"""The installed-project checker also runs in the ordinary Python test gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scopecat.daemon.endpoint import DAEMON_URL_ENV


def test_reference_lab_snapshot_roundtrip_in_fresh_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DAEMON_URL_ENV, "http://127.0.0.1:1")
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local test fixture
        [
            sys.executable,
            str(root / "packages/scopecat-server/tests/fixtures/snapshot_roundtrip.py"),
            str(root / "examples/reference_lab"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_snapshot_start_failure_keeps_daemon_log_and_original_error(
    tmp_path: Path,
) -> None:
    import runpy
    from collections.abc import Callable
    from typing import cast
    from unittest.mock import Mock, patch

    from scopecat.project import Project

    from scopecat_server.lifecycle import DaemonLifecycleError

    root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(
        str(root / "packages/scopecat-server/tests/fixtures/snapshot_roundtrip.py")
    )
    start = cast("Callable[[Project], None]", namespace["_start_fixture_project"])
    state = tmp_path / ".scopecat"
    state.mkdir()
    (state / "daemon.log").write_bytes(b"old" * 4096 + b"child startup stage")
    project = Mock(spec=Project, root=tmp_path)
    failure = DaemonLifecycleError("original startup failure")
    cleanup = Mock(side_effect=DaemonLifecycleError("cleanup failure"))
    with (
        patch.dict(
            start.__globals__,
            start_project=Mock(side_effect=failure),
            stop_project=cleanup,
        ),
        pytest.raises(DaemonLifecycleError) as caught,
    ):
        start(project)
    assert caught.value is failure
    assert failure.__notes__[0].endswith("child startup stage")
    assert len(failure.__notes__[0]) < 8300
    assert failure.__notes__[1] == "Startup cleanup: cleanup failure"
    cleanup.assert_called_once_with(project)
