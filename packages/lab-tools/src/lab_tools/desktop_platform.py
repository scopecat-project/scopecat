# PyObjC exposes Cocoa symbols dynamically, without Python type declarations.
# pyright: reportAttributeAccessIssue=false, reportMissingImports=false
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
"""Platform activation glue; service and quit decisions stay in the desktop host."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import webview
    from pystray._base import Icon


def show_window(window: webview.Window) -> None:
    # Show before restoring: WinForms can retain minimized placement while hidden.
    window.show()
    window.restore()
    window.show()


def hide_window(window: webview.Window) -> None:
    # WinForms needs normalized placement; Cocoa deminiaturize schedules a
    # front-order animation which can complete after orderOut and reopen it.
    if sys.platform != "darwin":
        window.restore()
    window.hide()


def start_tray(create: Callable[[], Icon]) -> Callable[[], None]:
    """Keep Cocoa status item creation and visibility on the running main loop."""
    if sys.platform != "darwin":
        tray = create()
        tray.run_detached()

        def stop() -> None:
            tray.visible = False
            tray.stop()

        return stop

    from PyObjCTools import AppHelper

    mac_tray: Icon | None = None
    stopped = False

    def setup(_icon: Icon) -> None:
        pass

    def ready() -> None:
        nonlocal mac_tray
        if stopped:
            return
        mac_tray = create()
        # Default setup changes Cocoa visibility from a worker thread.
        mac_tray.run_detached(setup=setup)
        mac_tray.visible = True
        # pystray has no public template-image option. Its Cocoa image is created
        # by the first show; let macOS supply the menu-bar appearance and tint.
        mac_tray._icon_image.setTemplate_(True)
        logging.getLogger(__name__).info(
            "Menu bar ready: image=%s status_item_visible=%s",
            mac_tray._icon_image.size(),
            mac_tray._status_item.isVisible(),
        )

    AppHelper.callAfter(ready)

    def stop_mac() -> None:
        nonlocal stopped
        stopped = True
        if mac_tray is not None:
            mac_tray.visible = False
            mac_tray.stop()

    return stop_mac


def install_reopen_handler(
    show: Callable[[], None], quit_app: Callable[[], None], closing: Callable[[], bool]
) -> None:
    if sys.platform != "darwin":
        return

    import objc
    from AppKit import NSApplication

    # Add Cocoa application delegates as an Objective-C category.
    # reopen also fires when the app is already active but its window is hidden.
    def reopen(_self: object, _app: object, _visible: bool) -> bool:
        threading.Thread(target=show, daemon=True).start()
        return True

    def terminate(_self: object, _app: object) -> int:
        if closing():
            return 1  # NSTerminateNow
        threading.Thread(target=quit_app, daemon=True).start()
        return 0  # NSTerminateCancel until the backend has stopped.

    objc.classAddMethods(
        type(NSApplication.sharedApplication().delegate()),
        [
            objc.selector(
                reopen,
                selector=b"applicationShouldHandleReopen:hasVisibleWindows:",
                signature=b"B@:@B",
            ),
            objc.selector(
                terminate,
                selector=b"applicationShouldTerminate:",
                # Preserve the existing bridge ABI instead of guessing the
                # NSInteger/enum width used by pywebview's delegate.
                signature=NSApplication.sharedApplication()
                .delegate()
                .applicationShouldTerminate_.signature,
            ),
        ],
    )
