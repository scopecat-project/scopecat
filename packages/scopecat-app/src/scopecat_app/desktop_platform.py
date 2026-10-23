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
from typing import TYPE_CHECKING, cast

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


def macos_bundle_identifier() -> str | None:
    """Cocoa's identity must remain the outer app, not the bundled Python bin."""
    from Foundation import NSBundle

    return cast("str | None", NSBundle.mainBundle().bundleIdentifier())


def install_window_menu() -> None:
    """Let Cocoa maintain the list and activation of native application windows."""
    if sys.platform != "darwin":
        return

    import webview
    from AppKit import NSApplication, NSMenu, NSMenuItem
    from PyObjCTools import AppHelper

    def install() -> None:
        app = NSApplication.sharedApplication()
        menu = NSMenu.alloc().initWithTitle_("窗口")
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("窗口", None, "")
        item.setSubmenu_(menu)
        app.mainMenu().addItem_(item)
        # Status-item and other auxiliary windows are not document views.
        views = [view.native for view in webview.windows]
        for window in app.windows():
            if window not in views:
                window.setExcludedFromWindowsMenu_(True)
        app.setWindowsMenu_(menu)
        for window in views:
            app.addWindowsItem_title_filename_(window, window.title(), False)

    AppHelper.callAfter(install)


def _tray_is_available(tray: Icon) -> bool:
    """Check the native entry, not pystray's requested visibility flag."""
    if sys.platform == "darwin":
        button = tray._status_item.button()
        return bool(button.window() is not None and not button.isHidden())
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Identifier(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("guidItem", ctypes.c_byte * 16),
            ]

        # Pinned pystray 0.19.5 passes hID (not the structure's uID) to
        # NOTIFYICONDATAW, leaving its actual uID at zero. HWND disambiguates
        # icons. Keep this paired with the pinned backend when upgrading it.
        identifier = Identifier(ctypes.sizeof(Identifier), tray._hwnd, 0)
        rect = wintypes.RECT()
        query = ctypes.windll.shell32.Shell_NotifyIconGetRect
        query.argtypes = [ctypes.POINTER(Identifier), ctypes.POINTER(wintypes.RECT)]
        query.restype = ctypes.c_long
        return bool(
            query(ctypes.byref(identifier), ctypes.byref(rect)) == 0
            and rect.right > rect.left
            and rect.bottom > rect.top
        )
    return False


def _stop_tray(tray: Icon | None) -> None:
    if tray is not None:
        try:
            try:
                tray.visible = False
            finally:
                tray.stop()
        except Exception:
            logging.getLogger(__name__).warning(
                "Optional tray cleanup failed", exc_info=True
            )


def start_tray(
    create: Callable[[], Icon],
    changed: Callable[[bool], None] = lambda _available: None,
) -> Callable[[], None]:
    """An optional tray must never prevent the window/service from starting.

    Linux pystray backends cannot reliably acknowledge a usable host. Keep the
    window there rather than importing a backend that may open X11 at import time.
    Cocoa work, including failures and cleanup, stays on its native event loop.
    """
    logger = logging.getLogger(__name__)
    stopped = threading.Event()
    tray: Icon | None = None

    def report() -> None:
        try:
            available = tray is not None and tray.visible and _tray_is_available(tray)
        except Exception:
            available = False
            logger.info("Optional tray availability could not be confirmed")
        if not stopped.is_set():
            changed(available)

    def setup(icon: Icon) -> None:
        if stopped.is_set():
            return
        try:
            icon.visible = True
            report()
        except Exception:
            changed(False)
            logger.warning("Optional tray initialization failed", exc_info=True)
            _stop_tray(tray)

    def no_setup(_icon: Icon) -> None:
        pass

    def ready() -> None:
        nonlocal tray
        if stopped.is_set():
            return
        try:
            tray = create()  # Includes optional imports and image loading.
            if sys.platform == "darwin":
                tray.run_detached(setup=no_setup)
                tray.visible = True
                tray._icon_image.setTemplate_(True)
                report()
            else:
                tray.run_detached(setup=setup)
        except Exception:
            changed(False)
            logger.warning("Optional tray initialization failed", exc_info=True)
            _stop_tray(tray)

    if sys.platform not in {"darwin", "win32"}:
        changed(False)
        logger.info("No tray: backend cannot confirm a usable system entry")
        return stopped.set

    def schedule(action: Callable[[], None]) -> None:
        action()

    try:
        if sys.platform == "darwin":
            from PyObjCTools import AppHelper

            schedule = AppHelper.callAfter
        schedule(ready)
    except Exception:
        changed(False)
        logger.warning("Optional tray scheduling failed", exc_info=True)
        return stopped.set

    def monitor() -> None:
        while not stopped.wait(1):
            schedule(report)

    threading.Thread(target=monitor, daemon=True).start()

    def stop() -> None:
        stopped.set()
        schedule(lambda: _stop_tray(tray))

    return stop


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
