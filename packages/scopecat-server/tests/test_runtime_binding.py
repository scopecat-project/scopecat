"""Ownership and durable history across independently located code workspaces."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scopecat.project import open_project
from scopecat.project_sources import capture_sources
from scopecat.runtime_binding import RuntimeBindingError, load_runtime_binding

from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.snapshots import create_snapshot, restore_snapshot


def bind(workspace: Path, data: Path, deployment: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "scopecat.toml").write_text("[lab]\n", encoding="utf-8")
    (workspace / "scopecat.runtime.toml").write_text(
        "[runtime]\n"
        f"data_root = {data.as_posix()!r}\n"
        f"deployment_root = {deployment.as_posix()!r}\n",
        encoding="utf-8",
    )


def test_two_workspaces_share_one_stable_data_identity(tmp_path: Path) -> None:
    data, deployment = tmp_path / "data", tmp_path / "bench"
    a, b = tmp_path / "a", tmp_path / "b"
    for workspace in (a, b):
        bind(workspace, data, deployment)
    with LocalDaemonRuntime(a) as first:
        identity = first.application.health().project_id
        with pytest.raises(RuntimeError, match="running daemon"):
            LocalDaemonRuntime(b)
    shutil.rmtree(a)
    with LocalDaemonRuntime(b) as second:
        assert second.application.health().project_id == identity
        assert second.state_dir == data
    assert not (b / ".scopecat/control.sqlite3").exists()


def test_deployment_and_data_reservations_are_independent(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    bind(a, tmp_path / "data-a", tmp_path / "bench")
    bind(b, tmp_path / "data-b", tmp_path / "bench")
    with LocalDaemonRuntime(a):
        with pytest.raises(RuntimeError, match="deployment owner"):
            LocalDaemonRuntime(b)
        # Same data, another deployment must also fail and release its bench lock.
        bind(b, tmp_path / "data-a", tmp_path / "other-bench")
        with pytest.raises(RuntimeError, match="running daemon"):
            LocalDaemonRuntime(b)
        bind(b, tmp_path / "data-b", tmp_path / "other-bench")
        with LocalDaemonRuntime(b):
            pass


def test_runtime_locations_do_not_change_captured_code(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    bind(a, tmp_path / "data-a", tmp_path / "bench-a")
    bind(b, tmp_path / "data-b", tmp_path / "bench-b")
    assert (
        capture_sources(open_project(a)).manifest
        == capture_sources(open_project(b)).manifest
    )


def test_restore_external_state_and_receipts_without_old_locations(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data, deployment = tmp_path / "data", tmp_path / "bench"
    bind(workspace, data, deployment)
    with LocalDaemonRuntime(workspace) as runtime:
        identity = runtime.application.health().project_id
    receipts = data / "author-jobs"
    receipts.mkdir()
    (receipts / "retained.json").write_text('{"run_id": "evidence"}', encoding="utf-8")
    snapshot, restored = tmp_path / "snapshot", tmp_path / "restored"
    create_snapshot(open_project(workspace), snapshot)
    for path in (workspace, data, deployment):
        shutil.rmtree(path)
    restore_snapshot(snapshot, restored)
    assert not (restored / "scopecat.runtime.toml").exists()
    assert not (restored / ".scopecat/daemon.json").exists()
    assert (restored / ".scopecat/author-jobs/retained.json").read_text(
        encoding="utf-8"
    ) == '{"run_id": "evidence"}'
    with LocalDaemonRuntime(restored) as runtime:
        assert runtime.application.health().project_id == identity


def test_runtime_binding_rejects_incomplete_locations(tmp_path: Path) -> None:
    (tmp_path / "scopecat.runtime.toml").write_text(
        '[runtime]\ndata_root = "../data"\n', encoding="utf-8"
    )
    with pytest.raises(RuntimeBindingError, match="deployment_root"):
        load_runtime_binding(tmp_path)


def test_workspace_switch_and_restore_journey() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts/verify_workspace_binding.py"
    result = subprocess.run(  # noqa: S603 - fixed local interpreter and fixture
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
