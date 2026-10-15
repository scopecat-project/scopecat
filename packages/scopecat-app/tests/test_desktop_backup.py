"""Routine backups use the stopped snapshot contract through the native bridge."""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast
from unittest.mock import Mock

import pytest
from filelock import FileLock

from scopecat.application_instance import ApplicationInstance
from scopecat.runtime_binding import APPLICATION_INSTANCE_NAME
from scopecat_app.desktop import DesktopAPI
from scopecat_app.desktop_session import DesktopSession
from scopecat_server.snapshots import restore_snapshot, verify_snapshot
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

Backup = tuple[DesktopAPI, Mock, DesktopSession, Mock, Path]


def call_bridge(api: DesktopAPI, action: Callable[[], None]) -> None:
    errors: list[BaseException] = []

    def call() -> None:
        try:
            action()
        except BaseException as error:
            errors.append(error)

    bridge = threading.Thread(target=call)
    bridge.start()
    bridge.join(5)
    assert not bridge.is_alive()
    wait_for_page(api)
    if errors:
        raise errors[0]


def wait_for_page(api: DesktopAPI) -> None:
    # Inspect the retained host thread only to join it deterministically in tests.
    worker = api._page_thread  # pyright: ignore[reportPrivateUsage]
    if worker is not None:
        worker.join(5)
        assert not worker.is_alive()


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
    call_bridge(api, api.backup_application)
    runtime.stop_if_idle.assert_not_called()
    assert list(parent.iterdir()) == []


def test_busy_and_file_transfer_refuse_without_stopping_work(backup: Backup) -> None:
    api, runtime, session, _, parent = backup
    runtime.stop_if_idle.return_value = False
    with pytest.raises(ValueError, match="后台仍有工作"):
        call_bridge(api, api.backup_application)
    runtime.stop.assert_not_called()
    with session.file_operation(), pytest.raises(ValueError, match="文件操作"):
        call_bridge(api, api.backup_application)
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize("backup", ["application", "legacy"], indirect=True)
def test_native_backup_roundtrip_and_explicit_reopen(
    backup: Backup, tmp_path: Path
) -> None:
    api, runtime, _, window, parent = backup
    # Cocoa and other hosts return different dialog shapes.
    window.create_file_dialog.return_value = str(parent)
    call_bridge(api, api.backup_application)
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
    call_bridge(api, lambda: api.retry(True))
    runtime.start.assert_called_once()


def test_snapshot_lock_failure_preserves_original_and_allows_retry(
    backup: Backup,
) -> None:
    api, runtime, _, window, parent = backup
    database = runtime.home / "data/control.sqlite3"
    original = database.read_bytes()
    with FileLock(runtime.home / "state/deployment.lock"):
        call_bridge(api, api.backup_application)
    assert "备份未完成或未通过校验" in window.load_html.call_args.args[0]
    assert database.read_bytes() == original
    assert list(parent.iterdir()) == []
    runtime.start.assert_not_called()
    call_bridge(api, api.backup_application)
    assert len(list(parent.iterdir())) == 1


def test_internal_destination_is_rejected_before_shutdown(backup: Backup) -> None:
    api, runtime, _, window, _ = backup
    window.create_file_dialog.return_value = (str(runtime.home / "data"),)
    with pytest.raises(ValueError, match="之外"):
        call_bridge(api, api.backup_application)
    runtime.stop_if_idle.assert_not_called()


def test_verification_failure_is_not_reported_as_success(
    backup: Backup, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, runtime, _, window, parent = backup

    def reject(_path: Path) -> None:
        raise ValueError("checksum mismatch <details>")

    monkeypatch.setattr("scopecat_server.snapshots.verify_snapshot", reject)
    call_bridge(api, api.backup_application)
    page = window.load_html.call_args.args[0]
    assert "备份未完成或未通过校验" in page
    assert "checksum mismatch &lt;details&gt;" in page
    assert "备份完成并已校验" not in page
    assert len(list(parent.iterdir())) == 1
    assert (runtime.home / "data/control.sqlite3").is_file()
    runtime.start.assert_not_called()


def test_backup_fences_other_bridges_through_capture_and_result(
    backup: Backup, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat_server import snapshots

    api, runtime, session, window, parent = backup
    second = DesktopAPI(session, Mock())
    # An earlier wait-for-idle quit must not interrupt the capture.
    session.wait_for_idle(True)
    stages = []

    def check_fence(stage: str) -> None:
        stages.append(stage)
        for action in (
            second.retry,
            second.restart,
            second.request_exit,
            lambda: second.exit(False),
        ):
            with pytest.raises(ValueError, match="另一项操作"):
                action()
        session.poll_exit()
        assert not session.closing.is_set()
        runtime.start.assert_not_called()
        runtime.stop.assert_not_called()
        runtime.stop_if_idle.assert_called_once()

    capture = snapshots.create_snapshot
    verify = snapshots.verify_snapshot

    def capture_fenced(instance: ApplicationInstance, destination: Path):
        check_fence("capture")
        return capture(instance, destination)

    def verify_fenced(destination: Path):
        check_fence("verify")
        return verify(destination)

    def load_page(page: str) -> None:
        if "备份完成并已校验" in page:
            check_fence("result")

    monkeypatch.setattr(snapshots, "create_snapshot", capture_fenced)
    monkeypatch.setattr(snapshots, "verify_snapshot", verify_fenced)
    window.load_html.side_effect = load_page
    call_bridge(api, api.backup_application)
    assert stages == ["capture", "verify", "result"]
    assert len(list(parent.iterdir())) == 1


@pytest.mark.parametrize("action", ["backup_application", "retry"])
def test_page_replacement_waits_for_real_pywebview_callback(
    backup: Backup, action: str
) -> None:
    from webview.util import js_bridge_call

    api, runtime, _, window, _ = backup
    window._functions = {}
    window._js_api = api
    callback_started, release = threading.Event(), threading.Event()
    callers: list[threading.Thread] = []

    def reply(script: str) -> None:
        assert f'_returnValuesCallbacks["{action}"]["receipt"]' in script
        callers.append(threading.current_thread())
        callback_started.set()
        assert release.wait(5)

    def replace(_page: str) -> None:
        assert len(callers) == 1
        assert not callers[0].is_alive()

    window.evaluate_js.side_effect = reply
    window.load_html.side_effect = replace
    window.run_js.side_effect = replace
    js_bridge_call(
        window, action, [] if action == "backup_application" else [True], "receipt"
    )
    try:
        assert callback_started.wait(5)
        window.load_html.assert_not_called()
        window.run_js.assert_not_called()
        runtime.start.assert_not_called()
        with pytest.raises(ValueError, match="另一项操作"):
            api.restart()
    finally:
        release.set()
        wait_for_page(api)
    if action == "backup_application":
        assert window.load_html.call_count == 2
        assert "备份完成并已校验" in window.load_html.call_args.args[0]
    else:
        runtime.start.assert_called_once()
        window.run_js.assert_called_once()


def test_deferred_reopen_failure_keeps_error_and_releases_admission(
    backup: Backup,
) -> None:
    api, runtime, session, window, _ = backup
    runtime.start.side_effect = ValueError("reopen failed")
    call_bridge(api, lambda: api.retry(True))
    assert "reopen failed" in window.load_html.call_args.args[0]
    assert 'onclick="retry()"' in window.load_html.call_args.args[0]
    with session.operation():
        assert not session.closing.is_set()
