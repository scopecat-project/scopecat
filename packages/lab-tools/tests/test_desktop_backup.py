"""Routine backups use the stopped snapshot contract through the native bridge."""

import threading
from pathlib import Path
from typing import Literal, cast
from unittest.mock import Mock

import pytest
from filelock import FileLock

from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_session import DesktopSession
from scopecat.application_instance import ApplicationInstance
from scopecat.runtime_binding import APPLICATION_INSTANCE_NAME
from scopecat_server.snapshots import restore_snapshot, verify_snapshot
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

Backup = tuple[DesktopAPI, Mock, DesktopSession, Mock, Path]


@pytest.fixture
def backup(tmp_path: Path, request: pytest.FixtureRequest) -> Backup:
    home = tmp_path / "home"
    home.mkdir()
    layout = cast(
        'Literal["application", "legacy"]', getattr(request, "param", "application")
    )
    root = home / "runtime" if layout == "legacy" else home
    root.mkdir(exist_ok=True)
    instance = ApplicationInstance(root, layout)
    (root / APPLICATION_INSTANCE_NAME).write_text(instance.declaration())
    data = instance.runtime_binding.data_root
    store = SQLiteProjectStore(
        SQLiteDatabase(data / "control.sqlite3"), data / "objects"
    )
    store.bootstrap()
    store.close()
    (home / "state").mkdir(exist_ok=True)
    external = tmp_path / "author"
    external.mkdir()
    (external / "source.py").write_text("original source")
    parent = tmp_path / "backups"
    parent.mkdir()
    runtime = Mock(home=home, instance=instance)
    runtime.stop_if_idle.return_value = True
    runtime.start.return_value.base_url = "http://localhost:1234"
    session = DesktopSession(runtime, threading.Event())
    window = Mock()
    window.create_file_dialog.return_value = (str(parent),)
    window.create_confirmation_dialog.return_value = True
    return DesktopAPI(session, lambda: window), runtime, session, window, parent


@pytest.mark.parametrize("cancel", ["folder", "confirmation"])
def test_cancel_does_not_stop_or_capture(backup: Backup, cancel: str) -> None:
    api, runtime, _, window, parent = backup
    if cancel == "folder":
        window.create_file_dialog.return_value = None
    else:
        window.create_confirmation_dialog.return_value = False
    api.backup_application()
    runtime.stop_if_idle.assert_not_called()
    assert list(parent.iterdir()) == []


def test_busy_and_file_transfer_refuse_without_stopping_work(backup: Backup) -> None:
    api, runtime, session, _, parent = backup
    runtime.stop_if_idle.return_value = False
    with pytest.raises(ValueError, match="后台仍有工作"):
        api.backup_application()
    runtime.stop.assert_not_called()
    with session.file_operation(), pytest.raises(ValueError, match="文件操作"):
        api.backup_application()
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize("backup", ["application", "legacy"], indirect=True)
def test_native_backup_roundtrip_and_explicit_reopen(
    backup: Backup, tmp_path: Path
) -> None:
    api, runtime, _, window, parent = backup
    # Cocoa and other hosts return different dialog shapes.
    window.create_file_dialog.return_value = str(parent)
    api.backup_application()
    (snapshot,) = parent.iterdir()
    assert verify_snapshot(snapshot).instance_kind == "application"
    restored = tmp_path / "restored"
    restore_snapshot(snapshot, restored)
    assert (restored / "data/control.sqlite3").is_file()
    assert (tmp_path / "author/source.py").read_text() == "original source"
    page = window.load_html.call_args.args[0]
    assert "备份完成并已校验" in page
    assert str(snapshot) in page
    assert 'onclick="retry()"' in page
    runtime.start.assert_not_called()
    api.retry()
    runtime.start.assert_called_once()


def test_snapshot_lock_failure_preserves_original_and_allows_retry(
    backup: Backup,
) -> None:
    api, runtime, _, window, parent = backup
    database = runtime.home / "data/control.sqlite3"
    original = database.read_bytes()
    with FileLock(runtime.home / "state/deployment.lock"):
        api.backup_application()
    assert "备份未完成或未通过校验" in window.load_html.call_args.args[0]
    assert database.read_bytes() == original
    assert list(parent.iterdir()) == []
    runtime.start.assert_not_called()
    api.backup_application()
    assert len(list(parent.iterdir())) == 1


def test_internal_destination_is_rejected_before_shutdown(backup: Backup) -> None:
    api, runtime, _, window, _ = backup
    window.create_file_dialog.return_value = (str(runtime.home / "data"),)
    with pytest.raises(ValueError, match="之外"):
        api.backup_application()
    runtime.stop_if_idle.assert_not_called()


def test_verification_failure_is_not_reported_as_success(
    backup: Backup, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, runtime, _, window, parent = backup

    def reject(_path: Path) -> None:
        raise ValueError("checksum mismatch <details>")

    monkeypatch.setattr("scopecat_server.snapshots.verify_snapshot", reject)
    api.backup_application()
    page = window.load_html.call_args.args[0]
    assert "备份未完成或未通过校验" in page
    assert "checksum mismatch &lt;details&gt;" in page
    assert "备份完成并已校验" not in page
    assert len(list(parent.iterdir())) == 1
    assert (runtime.home / "data/control.sqlite3").is_file()
    runtime.start.assert_not_called()
