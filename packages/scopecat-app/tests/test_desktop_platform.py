"""Native presentation transitions must not depend on a visible normal window."""

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from scopecat_app.desktop_platform import (
    install_reopen_handler,
    install_window_menu,
    log_native_backend,
    start_tray,
)


def test_windows_backend_diagnostic_reads_the_loaded_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    import hashlib
    import logging

    source = tmp_path / "winforms.py"
    source.write_bytes(b"verified source")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "webview.platforms.winforms",
        SimpleNamespace(__file__=str(source)),
    )

    def installed_version(_name: str) -> str:
        return "verified-version"

    monkeypatch.setattr("scopecat_app.desktop_platform.version", installed_version)
    with caplog.at_level(logging.INFO):
        log_native_backend()
    assert "pywebview=verified-version" in caplog.text
    assert f"backend={source}" in caplog.text
    assert hashlib.sha256(source.read_bytes()).hexdigest() in caplog.text


def test_windows_backend_diagnostic_reports_missing_evidence(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "webview.platforms.winforms", raising=False)
    log_native_backend()
    assert "Native WebView identity could not be verified" in caplog.text
    assert "Native WebView: pywebview=" not in caplog.text


def _backend_tray(backend: str) -> Mock:
    tray = Mock()
    # Mock creates a fresh class per instance; tag that class for backend dispatch.
    type(tray).__module__ = backend
    return tray


def test_window_menu_excludes_auxiliary_windows_on_the_main_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    document, auxiliary, app = Mock(), Mock(), Mock()
    app.windows.return_value = [document, auxiliary]
    callbacks: list[Callable[[], object]] = []
    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(windows=[SimpleNamespace(native=document)]),
    )
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSApplication=SimpleNamespace(sharedApplication=lambda: app),
            NSMenu=Mock(),
            NSMenuItem=Mock(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "PyObjCTools",
        SimpleNamespace(AppHelper=SimpleNamespace(callAfter=callbacks.append)),
    )
    install_window_menu()
    app.setWindowsMenu_.assert_not_called()
    callbacks.pop()()
    auxiliary.setExcludedFromWindowsMenu_.assert_called_once_with(True)
    document.setExcludedFromWindowsMenu_.assert_not_called()
    app.addWindowsItem_title_filename_.assert_called_once_with(
        document, document.title(), False
    )


def test_cocoa_tray_is_created_and_shown_on_the_main_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        "scopecat_app.desktop_platform.macos_bundle_identifier",
        lambda: "org.scopecat.desktop",
    )
    callbacks: list[Callable[[], object]] = []
    monkeypatch.setitem(
        sys.modules,
        "PyObjCTools",
        SimpleNamespace(AppHelper=SimpleNamespace(callAfter=callbacks.append)),
    )
    tray = Mock(visible=False)
    create = Mock(return_value=tray)
    stop = start_tray(create)
    create.assert_not_called()
    callbacks.pop()()
    assert tray.visible
    setup = tray.run_detached.call_args.kwargs["setup"]
    tray.visible = False
    setup(tray)
    assert not tray.visible  # The pystray setup worker must not touch Cocoa.
    stop()
    tray.stop.assert_called_once()


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa delegate ABI")
def test_quit_hook_matches_real_pywebview_delegate_signature():
    from webview.platforms import cocoa

    # PyObjC populates AppKit dynamically; pywebview exposes the actual module.
    native_cocoa = cast("Any", cocoa)
    app = native_cocoa.AppKit.NSApplication.sharedApplication()
    previous = app.delegate()
    delegate = native_cocoa.BrowserView.AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    quit_requested = threading.Event()
    closing = threading.Event()
    try:
        signature = delegate.applicationShouldTerminate_.signature
        install_reopen_handler(lambda: None, quit_requested.set, closing.is_set)
        assert delegate.applicationShouldTerminate_.signature == signature
        assert delegate.applicationShouldTerminate_(app) == 0
        assert quit_requested.wait(2)
        closing.set()
        assert delegate.applicationShouldTerminate_(app) == 1
    finally:
        app.setDelegate_(previous)


@pytest.mark.parametrize("failure", [ImportError, RuntimeError])
@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_optional_tray_failure_retains_fallback(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception], platform: str
):
    monkeypatch.setattr(sys, "platform", platform)
    callbacks: list[Callable[[], object]] = []
    monkeypatch.setitem(
        sys.modules,
        "PyObjCTools",
        SimpleNamespace(AppHelper=SimpleNamespace(callAfter=callbacks.append)),
    )
    changed = Mock()
    stop = start_tray(Mock(side_effect=failure("unavailable")), changed)
    if callbacks:
        callbacks.pop()()
    changed.assert_called_with(False)
    stop()
    if callbacks:
        callbacks.pop()()


