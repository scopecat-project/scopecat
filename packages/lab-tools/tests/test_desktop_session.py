"""Native close keeps failure recovery in the same window, without a manager."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools.desktop import DesktopAPI


@pytest.mark.parametrize("background", [True, False])
def test_close_obeys_explicit_background_choice(background):
    runtime = Mock()
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(runtime, lambda: window, closing)
    api.exit(background)
    assert runtime.stop.call_count == (0 if background else 1)
    assert closing.is_set()
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
    api.exit(True)
    assert closing.is_set()


def test_close_during_candidate_preparation_does_not_abandon_installer():
    runtime = Mock()
    window = Mock()
    closing = threading.Event()
    api = DesktopAPI(runtime, lambda: window, closing)

    def prepare(_path):
        with pytest.raises(ValueError, match="等待操作完成"):
            api.exit(True)
        window.destroy.assert_not_called()
        return SimpleNamespace(model_dump=lambda **_: {})

    runtime.prepare_update.side_effect = prepare
    api.prepare_update("/delivery")
    api.exit(True)
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
