"""Native close keeps failure recovery in the same window, without a manager."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools.desktop import DesktopAPI, DesktopWindows, _window_close_handlers
from lab_tools.desktop_session import DesktopSession


@pytest.mark.parametrize("stop", [False, True])
def test_file_work_allows_other_windows_and_participates_in_quit(stop):
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


def test_native_windows_share_backend_and_keep_navigation(monkeypatch):
    from webview.event import Event

    created = []

    def create(_title, **kwargs):
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
    first.api.retry()
    first.window.run_js.assert_called_once_with(
        'window.location.replace("http://localhost:1234");'
    )
    first.window.run_js.reset_mock()
    first.window.get_current_url.return_value = "http://localhost:1234/?run=A#runs"
    first.api.open_run_window("B &?#/结果")
    second = windows.latest
    assert second is not first
    assert (
        created[1][1]["url"]
        == "http://localhost:1234/?run=B+%26%3F%23%2F%E7%BB%93%E6%9E%9C"
    )
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

    # Tray hide/open is application-wide: neither retained view may be stranded.
    shown = Mock()
    monkeypatch.setattr("lab_tools.desktop.show_window", shown)
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

    # pywebview returns True from Event.set when a handler vetoes the close.
    assert first.window.events.closing.set() is False
    assert second.window.events.closing.set() is True
    first.window.events.closed.set()
    assert windows.latest is second
    shown.reset_mock()
    windows.show()
    shown.assert_called_once_with(second.window)
    runtime.stop.assert_not_called()
    session.closing.set()
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
    session.finish_exit(lambda: (first.destroy(), second.destroy()))
    first.destroy.assert_called_once()
    second.destroy.assert_called_once()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_window_close_hides_without_requesting_quit(platform, monkeypatch):
    monkeypatch.setattr("lab_tools.desktop.sys.platform", platform)
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


def test_new_source_preserves_existing_files(tmp_path):
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
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "lab_tools.author_environment.create_client_environment", lambda *_: None
    )
    monkeypatch.setattr(
        "lab_tools.author_environment.prepare_execution_environment",
        lambda *_: tmp_path / "execution/python",
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
def test_new_source_name_cannot_escape_selected_parent(tmp_path, name):
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
    tmp_path, monkeypatch
):
    from scopecat_server.scaffold import write_author_scaffold

    source = tmp_path / "experiments"
    write_author_scaffold(source)
    nested = source / "unrelated"
    nested.mkdir()
    (nested / "pyproject.toml").write_text('[project]\nname = "unrelated"\n')
    prepare = Mock()
    monkeypatch.setattr(
        "lab_tools.author_environment.prepare_execution_environment", prepare
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


def test_waiting_exit_does_not_replace_an_in_progress_preparation():
    runtime = Mock()
    closing = threading.Event()
    api = DesktopAPI(DesktopSession(runtime, closing), Mock())
    api.wait_for_idle(True)
    with api._session.operation():
        api._session.poll_exit()
    runtime.stop_if_idle.assert_not_called()
    assert not closing.is_set()
    runtime.stop_if_idle.return_value = True
    api._session.poll_exit()
    assert closing.is_set()


def finish_exit(api, background):
    bridge = threading.Thread(target=api.exit, args=(background,))
    bridge.start()
    bridge.join()
    if not background:
        api._session.finish_exit(api._window().destroy)


@pytest.mark.parametrize("background", [True, False])
def test_close_obeys_explicit_background_choice(background):
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
    runtime.stop.assert_called_once()
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
def test_run_window_rejects_invalid_bridge_input(run_id):
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