@pytest.mark.parametrize("available", [True, False])
def test_windows_tray_needs_native_confirmation(
    monkeypatch: pytest.MonkeyPatch, available: bool
):
    monkeypatch.setattr(sys, "platform", "win32")

    def is_available(_tray: object) -> bool:
        return available

    monkeypatch.setattr(
        "scopecat_app.desktop_platform._tray_is_available", is_available
    )
    tray, changed = Mock(), Mock()
    stop = start_tray(lambda: tray, changed)
    changed.assert_not_called()  # Constructor and run_detached are not proof.
    tray.run_detached.call_args.kwargs["setup"](tray)
    changed.assert_called_once_with(available)
    stop()
    tray.stop.assert_called_once()


def test_async_tray_show_failure_is_contained(monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import PropertyMock

    monkeypatch.setattr(sys, "platform", "win32")
    tray, changed = Mock(), Mock()
    type(tray).visible = PropertyMock(side_effect=RuntimeError("show failed"))
    stop = start_tray(lambda: tray, changed)
    tray.run_detached.call_args.kwargs["setup"](tray)
    changed.assert_called_once_with(False)
    stop()


@pytest.mark.parametrize("result, width", [(0, 16), (0, 0), (-1, 16)])
def test_windows_native_probe_uses_pinned_backend_identity(
    monkeypatch: pytest.MonkeyPatch, result: int, width: int
):
    import ctypes

    from scopecat_app.desktop_platform import _tray_is_available

    monkeypatch.setattr(sys, "platform", "win32")

    def query(identifier: Any, rect: Any) -> int:
        # ctypes.byref exposes native structures through a runtime-only _obj.
        assert identifier._obj.hWnd == 123
        assert identifier._obj.uID == 0
        rect._obj.right = width
        rect._obj.bottom = 16
        return result

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            shell32=SimpleNamespace(Shell_NotifyIconGetRect=Mock(side_effect=query))
        ),
        raising=False,
    )
    assert _tray_is_available(Mock(_hwnd=123)) == (result == 0 and width > 0)


@pytest.mark.parametrize(
    "hidden, window", [(False, object()), (True, object()), (False, None)]
)
def test_cocoa_probe_requires_visible_native_button(
    monkeypatch: pytest.MonkeyPatch, hidden: bool, window: object | None
):
    from scopecat_app.desktop_platform import _tray_is_available

    monkeypatch.setattr(sys, "platform", "darwin")
    tray = Mock()
    button = tray._status_item.button.return_value
    button.isHidden.return_value = hidden
    button.window.return_value = window
    assert _tray_is_available(tray) == (not hidden and window is not None)


def test_late_windows_setup_after_shutdown_cleans_up(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    tray, changed = Mock(), Mock()
    stop = start_tray(lambda: tray, changed)
    setup = tray.run_detached.call_args.kwargs["setup"]
    stop()
    tray.reset_mock()
    setup(tray)
    tray.stop.assert_called_once()
    changed.assert_not_called()


@pytest.mark.parametrize("embedded", [True, False])
def test_linux_gtk_keeps_working_tray_and_requires_embedding(
    monkeypatch: pytest.MonkeyPatch, embedded: bool
):
    from scopecat_app.desktop_platform import _tray_is_available

    monkeypatch.setattr(sys, "platform", "linux")
    tray = _backend_tray("pystray._gtk")
    tray._status_icon = Mock()
    tray._status_icon.is_embedded.return_value = embedded
    tray._status_icon.get_visible.return_value = True
    assert _tray_is_available(tray) is embedded


@pytest.mark.parametrize("docked, mapped", [(True, True), (False, True), (True, False)])
def test_linux_xorg_requires_completed_docking(
    monkeypatch: pytest.MonkeyPatch, docked: bool, mapped: bool
):
    from scopecat_app.desktop_platform import _tray_is_available

    monkeypatch.setattr(sys, "platform", "linux")
    tray = _backend_tray("pystray._xorg")
    tray._systray_manager = object()
    tray._display = Mock()
    tray._display.screen.return_value.root.id = 1
    tray._window = Mock()
    tray._window.query_tree.return_value.parent.id = 2 if docked else 1
    tray._window.get_attributes.return_value.map_state = 2 if mapped else 0
    assert _tray_is_available(tray) == (docked and mapped)


@pytest.mark.parametrize(
    "host, registered", [(True, True), (False, True), (True, False)]
)
def test_indicator_requires_host_and_own_registration(
    monkeypatch: pytest.MonkeyPatch, host: bool, registered: bool
):
    from scopecat_app.desktop_platform import _indicator_has_host

    bus = Mock()
    bus.get_unique_name.return_value = ":1.42"
    bus.call_sync.return_value.unpack.return_value = (
        {
            "IsStatusNotifierHostRegistered": host,
            "RegisteredStatusNotifierItems": [
                (":1.42" if registered else ":1.99") + "/StatusNotifierItem"
            ],
        },
    )
    gio, glib = Mock(), Mock()
    gio.bus_get_sync.return_value = bus
    monkeypatch.setitem(
        sys.modules, "gi.repository", SimpleNamespace(Gio=gio, GLib=glib)
    )
    tray = Mock()
    tray._appindicator.get_property.return_value = True
    tray._appindicator.get_status.return_value = 1
    assert _indicator_has_host(tray) == (host and registered)


def test_linux_gtk_reports_only_from_its_event_loop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "linux")
    callbacks: list[Callable[[], object]] = []
    glib = SimpleNamespace(idle_add=callbacks.append)
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(GLib=glib))
    tray = _backend_tray("pystray._gtk")
    tray.visible = False
    tray.run_detached, tray.stop = Mock(), Mock()
    tray._status_icon = Mock()
    tray._status_icon.is_embedded.return_value = True
    tray._status_icon.get_visible.return_value = True
    changed = Mock()
    stop = start_tray(lambda: tray, changed)
    tray.run_detached.call_args.kwargs["setup"](tray)
    changed.assert_not_called()
    callbacks.pop()()
    changed.assert_called_once_with(True)
    stop()


