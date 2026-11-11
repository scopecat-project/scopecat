"""Native close keeps failure recovery in the same window, without a manager."""

import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, call, patch

import pytest

from scopecat.daemon.exit import ExitReport, ExitWork
from scopecat.daemon.health import ApplicationActivity
from scopecat_app.desktop import (
    DesktopAPI,
    DesktopView,
    DesktopWindows,
    _window_close_handlers,
)
from scopecat_app.desktop_session import DesktopSession


@pytest.mark.parametrize("stop", [False, True])
def test_file_work_allows_other_windows_and_participates_in_quit(stop: bool):
    runtime = Mock()
    runtime.activity.return_value.model_dump.return_value = {}
    runtime.stop_if_idle.return_value = True
    session = DesktopSession(runtime, threading.Event())
    started, release = threading.Event(), threading.Event()

    def transfer():
        with session.file_operation() as cancel:
            started.set()
            assert (cancel if stop else release).wait(5)

    worker = threading.Thread(target=transfer)
    worker.start()
    try:
        assert started.wait(5)
        # File work in one window does not lock another window's commands.
        with session.operation(allow_files=True):
            pass
        with pytest.raises(ValueError, match="文件操作"):
            DesktopAPI(session, Mock()).restart()
        assert session.request_exit() == {"file_operations": 1}
        runtime.stop_if_idle.assert_not_called()
        session.wait_for_idle(True)
        session.poll_exit()
        runtime.stop_if_idle.assert_not_called()
        if stop:
            session.exit()
            runtime.stop.assert_called_once()
        else:
            release.set()
            worker.join(5)
            session.poll_exit()
            runtime.stop_if_idle.assert_called_once()
        assert session.closing.is_set()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_native_windows_share_backend_and_keep_navigation(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    created: list[tuple[Mock, dict[str, object]]] = []

    def create(_title: str, **kwargs: object):
        window = Mock()
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        window.get_current_url.return_value = kwargs["url"]
        window.destroy.side_effect = window.events.closed.set
        created.append((window, kwargs))
        return window

    monkeypatch.setattr("webview.create_window", create)
    runtime, prepare = Mock(), Mock()
    runtime.start.return_value = SimpleNamespace(base_url="http://localhost:1234")
    session = DesktopSession(runtime, threading.Event())
    windows = DesktopWindows(session, prepare)
    first = windows.create()
    assert isinstance(first.window, Mock)
    first.api.retry()
    first.window.run_js.assert_called_once_with(
        'window.location.replace("http://localhost:1234");'
    )
    first.window.run_js.reset_mock()
    first.window.get_current_url.return_value = "http://localhost:1234/?run=A#runs"
    first.api.open_run_window("B &?#/结果")
    second = windows.latest
    assert second is not first
    assert isinstance(second.window, Mock)
    assert (
        created[1][1]["url"]
        == "http://localhost:1234/?run=B+%26%3F%23%2F%E7%BB%93%E6%9E%9C&focus=run#runs"
    )
    first.api.open_run_window("B &?#/结果")
    assert len(created) == 2
    assert windows.latest is second
    # A global menu targets the focused window, not the most recently created one.
    monkeypatch.setattr("webview.active_window", lambda: first.window)
    windows.open_file()
    first.window.run_js.assert_called_once_with(
        "window.dispatchEvent(new Event('scopecat:open-file'));"
    )
    second.window.run_js.assert_not_called()
    windows.navigate_history(backward=True)
    first.window.run_js.assert_called_with(
        "window.dispatchEvent(new Event('scopecat:back'));"
    )
    windows.navigate_history(backward=False)
    first.window.run_js.assert_called_with(
        "window.dispatchEvent(new Event('scopecat:forward'));"
    )
    windows.zoom("in")
    first.window.run_js.assert_called_with(
        "window.dispatchEvent(new Event('scopecat:zoom-in'));"
    )
    windows.find()
    first.window.run_js.assert_called_with(
        "window.dispatchEvent(new Event('scopecat:find'));"
    )
    second.window.run_js.assert_not_called()
    prepare.assert_called_once()
    runtime.start.assert_called_once()
    second.window.get_current_url.return_value = "http://localhost:1234/?run=B#runs"
    session.connected("http://localhost:4321")
    first.window.run_js.assert_called_with(
        'window.location.replace("http://localhost:4321/?run=A#runs");'
    )
    second.window.run_js.assert_called_with(
        'window.location.replace("http://localhost:4321/?run=B#runs");'
    )

    windows.tray_changed(True)
    # Tray hide/open is application-wide: neither retained view may be stranded.
    shown = Mock()
    monkeypatch.setattr("scopecat_app.desktop.show_window", shown)
    windows.hide()
    first.window.hide.assert_called_once()
    second.window.hide.assert_called_once()
    windows.show()
    assert [call.args[0] for call in shown.call_args_list] == [
        first.window,
        second.window,
    ]
    assert len(created) == 2
    runtime.start.assert_called_once()

    # Maintenance keeps both bridges alive even if every window is closed.
    # This exercises pywebview events with fake windows, not native GUI behavior.
    with session.operation():
        assert first.window.events.closing.set() is True
        assert second.window.events.closing.set() is True
        assert not session.closing.is_set()
        first.window.destroy.assert_not_called()
        second.window.destroy.assert_not_called()
    shown.reset_mock()
    windows.show()
    assert [call.args[0] for call in shown.call_args_list] == [
        first.window,
        second.window,
    ]

    # pywebview returns True from Event.set when a handler vetoes the close.
    def confirm_close(script: str):
        if "scopecat:close-preflight" in script:
            assert first.close_request is not None
            first.api.complete_close_preflight(first.close_request, "saved")

    first.window.run_js.side_effect = confirm_close
    destroyed = threading.Event()

    def destroy_first():
        first.window.events.closed.set()
        destroyed.set()

    first.window.destroy.side_effect = destroy_first
    assert first.window.events.closing.set() is True
    assert destroyed.wait(5)
    assert second.window.events.closing.set() is True
    assert windows.latest is second
    shown.reset_mock()
    windows.show()
    shown.assert_called_once_with(second.window)
    runtime.stop.assert_not_called()
    acknowledge(second)
    second.api.exit(False)
    windows.destroy()
    second.window.destroy.assert_called_once()


def test_two_windows_share_quit_decision_and_operation_lock():
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    session = DesktopSession(runtime, threading.Event())
    first, second = Mock(), Mock()
    first_api = DesktopAPI(session, lambda: first)
    second_api = DesktopAPI(session, lambda: second)

    assert first_api.request_exit() == {"runs": 1}
    first_api.wait_for_idle(True)
    # A decision in another window cancels the same pending application quit.
    second_api.exit(True)
    session.poll_exit()
    runtime.stop_if_idle.assert_called_once()
    first.hide.assert_not_called()
    second.hide.assert_called_once()
    assert not session.closing.is_set()

    with session.operation(), pytest.raises(ValueError, match="另一项操作"):
        second_api.restart()
    runtime.stop.assert_not_called()

    bridge = threading.Thread(target=second_api.exit, args=(False,))
    bridge.start()
    bridge.join(5)
    assert session.closing.is_set()
    with pytest.raises(ValueError, match="正在关闭"):
        first_api.restart()
    runtime.stop.assert_called_once()

    def destroy_windows() -> None:
        first.destroy()
        second.destroy()

    session.finish_exit(destroy_windows)
    first.destroy.assert_called_once()
    second.destroy.assert_called_once()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_window_close_hides_without_requesting_quit(
    platform: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("scopecat_app.desktop.sys.platform", platform)
    window = Mock()
    if platform == "darwin":
        window.restore.side_effect = AssertionError("Cocoa hide must not deminiaturize")
    hidden = threading.Event()
    window.hide.side_effect = hidden.set
    closing, loaded = threading.Event(), threading.Event()
    loaded.set()
    close, _quit = _window_close_handlers(window, closing, loaded)
    assert close() is False
    assert hidden.wait(2)
    window.run_js.assert_not_called()
    window.destroy.assert_not_called()
    closing.set()
    assert close() is True


def test_new_source_preserves_existing_files(tmp_path: Path):
    source = tmp_path / "experiments"
    source.mkdir()
    notes = source / "notes.txt"
    notes.write_text("keep")
    runtime = Mock()
    api = DesktopAPI(DesktopSession(runtime, threading.Event()), Mock())
    with pytest.raises(FileExistsError):
        api.create_source(str(tmp_path), "experiments")
    assert notes.read_text() == "keep"
    runtime.stop_if_idle.assert_not_called()


def test_busy_source_creation_retains_folder_without_interrupting_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def create_client_environment(*_: object) -> None:
        return None

    def prepare_execution_environment(*_: object) -> Path:
        return tmp_path / "execution/python"

    monkeypatch.setattr(
        "scopecat_app.author_environment.create_client_environment",
        create_client_environment,
    )
    monkeypatch.setattr(
        "scopecat_app.author_environment.prepare_execution_environment",
        prepare_execution_environment,
    )
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    window = Mock()
    api = DesktopAPI(DesktopSession(runtime, threading.Event()), lambda: window)
    runtime.start.return_value.base_url = "http://127.0.0.1:1234"
    api.create_source(str(tmp_path), "experiments")
    assert (tmp_path / "experiments/notebooks/02_edit_scan.py").is_file()
    runtime.stop.assert_not_called()
    runtime.stop_if_idle.assert_not_called()
    runtime.register_source.assert_called_once()


@pytest.mark.parametrize("name", ["../outside", "..", "C:outside", "nested\\folder"])
def test_new_source_name_cannot_escape_selected_parent(tmp_path: Path, name: str):
    api = DesktopAPI(DesktopSession(Mock(), threading.Event()), Mock())
    with pytest.raises(ValueError, match="单个新目录"):
        api.create_source(str(tmp_path), name)
    assert list(tmp_path.iterdir()) == []


def test_folder_picker_cancellation_does_not_change_runtime():
    runtime = Mock()
    window = Mock()
    window.create_file_dialog.return_value = None
    api = DesktopAPI(DesktopSession(runtime, threading.Event()), lambda: window)
    assert api.choose_directory() is None
    assert runtime.mock_calls == []


def test_registration_validates_selected_folder_before_preparing_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scopecat_server.scaffold import write_author_scaffold

    source = tmp_path / "experiments"
    write_author_scaffold(source)
    nested = source / "unrelated"
    nested.mkdir()
    (nested / "pyproject.toml").write_text('[project]\nname = "unrelated"\n')
    prepare = Mock()
    monkeypatch.setattr(
        "scopecat_app.author_environment.prepare_execution_environment", prepare
    )
    runtime = Mock()
    api = DesktopAPI(DesktopSession(runtime, threading.Event()), Mock())
    with pytest.raises(ValueError, match="cannot read project manifest"):
        api.register_source(str(nested), str(nested / "python"))
    prepare.assert_not_called()
    assert runtime.mock_calls == []


def test_busy_close_keeps_window_and_service_until_explicit_choice():
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(DesktopSession(runtime, closing), lambda: window)
    assert api.request_exit() == {"runs": 1}
    runtime.stop.assert_not_called()
    assert not closing.is_set()
    window.destroy.assert_not_called()


def test_waiting_exit_can_be_cancelled_without_stopping_work():
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    api = DesktopAPI(DesktopSession(runtime, threading.Event()), Mock())
    api.wait_for_idle(True)
    api._session.poll_exit()
    runtime.stop_if_idle.assert_called_once()
    api.wait_for_idle(False)
    api._session.poll_exit()
    runtime.stop_if_idle.assert_called_once()


def test_waiting_exit_does_not_replace_an_in_progress_lifecycle_operation():
    runtime = Mock()
    closing = threading.Event()
    api = DesktopAPI(DesktopSession(runtime, closing), Mock())
    api.wait_for_idle(True)
    with api._session.operation(allow_wait=True):
        api._session.poll_exit()
    runtime.stop_if_idle.assert_not_called()
    assert not closing.is_set()
    runtime.stop_if_idle.return_value = True
    api._session.poll_exit()
    assert closing.is_set()


def finish_exit(api: DesktopAPI, background: bool):
    bridge = threading.Thread(target=api.exit, args=(background,))
    bridge.start()
    bridge.join()
    if not background:
        api._session.finish_exit(api._window().destroy)


@pytest.mark.parametrize("background", [True, False])
def test_close_obeys_explicit_background_choice(background: bool):
    runtime = Mock()
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(DesktopSession(runtime, closing), lambda: window)
    finish_exit(api, background)
    assert runtime.stop.call_count == (0 if background else 1)
    assert closing.is_set() is not background
    if background:
        window.hide.assert_called_once()
        window.destroy.assert_not_called()
    else:
        window.destroy.assert_called_once()


def test_failed_stop_keeps_window_available():
    runtime = Mock()
    runtime.stop.side_effect = ValueError("release pending")
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(DesktopSession(runtime, closing), lambda: window)
    with pytest.raises(ValueError, match="release pending"):
        api.exit(False)
    assert not closing.is_set()
    window.destroy.assert_not_called()
    finish_exit(api, True)
    assert not closing.is_set()
    window.hide.assert_called_once()


def test_close_during_preparation_does_not_abandon_installer():
    runtime = Mock()
    runtime.start.return_value = SimpleNamespace(base_url="http://127.0.0.1:1234")
    window = Mock()
    closing = threading.Event()

    def prepare():
        with pytest.raises(ValueError, match="另一项操作"):
            api.exit(True)
        window.destroy.assert_not_called()

    api = DesktopAPI(DesktopSession(runtime, closing), lambda: window, prepare)
    api.retry()
    finish_exit(api, True)
    window.hide.assert_called_once()
    window.destroy.assert_not_called()


def test_window_survives_until_bridge_has_delivered_exit_reply():
    runtime = Mock()
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(DesktopSession(runtime, closing), lambda: window)
    reply = threading.Event()

    def bridge_call():
        api.exit(False)
        # The bridge still needs the live page after the exposed API returns.
        window.destroy.assert_not_called()
        assert reply.wait(5)

    bridge = threading.Thread(target=bridge_call)
    bridge.start()
    assert closing.wait(5)
    supervisor = threading.Thread(
        target=lambda: api._session.finish_exit(window.destroy)
    )
    supervisor.start()
    window.destroy.assert_not_called()
    reply.set()
    supervisor.join(5)
    bridge.join(5)
    assert not supervisor.is_alive()
    window.destroy.assert_called_once()


def test_automatic_quit_cannot_report_cancelled_during_stop():
    runtime = Mock()
    api = DesktopAPI(DesktopSession(runtime, threading.Event()), Mock())
    api.wait_for_idle(True)
    stopping = threading.Event()
    finish = threading.Event()

    def stop():
        stopping.set()
        assert finish.wait(5)
        return True

    runtime.stop_if_idle.side_effect = stop
    poll = threading.Thread(target=api._session.poll_exit)
    poll.start()
    try:
        assert stopping.wait(5)
        with pytest.raises(ValueError, match="另一项操作"):
            api.wait_for_idle(False)
    finally:
        finish.set()
        poll.join(5)
    assert not poll.is_alive()
    with pytest.raises(ValueError, match="正在关闭"):
        api.wait_for_idle(False)


def test_failed_restart_preparation_keeps_window_available():
    runtime = Mock()
    window = Mock()
    prepare = Mock(side_effect=ValueError("missing dependency"))
    closing = threading.Event()
    api = DesktopAPI(DesktopSession(runtime, closing), lambda: window, prepare)
    with pytest.raises(ValueError, match="missing dependency"):
        api.restart()
    runtime.stop_if_idle.assert_called_once()
    runtime.stop.assert_not_called()
    prepare.assert_called_once()
    runtime.start.assert_not_called()
    window.run_js.assert_not_called()
    window.destroy.assert_not_called()
    assert not closing.is_set()


def test_interrupted_selection_can_retry_without_closing_window():
    runtime = Mock()
    runtime.start.return_value = SimpleNamespace(base_url="http://localhost:1234")
    window = Mock()
    prepare = Mock(side_effect=[OSError("interrupted selection"), None])
    api = DesktopAPI(
        DesktopSession(runtime, threading.Event()), lambda: window, prepare
    )
    with pytest.raises(OSError, match="interrupted selection"):
        api.retry()
    runtime.start.assert_not_called()
    window.destroy.assert_not_called()
    api.retry()
    assert prepare.call_count == 2
    window.run_js.assert_called_once_with(
        'window.location.replace("http://localhost:1234");'
    )


@pytest.mark.parametrize("run_id", ["", "   ", None, 123, "x" * 513])
def test_run_window_rejects_invalid_bridge_input(run_id: object):
    session = DesktopSession(Mock(), threading.Event())
    create = Mock()
    api = DesktopAPI(session, Mock(), new_window=create)
    with pytest.raises(ValueError, match="run"):
        api.open_run_window(run_id)
    create.assert_not_called()


def test_new_window_keeps_root_and_run_window_requires_ready_service():
    session = DesktopSession(Mock(), threading.Event())
    create = Mock()
    api = DesktopAPI(session, Mock(), new_window=create)
    api.new_window()
    create.assert_called_once_with(None)
    windows = DesktopWindows(session, Mock())
    with pytest.raises(ValueError, match="准备"):
        windows.new_window("run-a")


def test_development_windows_reload_same_ui_origin_after_proxy_changes():
    runtime = Mock()
    session = DesktopSession(runtime, threading.Event())
    session.ui_url = "http://127.0.0.1:5173"
    session.connected("http://127.0.0.1:9000")
    windows = DesktopWindows(session, Mock())
    first, second = Mock(), Mock()
    first.saved_measurement_id = second.saved_measurement_id = None
    first.window.get_current_url.return_value = session.ui_url + "/?run=A#runs"
    second.window.get_current_url.return_value = session.ui_url + "/?run=B#runs"
    windows._views = [first, second]
    proxy = Mock()
    session.endpoint_changed = proxy
    session.connected("http://127.0.0.1:9001")
    proxy.assert_called_once_with("http://127.0.0.1:9001")
    first.window.run_js.assert_called_once_with(
        'window.location.replace("http://127.0.0.1:5173/?run=A#runs");'
    )
    second.window.run_js.assert_called_once_with(
        'window.location.replace("http://127.0.0.1:5173/?run=B#runs");'
    )
    api = DesktopAPI(session, lambda: first.window)
    assert api._data_url() == "http://127.0.0.1:9001"


def test_lost_wait_response_keeps_pending_exit_and_cancel_restores_admission():
    runtime = Mock()
    runtime.set_exit_wait.side_effect = [OSError("response lost"), None]
    session = DesktopSession(runtime, threading.Event())
    with pytest.raises(OSError, match="response lost"):
        session.wait_for_idle(True)
    assert session._waiting.is_set()
    with pytest.raises(ValueError, match="取消等待"), session.file_operation():
        pytest.fail("new file operation was admitted")
    session.wait_for_idle(False)
    assert not session._waiting.is_set()
    assert runtime.set_exit_wait.call_args_list == [call(True), call(False)]
    with session.file_operation():
        pass


def close_fixture() -> tuple[Mock, DesktopSession, DesktopWindows, list[DesktopView]]:
    runtime = Mock()
    runtime.stop_if_idle.return_value = True
    session = DesktopSession(runtime, threading.Event())
    windows = DesktopWindows(session, Mock())
    windows.preflight_timeout = 0.03
    views: list[DesktopView] = []
    for _ in range(2):
        window = Mock()
        api = DesktopAPI(session, lambda window=window: window)
        view = DesktopView(window, api, threading.Event(), Mock())
        api.close_reply = lambda request, status, view=view: windows._reply(
            view, request, status
        )
        api.close_review_status = windows._review_status
        api.focus_close_review = windows._show_close_blocker
        api.cancel_close_review = windows._cancel_close_review
        views.append(view)
    windows._views = views
    return runtime, session, windows, views


def mock_window(view: DesktopView) -> Mock:
    assert isinstance(view.window, Mock)
    return view.window


def acknowledge(view: DesktopView, status: str = "saved"):
    def dispatch(script: str):
        if "scopecat:close-preflight" in script:
            assert view.close_request is not None
            view.api.complete_close_preflight(view.close_request, status)

    mock_window(view).run_js.side_effect = dispatch


@pytest.mark.parametrize("status", ["blocked", "unknown", None])
def test_quit_preflight_failure_preserves_all_views_and_daemon(status: str | None):
    runtime, session, windows, views = close_fixture()
    acknowledge(views[0])
    if status:
        acknowledge(views[1], status)
    with pytest.raises(ValueError, match="confirmed"):
        views[0].api.request_exit()
    assert not session.closing.is_set()
    runtime.stop_if_idle.assert_not_called()
    runtime.stop.assert_not_called()
    for view in views:
        mock_window(view).destroy.assert_not_called()
        assert any(
            "close-release" in call.args[0]
            for call in mock_window(view).run_js.call_args_list
        )
    assert len(windows._views) == 2


def test_all_view_preflight_releases_after_busy_inspection_and_rechecks_stop():
    runtime, session, _windows, views = close_fixture()
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    for view in views:
        acknowledge(view)
    assert views[0].api.request_exit() == {"runs": 1}
    assert not session.closing.is_set()
    for view in views:
        assert any(
            "close-release" in call.args[0]
            for call in mock_window(view).run_js.call_args_list
        )
    acknowledge(views[1], "blocked")
    with pytest.raises(ValueError, match="confirmed"):
        views[0].api.exit(False)
    runtime.stop.assert_not_called()


def test_preflight_rejects_stale_ack_and_prevents_new_view():
    runtime, session, windows, views = close_fixture()

    def dispatch(script: str):
        if "scopecat:close-preflight" in script:
            views[0].api.complete_close_preflight("old-request", "saved")
            assert not views[0].close_ready.is_set()
            with pytest.raises(ValueError, match="saving"):
                windows.create()
            assert views[0].close_request is not None
            views[0].api.complete_close_preflight(views[0].close_request, "saved")

    mock_window(views[0]).run_js.side_effect = dispatch
    acknowledge(views[1])
    with windows.preflight():
        assert not session.closing.is_set()
    runtime.stop.assert_not_called()


def test_deferred_exit_obtains_fresh_all_view_preflight():
    runtime, session, _windows, views = close_fixture()
    for view in views:
        acknowledge(view)
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    assert views[0].api.request_exit() == {"runs": 1}
    views[0].api.wait_for_idle(True)
    acknowledge(views[1], "blocked")
    with pytest.raises(ValueError, match="confirmed"):
        session.poll_exit()
    assert not session.closing.is_set()
    assert runtime.stop_if_idle.call_count == 1
    acknowledge(views[1])
    runtime.stop_if_idle.return_value = True
    session.poll_exit()
    assert not session.closing.is_set()
    assert not session._waiting.is_set()
    runtime.set_exit_wait.assert_called_with(False)
    # Resolving the blocker does not silently re-arm a cancelled exit intent.
    assert views[0].api.request_exit() is None
    assert session.closing.is_set()
    assert runtime.stop_if_idle.call_count == 2


def test_deferred_device_exit_confirms_once_but_rechecks_fresh_saved_input():
    runtime, session, _windows, views = close_fixture()
    view = views[0]
    view.device_id = "device-a"
    for item in views:
        acknowledge(item)
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    runtime.stop_if_idle.return_value = False
    runtime.exit_report.return_value = ExitReport(
        draining=True, finishing=(ExitWork(kind="run", identity="run", reason="busy"),)
    )
    assert view.api.request_exit() == {"runs": 1}
    initial = view.close_status
    view.api.wait_for_idle(True)
    for _ in range(3):
        assert view.api.request_exit() == {"runs": 1}
        view.api.wait_for_idle(True)
        session.poll_exit()
    assert view.close_status == initial
    mock_window(view).create_confirmation_dialog.assert_not_called()
    runtime.stop_if_idle.assert_called_once()
    runtime.set_exit_wait.assert_called_once_with(True)

    runtime.exit_report.return_value = ExitReport(draining=True)
    requests: list[str] = []

    def recheck(script: str):
        if "scopecat:close-preflight" in script:
            assert view.close_request is not None
            requests.append(view.close_request)
            view.api.complete_close_preflight(view.close_request, "saved")

    mock_window(view).run_js.side_effect = recheck
    # A racing backend operation may still veto the atomic idle stop.
    session.poll_exit()
    assert not session.closing.is_set()
    runtime.stop_if_idle.return_value = True
    session.poll_exit()
    assert session.closing.is_set()
    assert len(set(requests)) == 2
    mock_window(view).create_confirmation_dialog.assert_not_called()
    runtime.stop.assert_not_called()


@pytest.mark.parametrize("status", ["blocked", "unknown", None])
@pytest.mark.parametrize("cancel_fails", [False, True])
def test_blocked_deferred_device_exit_never_repeats_dialogs_or_saving(
    status: str | None, cancel_fails: bool
):
    runtime, session, _windows, views = close_fixture()
    view = views[0]
    view.device_id = "device-a"
    for item in views:
        acknowledge(item)
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    runtime.exit_report.return_value = ExitReport(draining=True)
    assert view.api.request_exit() == {"runs": 1}
    session.wait_for_idle(True)
    if status:
        acknowledge(view, status)
    else:
        mock_window(view).run_js.side_effect = None
    if cancel_fails:
        runtime.set_exit_wait.side_effect = OSError("cancellation response lost")
    with pytest.raises(ValueError, match="confirmed"):
        session.poll_exit()
    calls = mock_window(view).run_js.call_count
    for _ in range(3):
        session.poll_exit()
    assert mock_window(view).run_js.call_count == calls
    mock_window(view).create_confirmation_dialog.assert_not_called()
    runtime.stop_if_idle.assert_called_once()
    runtime.stop.assert_not_called()
    assert not session.closing.is_set()
    assert session._waiting.is_set() is cancel_fails
    assert session.exit_status()["error"]
    if cancel_fails:
        with pytest.raises(ValueError, match="取消等待"), session.operation():
            pytest.fail("uncertain cancellation must retain admission fencing")
        with pytest.raises(ValueError, match="取消等待"):
            session.wait_for_idle(True)
        runtime.set_exit_wait.side_effect = None
        session.wait_for_idle(False)
        assert not session._waiting.is_set()
    acknowledge(view)
    runtime.stop_if_idle.return_value = True
    assert view.api.request_exit() is None
    assert session.closing.is_set()
    mock_window(view).create_confirmation_dialog.assert_not_called()


def test_deferred_exit_does_not_invent_device_close_consent():
    runtime, session, _windows, views = close_fixture()
    views[0].device_id = "device-a"
    runtime.exit_report.return_value = ExitReport(draining=True)
    session.wait_for_idle(True)
    with pytest.raises(ValueError, match="设备窗口已改变"):
        session.poll_exit()
    mock_window(views[0]).create_confirmation_dialog.assert_not_called()
    runtime.stop_if_idle.assert_not_called()
    assert not session._waiting.is_set()


def test_deferred_exit_can_finish_when_backend_is_already_stopped():
    runtime, session, _windows, views = close_fixture()
    for view in views:
        acknowledge(view)
    runtime.exit_report.return_value = ExitReport(draining=False)
    runtime.activity.return_value = ApplicationActivity()
    session.wait_for_idle(True)
    session.poll_exit()
    assert session.closing.is_set()
    runtime.stop_if_idle.assert_called_once()


def test_quit_in_progress_cannot_start_a_second_preflight():
    runtime, session, _windows, views = close_fixture()
    views[0].device_id = "device-a"
    acknowledge(views[1])

    def review(script: str):
        if "scopecat:close-preflight" in script:
            with pytest.raises(ValueError, match="另一项操作"):
                session.request_exit()
            request = views[0].close_request
            assert request is not None
            views[0].api.complete_close_preflight(request, "blocked")

    mock_window(views[0]).run_js.side_effect = review
    with pytest.raises(ValueError, match="confirmed"):
        session.request_exit()
    mock_window(views[0]).create_confirmation_dialog.assert_not_called()
    runtime.stop_if_idle.assert_not_called()
    assert not session._waiting.is_set()


def test_development_controller_does_not_retry_a_blocked_device_exit(tmp_path: Path):
    import queue

    from scripts.dev import control_session

    runtime, session, _windows, views = close_fixture()
    views[0].device_id = "device-a"
    for view in views:
        acknowledge(view)
    checks = 0

    def save(script: str):
        nonlocal checks
        if "scopecat:close-preflight" in script:
            checks += 1
            request = views[0].close_request
            assert request is not None
            views[0].api.complete_close_preflight(
                request, "saved" if checks == 1 else "blocked"
            )

    mock_window(views[0]).run_js.side_effect = save
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    runtime.exit_report.return_value = ExitReport(draining=True)
    closing = Mock()
    closing.is_set.return_value = False
    closing.wait.side_effect = [False, False, False, False, True]
    session.closing = closing
    commands: queue.Queue[str] = queue.Queue()
    commands.put("q")
    control_session(session, tmp_path, commands, Mock())
    assert checks == 2
    mock_window(views[0]).create_confirmation_dialog.assert_not_called()
    assert not session._waiting.is_set()
    assert runtime.set_exit_wait.call_args_list == [call(True), call(False)]
    runtime.stop_if_idle.assert_called_once()
    runtime.stop.assert_not_called()
    closing.set.assert_not_called()


def test_close_reserves_last_view_until_actual_closed_event():
    runtime, session, windows, views = close_fixture()
    windows.tray_changed(True)
    for view in views:
        acknowledge(view)
    # Fake destroy deliberately does not emit closed yet.
    windows._close_view(views[0])
    assert views[0].close_authorized
    assert len(windows._views) == 2
    mock_window(views[0]).destroy.assert_called_once()
    windows._close_view(views[1])
    mock_window(views[1]).destroy.assert_not_called()
    mock_window(views[1]).hide.assert_called_once()
    assert not session.closing.is_set()
    runtime.stop.assert_not_called()


def test_reload_during_preflight_invalidates_old_page_ack(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    def create(_title: str, **_kwargs: object):
        window = Mock()
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        return window

    monkeypatch.setattr("webview.create_window", create)
    runtime = Mock()
    session = DesktopSession(runtime, threading.Event())
    windows = DesktopWindows(session, Mock())
    view = windows.create()
    assert isinstance(view.window, Mock)

    def dispatch(script: str):
        if "scopecat:close-preflight" in script:
            request = view.close_request
            assert request is not None
            view.window.events.loaded.set()
            view.api.complete_close_preflight(request, "saved")

    view.window.run_js.side_effect = dispatch
    with pytest.raises(ValueError, match="confirmed"):
        view.api.request_exit()
    runtime.stop_if_idle.assert_not_called()
    assert not session.closing.is_set()
    view.window.destroy.assert_not_called()


@pytest.mark.parametrize("invalidate", ["reload", "cancel"])
def test_deferred_device_confirmation_does_not_survive_reload_or_cancel(
    monkeypatch: pytest.MonkeyPatch, invalidate: str
):
    from webview.event import Event

    def create(_title: str, **_kwargs: object):
        window = Mock()
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        return window

    monkeypatch.setattr("webview.create_window", create)
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    runtime.exit_report.return_value = ExitReport(draining=True)
    session = DesktopSession(runtime, threading.Event())
    session.connected("http://127.0.0.1:3210")
    windows = DesktopWindows(session, Mock())
    view = windows.create(device_id="device-a")
    acknowledge(view)
    assert view.api.request_exit() == {"runs": 1}
    session.wait_for_idle(True)
    if invalidate == "reload":
        mock_window(view).events.loaded.set()
    else:
        session.wait_for_idle(False)
        session.wait_for_idle(True)
    with pytest.raises(ValueError, match="设备窗口已改变"):
        session.poll_exit()
    mock_window(view).create_confirmation_dialog.assert_not_called()
    runtime.stop_if_idle.assert_called_once()
    assert not session.closing.is_set()
    assert not session._waiting.is_set()


@pytest.mark.parametrize("operation", ["request", "stop", "deferred"])
def test_shutdown_revalidates_certificate_after_runtime_stop(operation: str):
    runtime, session, _windows, views = close_fixture()
    for view in views:
        acknowledge(view)

    def invalidate():
        views[1].close_status = "unknown"
        return True

    runtime.stop.side_effect = invalidate
    runtime.stop_if_idle.side_effect = invalidate
    with pytest.raises(ValueError, match="page changed before closing"):
        if operation == "request":
            views[0].api.request_exit()
        elif operation == "stop":
            views[0].api.exit(False)
        else:
            views[0].api.wait_for_idle(True)
            session.poll_exit()
    assert not session.closing.is_set()
    for view in views:
        mock_window(view).destroy.assert_not_called()
        assert view.close_request is None


def test_reload_between_authorization_and_destroy_vetoes_close(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    def create(_title: str, **_kwargs: object):
        window = Mock()
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        return window

    monkeypatch.setattr("webview.create_window", create)
    session = DesktopSession(Mock(), threading.Event())
    windows = DesktopWindows(session, Mock())
    first, second = windows.create(), windows.create()
    assert isinstance(first.window, Mock)
    acknowledge(first)
    acknowledge(second)
    vetoed: list[bool] = []

    def destroy():
        first.window.events.loaded.set()
        vetoed.append(first.window.events.closing.set())

    first.window.destroy.side_effect = destroy
    windows._close_view(first)
    assert vetoed == [True]
    assert first in windows._views
    assert not first.close_authorized
    assert first.close_request is None
    assert not session.closing.is_set()


@pytest.mark.parametrize("phase", ["join", "destroy"])
def test_post_commit_reload_aborts_close_and_allows_existing_retry(
    monkeypatch: pytest.MonkeyPatch, phase: str
):
    from webview.event import Event

    def create(_title: str, **_kwargs: object):
        window = Mock()
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        return window

    monkeypatch.setattr("webview.create_window", create)
    runtime = Mock()
    session = DesktopSession(runtime, threading.Event())
    windows = DesktopWindows(session, Mock())
    first, second = windows.create(), windows.create()
    assert isinstance(first.window, Mock)
    assert isinstance(second.window, Mock)
    acknowledge(first)
    acknowledge(second)
    bridge = threading.Thread(target=first.api.request_exit)
    bridge.start()
    bridge.join(5)
    assert session.closing.is_set()
    if phase == "join":
        pending_file = Mock()
        pending_file.join.side_effect = first.window.events.loaded.set
        session._file_threads.add(pending_file)
    else:

        def destroy_invalidated():
            first.window.events.loaded.set()
            assert first.window.events.closing.set() is True

        first.window.destroy.side_effect = destroy_invalidated
    assert session.finish_exit(windows.destroy) is False
    assert not session.closing.is_set()
    assert windows._views == [first, second]
    assert session._exit_error is not None
    assert "page" in session._exit_error.lower()
    for view in (first, second):
        assert view.close_request is None
    restarted = Mock()
    session.restart(restarted)
    restarted.assert_called_once()
    # Existing retry/quit paths remain usable; a fresh certificate is required.
    session._file_threads.clear()

    def destroy_first():
        assert first.window.events.closing.set() is False
        first.window.events.closed.set()

    def destroy_second():
        assert second.window.events.closing.set() is False
        second.window.events.closed.set()

    first.window.destroy.side_effect = destroy_first
    second.window.destroy.side_effect = destroy_second
    retry = threading.Thread(target=first.api.request_exit)
    retry.start()
    retry.join(5)
    assert session.finish_exit(windows.destroy) is True
    assert not windows._views


@pytest.mark.parametrize("available", [False, True])
def test_last_reserved_view_retains_work_and_can_reopen(
    monkeypatch: pytest.MonkeyPatch, available: bool
):
    runtime, session, windows, views = close_fixture()
    monkeypatch.setattr("webview.active_window", lambda: views[1].window)
    windows.tray_changed(available)
    for view in views:
        acknowledge(view)
    windows._close_view(views[0])
    windows._close_view(views[1])
    mock_window(views[1]).destroy.assert_not_called()
    assert mock_window(views[1]).hide.call_count == int(available)
    assert mock_window(views[1]).create_confirmation_dialog.call_count == int(
        not available
    )
    assert not session.closing.is_set()
    runtime.stop.assert_not_called()
    runtime.stop_if_idle.assert_not_called()
    windows.show()
    mock_window(views[1]).show.assert_called()


def test_tray_loss_restores_hidden_views_without_stopping_work():
    runtime, session, windows, views = close_fixture()
    windows.tray_changed(True)
    windows.hide()
    windows.tray_changed(False)
    for view in views:
        mock_window(view).hide.assert_called_once()
        mock_window(view).show.assert_called()
    assert not session.closing.is_set()
    runtime.stop.assert_not_called()
    runtime.stop_if_idle.assert_not_called()


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_desktop_starts_service_with_optional_import_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
):
    import builtins
    import sys

    from webview.event import Event

    from scopecat_app.desktop import run

    monkeypatch.setattr(sys, "platform", platform)
    original_import = builtins.__import__
    attempted: list[str] = []

    def import_module(name: str, *args: Any, **kwargs: Any):
        if name == "pystray":
            attempted.append(name)
            raise ImportError("optional backend unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)
    window = Mock()
    window.events = SimpleNamespace(
        loaded=Event(window, True),
        closing=Event(window, True),
        closed=Event(window, True),
    )
    monkeypatch.setattr("webview.create_window", Mock(return_value=window))
    runtime = Mock()
    session = DesktopSession(runtime, threading.Event())

    def start_service():
        session.closing.set()
        return SimpleNamespace(base_url="http://localhost:1234")

    runtime.start.side_effect = start_service

    def finish_exit(_destroy: Callable[[], None]) -> bool:
        return True

    monkeypatch.setattr(session, "finish_exit", finish_exit)

    def start_native(supervise: Callable[[], None], **kwargs: object):
        assert kwargs["menu"]  # Explicit Quit/status remain native menu actions.
        window.events.loaded.set()
        supervise()

    monkeypatch.setattr("webview.start", start_native)
    run(tmp_path, prepare=Mock(), session=session)
    runtime.start.assert_called_once()
    window.load_html.assert_not_called()
    assert attempted == ["pystray"]


def test_no_tray_close_and_reopen_preserve_synthetic_background_work(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime, session, windows, views = close_fixture()
    windows._views = [views[0]]
    monkeypatch.setattr("webview.active_window", lambda: views[0].window)
    proceed, completed = threading.Event(), threading.Event()
    results: list[str] = []

    def work():
        if proceed.wait(2):
            results.append("synthetic result")
            completed.set()

    worker = threading.Thread(target=work)
    worker.start()
    try:
        windows._hide_or_retain(views[0].window)
        proceed.set()
        assert completed.wait(2)
        windows.show()
        assert results == ["synthetic result"]
        assert not session.closing.is_set()
        mock_window(views[0]).hide.assert_not_called()
        runtime.stop.assert_not_called()
        runtime.stop_if_idle.assert_not_called()
    finally:
        proceed.set()
        worker.join(2)


def test_hide_does_not_block_synchronous_native_close_callback(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    window = Mock()
    window.events = SimpleNamespace(
        loaded=Event(window, True),
        closing=Event(window, True),
        closed=Event(window, True),
    )
    monkeypatch.setattr("webview.create_window", Mock(return_value=window))
    session = DesktopSession(Mock(), threading.Event())
    windows = DesktopWindows(session, Mock())
    windows.create()
    windows.tray_changed(True)
    # Closing still runs the actual native callback, but its deferred hide is
    # irrelevant to the synchronous GUI-thread/host-lock interleaving.
    monkeypatch.setattr(windows, "_hide_or_retain", Mock())
    invoked, released, closed = threading.Event(), threading.Event(), threading.Event()

    def native_hide(_window: object):
        invoked.set()
        assert released.wait(2)

    def native_closing():
        window.events.closing.set()
        closed.set()

    monkeypatch.setattr("scopecat_app.desktop.hide_window", native_hide)
    worker = threading.Thread(target=windows.hide)
    gui = threading.Thread(target=native_closing)
    worker.start()
    try:
        assert invoked.wait(2)
        gui.start()
        assert closed.wait(1), "GUI close callback blocked on the hide owner's lock"
    finally:
        released.set()
        worker.join(2)
        if gui.ident is not None:
            gui.join(2)
    assert not worker.is_alive() and not gui.is_alive()


@pytest.mark.parametrize("regained", [False, True])
def test_tray_loss_during_native_hide_cannot_leave_late_hidden_view(
    monkeypatch: pytest.MonkeyPatch, regained: bool
):
    _, _, windows, views = close_fixture()
    windows._views = views[:1]
    windows.tray_changed(True)
    actions: list[str] = []

    def native_show(_window: object) -> None:
        actions.append("show")

    monkeypatch.setattr("scopecat_app.desktop.show_window", native_show)
    monkeypatch.setattr(windows, "tray_status", Mock())

    def native_hide(_window: object):
        windows.tray_changed(False)
        if regained:
            windows.tray_changed(True)
        actions.append("hide")  # Native Invoke finishes after the loss callback.

    monkeypatch.setattr("scopecat_app.desktop.hide_window", native_hide)
    windows.hide()
    assert actions == ["show", "hide", "show"]


def test_file_picker_returns_selected_path_without_runtime_mutation():
    runtime = Mock()
    window = Mock()
    api = DesktopAPI(DesktopSession(runtime, threading.Event()), lambda: window)
    window.create_file_dialog.return_value = None
    assert api.choose_file() is None
    window.create_file_dialog.return_value = ("C:/SDK Python/python.exe",)
    assert api.choose_file() == "C:/SDK Python/python.exe"
    assert window.create_file_dialog.call_args.kwargs == {"allow_multiple": False}
    assert runtime.mock_calls == []


def test_device_window_identity_deduplicates_loading_navigation_and_two_devices(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    def create(_title: str, **kwargs: object):
        window = Mock()
        window.get_current_url.return_value = None
        window.initial_url = kwargs["url"]
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        return window

    monkeypatch.setattr("webview.create_window", create)
    session = DesktopSession(Mock(), threading.Event())
    session.connected("http://127.0.0.1:3210")
    windows = DesktopWindows(session, Mock())
    root = windows.create()
    root.api.open_device_window("device-a")
    first = windows.latest
    assert isinstance(first.window, Mock)
    root.api.open_device_window("device-a")
    assert windows.latest is first
    assert len(windows._views) == 2
    assert first.device_id == "device-a"
    assert "device-control=device-a" in first.window.initial_url
    first.loaded.set()
    first.window.get_current_url.return_value = "http://127.0.0.1:3210/other"
    root.api.open_device_window("device-a")
    assert windows.latest is first
    root.api.open_device_window("device-b")
    assert len(windows._views) == 3
    assert windows.latest.device_id == "device-b"
    with patch.object(session, "operation", side_effect=ValueError("exit pending")):
        assert first.window.events.closing.set() is True
        first.window.hide.assert_not_called()
    first.window.events.closed.set()
    root.api.open_device_window("device-a")
    assert windows.latest is not first
    assert len(windows._views) == 3


@pytest.mark.parametrize("status", ["blocked", "unknown", "saved"])
def test_last_device_window_only_hides_after_confirmed_release(status: str):
    _runtime, _session, windows, views = close_fixture()
    view = views[0]
    windows._views = [view]
    view.device_id = "device-a"
    acknowledge(view, status)
    with patch.object(windows, "_hide_or_retain") as hide:
        windows._close_view(view)
        assert hide.call_count == (1 if status == "saved" else 0)
    mock_window(view).destroy.assert_not_called()
    assert view.close_request is None
    assert not view.close_authorized


def test_device_close_failure_retains_window_and_retry_uses_new_preflight():
    _runtime, _session, windows, views = close_fixture()
    view = views[0]
    view.device_id = "device-a"
    mock_window(view).create_confirmation_dialog.return_value = True
    acknowledge(view, "unknown")
    windows._close_view(view)
    mock_window(view).destroy.assert_not_called()
    acknowledge(view, "saved")
    windows._close_view(view)
    mock_window(view).destroy.assert_called_once()
    assert view.close_authorized


def test_device_window_api_rejects_empty_identity_before_native_open():
    session = DesktopSession(Mock(), threading.Event())
    api = DesktopAPI(session, Mock())
    api.open_device = Mock()
    for value in (None, "", " ", "x" * 513):
        with pytest.raises(ValueError, match="identity"):
            api.open_device_window(value)
    api.open_device.assert_not_called()


def test_application_reopen_restores_main_after_main_then_device_close(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    native_windows: list[Mock] = []

    def create(_title: str, **kwargs: object):
        window = Mock()
        window.initial_url = kwargs["url"]
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        window.destroy.side_effect = window.events.closed.set
        native_windows.append(window)
        return window

    monkeypatch.setattr("webview.create_window", create)
    shown = Mock()
    monkeypatch.setattr("scopecat_app.desktop.show_window", shown)
    runtime = Mock()
    session = DesktopSession(runtime, threading.Event())
    session.connected("http://127.0.0.1:3210")
    windows = DesktopWindows(session, Mock())
    main = windows.create()
    windows.new_device_window("device-a")
    device = windows.latest
    assert main.is_main and not device.is_main
    acknowledge(main)
    acknowledge(device)
    windows.tray_changed(True)

    windows._close_view(main)
    assert main not in windows._views
    windows._close_view(device)
    native_windows[1].hide.assert_called_once()
    native_windows[1].destroy.assert_not_called()
    assert device.close_request is None
    shown.reset_mock()

    windows.activate()
    reopened = windows.latest
    assert reopened.is_main and reopened.device_id is None
    assert native_windows[2].initial_url == session.page_url
    assert "device-control" not in native_windows[2].initial_url
    shown.assert_called_once_with(reopened.window)
    assert len(windows._views) == 2
    assert device in windows._views
    windows.activate()
    assert windows.latest is reopened
    assert len(windows._views) == 2
    runtime.start.assert_not_called()
    runtime.stop.assert_not_called()


def test_activation_selects_existing_main_without_resuming_device_or_run(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime, session, windows, views = close_fixture()
    views[0].is_main = True
    views[1].device_id = "device-a"
    shown = Mock()
    monkeypatch.setattr("scopecat_app.desktop.show_window", shown)
    windows.activate()
    shown.assert_called_once_with(views[0].window)
    for view in views:
        mock_window(view).run_js.assert_not_called()
    assert runtime.mock_calls == []
    session.closing.set()
    shown.reset_mock()
    windows.activate()
    shown.assert_not_called()


@pytest.mark.parametrize("result", ["saved", "blocked", "unknown"])
def test_native_review_waits_for_choice_then_continues_same_close(result: str):
    _runtime, _session, windows, views = close_fixture()
    view = views[0]
    view.device_id = "device-a"
    choosing = threading.Event()
    release = threading.Event()

    def dispatch(script: str):
        if "scopecat:close-preflight" in script:
            request = view.close_request
            assert request is not None
            view.api.complete_close_preflight(request, "pending")
            choosing.set()

            def reply():
                assert release.wait(2)
                view.api.complete_close_preflight(request, result)

            threading.Thread(target=reply).start()

    mock_window(view).run_js.side_effect = dispatch
    worker = threading.Thread(target=windows._close_view, args=(view,))
    worker.start()
    try:
        assert choosing.wait(2)
        worker.join(windows.preflight_timeout * 3)
        assert worker.is_alive(), "Human review must not expire after bridge timeout"
        mock_window(view).destroy.assert_not_called()
        mock_window(view).create_confirmation_dialog.assert_not_called()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert mock_window(view).destroy.call_count == int(result == "saved")
    assert not view.close_in_progress


def test_repeated_native_x_during_review_has_one_intent(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    def create(_title: str, **_kwargs: object):
        window = Mock()
        window.events = SimpleNamespace(
            loaded=Event(window, True),
            closing=Event(window, True),
            closed=Event(window, True),
        )
        return window

    monkeypatch.setattr("webview.create_window", create)
    session = DesktopSession(Mock(), threading.Event())
    session.connected("http://127.0.0.1:3210")
    windows = DesktopWindows(session, Mock())
    windows.create()
    view = windows.create(device_id="device-a")
    choosing = threading.Event()

    def dispatch(script: str):
        if "scopecat:close-preflight" in script:
            assert view.close_request is not None
            view.api.complete_close_preflight(view.close_request, "pending")
            choosing.set()

    mock_window(view).run_js.side_effect = dispatch
    for _ in range(3):
        mock_window(view).events.closing.set()
    assert choosing.wait(2)
    request = view.close_request
    assert request is not None
    assert (
        sum(
            "close-preflight" in item.args[0]
            for item in mock_window(view).run_js.call_args_list
        )
        == 1
    )
    reply = threading.Thread(
        target=view.api.complete_close_preflight, args=(request, "blocked")
    )
    reply.start()
    reply.join(2)
    # Join the worker through the preflight lock without blocking its bridge reply.
    with windows._preflight_lock:
        pass
    mock_window(view).destroy.assert_not_called()


@pytest.mark.parametrize("result", ["blocked", "unknown"])
def test_other_view_cancels_pending_review_without_waiting_for_first(result: str):
    _runtime, session, windows, views = close_fixture()
    for view in views:

        def dispatch(script: str, view: DesktopView = view):
            if "scopecat:close-preflight" in script:
                assert view.close_request is not None
                view.api.complete_close_preflight(
                    view.close_request, "pending" if view is views[0] else result
                )

        mock_window(view).run_js.side_effect = dispatch
    with pytest.raises(ValueError, match="confirmed"):
        session.request_exit()
    assert not windows._preflight_lock.locked()
    assert not session.closing.is_set()
    for view in views:
        assert view.close_request is None
        assert any(
            "close-release" in item.args[0]
            for item in mock_window(view).run_js.call_args_list
        )
        mock_window(view).destroy.assert_not_called()


@pytest.mark.parametrize("operation", ["view", "request", "stop", "deferred"])
def test_user_cancel_releases_entire_close_without_error(operation: str):
    runtime, session, windows, views = close_fixture()
    for view in views:
        acknowledge(view, "cancelled" if view is views[0] else "saved")
    if operation == "view":
        windows._close_view(views[0])
    elif operation == "request":
        assert views[0].api.request_exit() == "cancelled"
    elif operation == "stop":
        assert views[0].api.exit(False) == "cancelled"
    else:
        runtime.exit_report.return_value = ExitReport(draining=True)
        session.wait_for_idle(True)
        session.poll_exit()
        assert not session.exit_status()["error"]
        assert not session._waiting.is_set()
    assert not windows._preflight_lock.locked()
    assert not session.closing.is_set()
    runtime.stop.assert_not_called()
    runtime.stop_if_idle.assert_not_called()
    for view in views[:1] if operation == "view" else views:
        assert view.close_request is None
        mock_window(view).destroy.assert_not_called()
        messages = [
            item.args[0]
            for item in mock_window(view).run_js.call_args_list
            if "close-release" in item.args[0]
        ]
        assert len(messages) == 1
        assert '"message": ""' in messages[0]


@pytest.mark.parametrize("failure", ["blocked", "unknown"])
@pytest.mark.parametrize("cancel_first", [True, False])
def test_user_cancel_never_hides_another_views_failed_close(
    failure: str, cancel_first: bool
):
    runtime, session, windows, views = close_fixture()
    for index, view in enumerate(views):
        acknowledge(view, "cancelled" if (index == 0) == cancel_first else failure)
    with pytest.raises(ValueError, match="Input is not confirmed saved"):
        views[0].api.request_exit()
    assert not windows._preflight_lock.locked()
    assert not session.closing.is_set()
    runtime.stop.assert_not_called()
    runtime.stop_if_idle.assert_not_called()
    for view in views:
        assert view.close_request is None
        mock_window(view).destroy.assert_not_called()
        messages = [
            item.args[0]
            for item in mock_window(view).run_js.call_args_list
            if "close-release" in item.args[0]
        ]
        assert len(messages) == 1
        assert "Input is not confirmed saved" in messages[0]


def test_hidden_pending_window_can_be_located_and_cancelled_from_saved_view(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime, session, windows, views = close_fixture()
    views[1].device_id = "scope-a"
    shown = Mock()
    monkeypatch.setattr("scopecat_app.desktop.show_window", shown)
    pending = threading.Event()
    acknowledge(views[0])

    def dispatch(script: str):
        if "scopecat:close-preflight" in script:
            request = views[1].close_request
            assert request is not None
            views[1].api.complete_close_preflight(request, "pending")
            pending.set()

    mock_window(views[1]).run_js.side_effect = dispatch
    outcomes: list[object] = []
    worker = threading.Thread(
        target=lambda: outcomes.append(views[0].api.request_exit())
    )
    worker.start()
    try:
        assert pending.wait(2)
        worker.join(windows.preflight_timeout * 3)
        assert worker.is_alive(), "Responsive reviews do not expire by elapsed time"
        request = views[0].close_request
        assert request is not None
        review = views[0].api.close_preflight_status()
        assert review == {
            "requestId": request,
            "windows": [
                {
                    "windowId": views[1].review_id,
                    "label": "Device: scope-a",
                    "status": "pending",
                }
            ],
        }
        shown.assert_called_once_with(views[1].window)
        views[0].api.show_close_blocker(request, views[1].review_id)
        assert shown.call_count == 2
        with pytest.raises(ValueError, match="no longer active"):
            views[0].api.show_close_blocker("old", views[1].review_id)
        assert not views[0].api.cancel_close_preflight("old")
        assert views[0].api.cancel_close_preflight(request)
    finally:
        if views[0].close_request is not None:
            views[0].api.cancel_close_preflight(views[0].close_request)
        worker.join(2)
    assert not worker.is_alive()
    assert outcomes == ["cancelled"]
    assert views[0].api.close_preflight_status() is None
    assert not session.closing.is_set()
    assert not windows._preflight_lock.locked()
    runtime.stop.assert_not_called()
    runtime.stop_if_idle.assert_not_called()
    for view in views:
        assert view.close_request is None
        mock_window(view).destroy.assert_not_called()
    # A submitted operation can later succeed; it cannot revive the cancelled quit.
    views[1].api.complete_close_preflight(request, "saved")
    assert not session.closing.is_set()
    runtime.stop_if_idle.assert_not_called()


def test_close_cancellation_is_not_available_after_preflight_hands_off_shutdown():
    _runtime, _session, windows, views = close_fixture()
    for view in views:
        acknowledge(view)
    with windows.preflight():
        request = views[0].close_request
        assert request is not None
        assert views[0].api.close_preflight_status() is None
        assert not views[0].api.cancel_close_preflight(request)
        windows._validate_certificate()
    assert not views[0].api.cancel_close_preflight(request)
