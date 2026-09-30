"""Native close keeps failure recovery in the same window, without a manager."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools.desktop import DesktopAPI


def test_new_source_preserves_existing_files(tmp_path):
    source = tmp_path / "experiments"
    source.mkdir()
    notes = source / "notes.txt"
    notes.write_text("keep")
    runtime = Mock()
    api = DesktopAPI(runtime, Mock(), threading.Event())
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
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    window = Mock()
    api = DesktopAPI(runtime, lambda: window, threading.Event())
    with pytest.raises(ValueError, match="先完成或停止"):
        api.create_source(str(tmp_path), "experiments")
    assert (tmp_path / "experiments/notebooks/02_edit_scan.py").is_file()
    runtime.stop.assert_not_called()
    runtime.register_source.assert_not_called()
    window.load_url.assert_not_called()


@pytest.mark.parametrize("name", ["../outside", "..", "C:outside", "nested\\folder"])
def test_new_source_name_cannot_escape_selected_parent(tmp_path, name):
    api = DesktopAPI(Mock(), Mock(), threading.Event())
    with pytest.raises(ValueError, match="单个新目录"):
        api.create_source(str(tmp_path), name)
    assert list(tmp_path.iterdir()) == []


def test_folder_picker_cancellation_does_not_change_runtime():
    runtime = Mock()
    window = Mock()
    window.create_file_dialog.return_value = None
    api = DesktopAPI(runtime, lambda: window, threading.Event())
    assert api.choose_directory() is None
    assert runtime.mock_calls == []


def test_busy_close_keeps_window_and_service_until_explicit_choice():
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    runtime.activity.return_value.model_dump.return_value = {"runs": 1}
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(runtime, lambda: window, closing)
    assert api.request_exit() == {"runs": 1}
    runtime.stop.assert_not_called()
    assert not closing.is_set()
    window.destroy.assert_not_called()


def test_waiting_exit_can_be_cancelled_without_stopping_work():
    runtime = Mock()
    runtime.stop_if_idle.return_value = False
    api = DesktopAPI(runtime, Mock(), threading.Event())
    api.wait_for_idle(True)
    api._poll_exit()
    runtime.stop_if_idle.assert_called_once()
    api.wait_for_idle(False)
    api._poll_exit()
    runtime.stop_if_idle.assert_called_once()


def test_waiting_exit_does_not_replace_an_in_progress_preparation():
    runtime = Mock()
    closing = threading.Event()
    api = DesktopAPI(runtime, Mock(), closing)
    api.wait_for_idle(True)
    with api._operation():
        api._poll_exit()
    runtime.stop_if_idle.assert_not_called()
    assert not closing.is_set()
    runtime.stop_if_idle.return_value = True
    api._poll_exit()
    assert closing.is_set()


def finish_exit(api, background):
    bridge = threading.Thread(target=api.exit, args=(background,))
    bridge.start()
    bridge.join()
    if not background:
        api._finish_exit()


@pytest.mark.parametrize("background", [True, False])
def test_close_obeys_explicit_background_choice(background):
    runtime = Mock()
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(runtime, lambda: window, closing)
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
    api = DesktopAPI(runtime, lambda: window, closing)
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
        with pytest.raises(ValueError, match="等待操作完成"):
            api.exit(True)
        window.destroy.assert_not_called()

    api = DesktopAPI(runtime, lambda: window, closing, prepare)
    api.retry()
    finish_exit(api, True)
    window.hide.assert_called_once()
    window.destroy.assert_not_called()


def test_window_survives_until_bridge_has_delivered_exit_reply():
    runtime = Mock()
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(runtime, lambda: window, closing)
    reply = threading.Event()

    def bridge_call():
        api.exit(False)
        # The bridge still needs the live page after the exposed API returns.
        window.destroy.assert_not_called()
        assert reply.wait(5)

    bridge = threading.Thread(target=bridge_call)
    bridge.start()
    assert closing.wait(5)
    supervisor = threading.Thread(target=api._finish_exit)
    supervisor.start()
    window.destroy.assert_not_called()
    reply.set()
    supervisor.join(5)
    bridge.join(5)
    assert not supervisor.is_alive()
    window.destroy.assert_called_once()


def test_failed_restart_preparation_keeps_window_available():
    runtime = Mock()
    window = Mock()
    prepare = Mock(side_effect=ValueError("missing dependency"))
    closing = threading.Event()
    api = DesktopAPI(runtime, lambda: window, closing, prepare)
    with pytest.raises(ValueError, match="missing dependency"):
        api.restart()
    runtime.stop.assert_called_once()
    prepare.assert_called_once()
    runtime.start.assert_not_called()
    window.load_url.assert_not_called()
    window.destroy.assert_not_called()
    assert not closing.is_set()


def test_interrupted_selection_can_retry_without_closing_window():
    runtime = Mock()
    runtime.start.return_value = SimpleNamespace(base_url="http://localhost:1234")
    window = Mock()
    prepare = Mock(side_effect=[OSError("interrupted selection"), None])
    api = DesktopAPI(runtime, lambda: window, threading.Event(), prepare)
    with pytest.raises(OSError, match="interrupted selection"):
        api.retry()
    runtime.start.assert_not_called()
    window.destroy.assert_not_called()
    api.retry()
    assert prepare.call_count == 2
    window.load_url.assert_called_once_with("http://localhost:1234")
