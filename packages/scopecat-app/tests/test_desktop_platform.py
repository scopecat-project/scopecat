"""Native presentation transitions must not depend on a visible normal window."""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scopecat_app.desktop_platform import (
    install_reopen_handler,
    install_window_menu,
    start_tray,
)


def test_window_menu_excludes_auxiliary_windows_on_the_main_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    document, auxiliary, app = Mock(), Mock(), Mock()
    app.windows.return_value = [document, auxiliary]
    callbacks = []
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
    callbacks = []
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
    callbacks.pop()()
    tray.stop.assert_called_once()


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa delegate ABI")
def test_quit_hook_matches_real_pywebview_delegate_signature():
    from webview.platforms import cocoa

    app = cocoa.AppKit.NSApplication.sharedApplication()
    previous = app.delegate()
    delegate = cocoa.BrowserView.AppDelegate.alloc().init()
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


@pytest.mark.parametrize("platform", ["linux", "freebsd"])
def test_unverified_platform_never_imports_tray(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    create, changed = Mock(), Mock()
    stop = start_tray(create, changed)
    changed.assert_called_once_with(False)
    create.assert_not_called()
    stop()


@pytest.mark.parametrize("failure", [ImportError, RuntimeError])
@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_optional_tray_failure_retains_fallback(monkeypatch, failure, platform):
    monkeypatch.setattr(sys, "platform", platform)
    callbacks = []
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
def test_windows_tray_needs_native_confirmation(monkeypatch, available):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "scopecat_app.desktop_platform._tray_is_available", lambda _tray: available
    )
    tray, changed = Mock(), Mock()
    stop = start_tray(lambda: tray, changed)
    changed.assert_not_called()  # Constructor and run_detached are not proof.
    tray.run_detached.call_args.kwargs["setup"](tray)
    changed.assert_called_once_with(available)
    stop()
    tray.stop.assert_called_once()


def test_async_tray_show_failure_is_contained(monkeypatch):
    from unittest.mock import PropertyMock

    monkeypatch.setattr(sys, "platform", "win32")
    tray, changed = Mock(), Mock()
    type(tray).visible = PropertyMock(side_effect=RuntimeError("show failed"))
    stop = start_tray(lambda: tray, changed)
    tray.run_detached.call_args.kwargs["setup"](tray)
    changed.assert_called_once_with(False)
    stop()


@pytest.mark.parametrize("result, width", [(0, 16), (0, 0), (-1, 16)])
def test_windows_native_probe_uses_pinned_backend_identity(monkeypatch, result, width):
    import ctypes

    from scopecat_app.desktop_platform import _tray_is_available

    monkeypatch.setattr(sys, "platform", "win32")

    def query(identifier, rect):
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
def test_cocoa_probe_requires_visible_native_button(monkeypatch, hidden, window):
    from scopecat_app.desktop_platform import _tray_is_available

    monkeypatch.setattr(sys, "platform", "darwin")
    tray = Mock()
    button = tray._status_item.button.return_value
    button.isHidden.return_value = hidden
    button.window.return_value = window
    assert _tray_is_available(tray) == (not hidden and window is not None)
