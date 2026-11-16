"""Retained results own windows without navigating or releasing controllers."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scopecat_app.desktop import DesktopAPI, DesktopView, DesktopWindows
from scopecat_app.desktop_session import DesktopSession

type ResultWindows = tuple[Mock, DesktopSession, DesktopWindows, DesktopView, Mock]


@pytest.fixture
def result_windows(monkeypatch: pytest.MonkeyPatch) -> ResultWindows:
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
    show = Mock()
    monkeypatch.setattr("scopecat_app.desktop.show_window", show)
    runtime = Mock()
    session = DesktopSession(runtime, threading.Event())
    session.connected("http://127.0.0.1:3210")
    windows = DesktopWindows(session, Mock())
    controller = windows.create(device_id="device-a")
    return runtime, session, windows, controller, show


def test_result_identity_reuse_parallel_results_and_reopen(
    result_windows: ResultWindows,
):
    runtime, _session, windows, controller, show = result_windows
    first_id, second_id = "a" * 64, "b" * 64
    controller.api.open_saved_measurement_window(first_id)
    first = windows.latest
    assert first.saved_measurement_id == first_id
    assert not first.is_main
    assert first.device_id is None
    assert isinstance(first.window, Mock)
    assert first.window.initial_url == (
        "http://127.0.0.1:3210/?saved-measurement=" + first_id + "&desktop=1"
    )
    controller.api.open_saved_measurement_window(first_id)
    assert len(windows._views) == 2
    first.loaded.set()
    first.window.get_current_url.return_value = "http://127.0.0.1:3210/other"
    controller.api.open_saved_measurement_window(first_id)
    show.assert_called_with(first.window)
    assert len(windows._views) == 2
    controller.api.open_saved_measurement_window(second_id)
    second = windows.latest
    assert second.saved_measurement_id == second_id
    assert len(windows._views) == 3
    controller.api.open_saved_measurement_window(first_id)
    show.assert_called_with(first.window)
    assert isinstance(controller.window, Mock)
    assert controller.window.initial_url == (
        "http://127.0.0.1:3210/?device-control=device-a&desktop=1"
    )
    controller.window.run_js.assert_not_called()
    controller.window.load_url.assert_not_called()
    controller.window.destroy.assert_not_called()
    assert controller.device_id == "device-a"
    assert not controller.close_authorized
    assert controller.close_request is None
    assert runtime.mock_calls == []
    first.window.events.closed.set()
    controller.api.open_saved_measurement_window(first_id)
    assert windows.latest is not first
    assert len(windows._views) == 3


@pytest.mark.parametrize("closing_state", ["authorized", "finished"])
def test_result_does_not_reuse_closing_window(
    result_windows: ResultWindows, closing_state: str
):
    _runtime, _session, windows, controller, show = result_windows
    controller.api.open_saved_measurement_window("a" * 64)
    first = windows.latest
    if closing_state == "authorized":
        first.close_authorized = True
    else:
        first.close_finished.set()
    controller.api.open_saved_measurement_window("a" * 64)
    assert windows.latest is not first
    show.assert_called_with(windows.latest.window)


@pytest.mark.parametrize("existing", [False, True])
def test_result_open_obeys_close_preflight(
    result_windows: ResultWindows, existing: bool
):
    _runtime, session, windows, controller, show = result_windows
    if existing:
        controller.api.open_saved_measurement_window("a" * 64)
    count = len(windows._views)
    show.reset_mock()
    with windows._preflight_lock, pytest.raises(ValueError, match="input saving"):
        controller.api.open_saved_measurement_window("a" * 64)
    assert len(windows._views) == count
    show.assert_not_called()
    session.closing.set()
    with pytest.raises(ValueError, match="正在关闭"):
        controller.api.open_saved_measurement_window("a" * 64)
    assert len(windows._views) == count
    show.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        None,
        42,
        {},
        "",
        " ",
        "a" * 63,
        "a" * 65,
        "G" * 64,
        "A" * 64,
        "http://other/?saved-measurement=" + "a" * 64,
        "../" + "a" * 64,
    ],
)
def test_result_api_validates_identity_before_native_open(value: object):
    api = DesktopAPI(DesktopSession(Mock(), threading.Event()), Mock())
    open_result = Mock()
    api.open_saved_measurement = open_result
    with pytest.raises(ValueError, match="Invalid saved measurement identity"):
        api.open_saved_measurement_window(value)
    open_result.assert_not_called()


def test_result_open_requires_connected_application(result_windows: ResultWindows):
    _runtime, session, windows, controller, show = result_windows
    session.base_url = None
    with pytest.raises(ValueError, match="not connected"):
        controller.api.open_saved_measurement_window("a" * 64)
    assert len(windows._views) == 1
    show.assert_not_called()


def test_result_identity_survives_reconnect(result_windows: ResultWindows):
    _runtime, session, windows, controller, show = result_windows
    record_id = "a" * 64
    controller.api.open_saved_measurement_window(record_id)
    result = windows.latest
    assert isinstance(result.window, Mock)
    result.window.get_current_url.return_value = result.window.initial_url
    session.connected("http://127.0.0.1:4321")
    result.window.run_js.assert_called_once_with(
        'window.location.replace("http://127.0.0.1:4321/?saved-measurement='
        + record_id
        + '&desktop=1");'
    )
    controller.api.open_saved_measurement_window(record_id)
    assert len(windows._views) == 2
    assert windows.latest is result
    show.assert_called_with(result.window)


@pytest.mark.parametrize("current", [None, "/other", "/?saved-measurement=" + "b" * 64])
def test_result_reconnect_restores_fixed_identity(
    result_windows: ResultWindows, current: str | None
):
    _runtime, session, windows, controller, _show = result_windows
    record_id = "a" * 64
    controller.api.open_saved_measurement_window(record_id)
    result = windows.latest
    assert isinstance(result.window, Mock)
    result.window.get_current_url.return_value = (
        None if current is None else "http://127.0.0.1:3210" + current
    )
    session.connected("http://127.0.0.1:4321")
    expected = "http://127.0.0.1:4321/?saved-measurement=" + record_id + "&desktop=1"
    assert result.initial_location == expected
    if current is None:
        result.window.load_url.assert_called_once_with(expected)
        result.window.run_js.assert_not_called()
    else:
        result.window.run_js.assert_called_once_with(
            f'window.location.replace("{expected}");'
        )
    controller.api.open_saved_measurement_window(record_id)
    assert windows.latest is result
    assert len(windows._views) == 2


def test_result_reopen_restores_identity_after_navigation(
    result_windows: ResultWindows,
):
    _runtime, _session, windows, controller, show = result_windows
    record_id = "a" * 64
    controller.api.open_saved_measurement_window(record_id)
    result = windows.latest
    assert isinstance(result.window, Mock)
    result.window.get_current_url.return_value = (
        "http://127.0.0.1:3210/?saved-measurement=" + "b" * 64
    )
    controller.api.open_saved_measurement_window(record_id)
    result.window.run_js.assert_called_once_with(
        'window.location.replace("http://127.0.0.1:3210/?saved-measurement='
        + record_id
        + '&desktop=1");'
    )
    show.assert_called_with(result.window)
    assert windows.latest is result
    assert len(windows._views) == 2
    assert isinstance(controller.window, Mock)
    controller.window.run_js.assert_not_called()
