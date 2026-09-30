"""Native close keeps failure recovery in the same window, without a manager."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools.desktop import DesktopAPI


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


def test_failed_requalification_does_not_publish_or_reopen():
    runtime = Mock()
    runtime.qualify.side_effect = ValueError("missing dependency")
    window = Mock()
    api = DesktopAPI(runtime, lambda: window, threading.Event())
    with pytest.raises(ValueError, match="missing dependency"):
        api.requalify()
    runtime.stop.assert_called_once()
    runtime.select.assert_not_called()
    runtime.start.assert_not_called()
    window.load_url.assert_not_called()


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