@pytest.mark.parametrize("minimized", [False, True])
def test_cocoa_show_restores_only_minimized_live_windows(
    monkeypatch: pytest.MonkeyPatch, minimized: bool
):
    from scopecat_app.desktop_platform import show_window

    monkeypatch.setattr(sys, "platform", "darwin")
    queue: list[Callable[[], None]] = []
    native, app = Mock(), Mock()
    native.isMiniaturized.return_value = minimized
    shown, closed = threading.Event(), threading.Event()
    shown.set()
    window = Mock(native=native, events=SimpleNamespace(shown=shown, closed=closed))
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(NSApplication=SimpleNamespace(sharedApplication=lambda: app)),
    )
    monkeypatch.setitem(
        sys.modules,
        "PyObjCTools",
        SimpleNamespace(AppHelper=SimpleNamespace(callAfter=queue.append)),
    )
    show_window(window)
    # An unconditional Cocoa restore can finish its animation after close and
    # reorder the NSWindow after its WebView was removed.
    window.restore.assert_not_called()
    native.makeKeyAndOrderFront_.assert_not_called()
    queue.pop()()
    assert native.deminiaturize_.call_count == int(minimized)
    native.makeKeyAndOrderFront_.assert_called_once()
    app.activateIgnoringOtherApps_.assert_called_once_with(True)


def test_cocoa_queued_show_cannot_resurrect_a_closed_window(
    monkeypatch: pytest.MonkeyPatch,
):
    from scopecat_app.desktop_platform import show_window

    monkeypatch.setattr(sys, "platform", "darwin")
    queue: list[Callable[[], None]] = []
    native, app = Mock(), Mock()
    shown, closed = threading.Event(), threading.Event()
    shown.set()
    window = Mock(native=native, events=SimpleNamespace(shown=shown, closed=closed))
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(NSApplication=SimpleNamespace(sharedApplication=lambda: app)),
    )
    monkeypatch.setitem(
        sys.modules,
        "PyObjCTools",
        SimpleNamespace(AppHelper=SimpleNamespace(callAfter=queue.append)),
    )
    show_window(window)
    closed.set()
    assert queue
    queue.pop()()
    native.assert_not_called()
    assert not native.mock_calls
    assert not app.mock_calls


@pytest.mark.parametrize("closed_before_ready", [False, True])
def test_cocoa_first_show_does_not_block_native_creation(
    monkeypatch: pytest.MonkeyPatch, closed_before_ready: bool
):
    from scopecat_app.desktop_platform import show_window

    monkeypatch.setattr(sys, "platform", "darwin")
    shown, closed, queued, waiting = (threading.Event() for _ in range(4))
    callbacks: list[Callable[[], None]] = []
    threads: list[threading.Thread] = []
    real_thread = threading.Thread

    def start_worker(
        *, target: Callable[..., None], args: tuple[object, ...], daemon: bool
    ):
        thread = real_thread(target=target, args=args, daemon=daemon)
        threads.append(thread)
        return thread

    def wait_ready(timeout: float):
        waiting.set()
        return shown.wait(min(timeout, 2))

    def schedule(callback: Callable[[], None]):
        callbacks.append(callback)
        queued.set()

    monkeypatch.setattr("scopecat_app.desktop_platform.threading.Thread", start_worker)
    monkeypatch.setattr("scopecat_app.desktop_platform._cocoa_schedule", schedule)
    ready = SimpleNamespace(is_set=shown.is_set, wait=wait_ready)
    window = Mock(events=SimpleNamespace(shown=ready, closed=closed))
    try:
        show_window(window)
        assert waiting.wait(1)
        assert not callbacks  # The main loop is free to create the native window.
        if closed_before_ready:
            closed.set()
        shown.set()
        threads[0].join(2)
        assert not threads[0].is_alive()
        assert len(callbacks) == (0 if closed_before_ready else 1)
    finally:
        shown.set()
        for thread in threads:
            thread.join(2)
