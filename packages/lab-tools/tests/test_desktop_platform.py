"""Native presentation transitions must not depend on a visible normal window."""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools.desktop_platform import install_reopen_handler, start_tray


def test_cocoa_tray_is_created_and_shown_on_the_main_loop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        "lab_tools.desktop_platform.macos_bundle_identifier",
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
    tray.stop.assert_called_once()


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa delegate ABI")
def test_quit_hook_matches_real_pywebview_delegate_signature():
    cocoa = pytest.importorskip("webview.platforms.cocoa")
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
