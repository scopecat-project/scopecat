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


def _indicator_has_host(tray: Icon) -> bool:
    from gi.repository import Gio, GLib

    indicator = tray._appindicator
    if not indicator.get_property("connected") or not indicator.get_status():
        return False
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = bus.call_sync(
        "org.kde.StatusNotifierWatcher",
        "/StatusNotifierWatcher",
        "org.freedesktop.DBus.Properties",
        "GetAll",
        GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
        GLib.VariantType.new("(a{sv})"),
        Gio.DBusCallFlags.NO_AUTO_START,
        1000,
        None,
    )
    properties = result.unpack()[0]
    owner = bus.get_unique_name()
    return bool(
        owner
        and properties.get("IsStatusNotifierHostRegistered") is True
        and any(
            item.startswith(owner + "/")
            for item in properties.get("RegisteredStatusNotifierItems", [])
        )
    )


def _linux_tray_is_available(tray: Icon) -> bool:
    backend = type(tray).__module__
    if backend == "pystray._gtk":
        return bool(tray._status_icon.is_embedded() and tray._status_icon.get_visible())
    if backend == "pystray._appindicator":
        return _indicator_has_host(tray)
    if backend == "pystray._xorg":
        # A dock request/selection owner alone is not acknowledgment. Require
        # the host to have reparented and mapped the actual icon window.
        return bool(
            tray._systray_manager is not None
            and tray._window.query_tree().parent.id != tray._display.screen().root.id
            and tray._window.get_attributes().map_state == 2  # X.IsViewable
        )
    return False


def _gtk_schedule(action: Callable[[], None]) -> None:
    from gi.repository import GLib

    def callback() -> bool:
        action()
        return False

    _ = GLib.idle_add(callback)


def _tray_is_available(tray: Icon) -> bool:
    """Check the native entry, not pystray's requested visibility flag."""
    if sys.platform.startswith("linux"):
        return _linux_tray_is_available(tray)
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


def _monitor_tray(
    stopped: threading.Event,
    schedule: Callable[[Callable[[], None]], None],
    report: Callable[[], None],
    changed: Callable[[bool], None],
) -> None:
    while not stopped.wait(1):
        try:
            schedule(report)
        except Exception:
            changed(False)
            logging.getLogger(__name__).warning(
                "Optional tray monitoring failed", exc_info=True
            )
            return


def start_tray(
    create: Callable[[], Icon],
    changed: Callable[[bool], None] = lambda _available: None,
) -> Callable[[], None]:
    """An optional tray must never prevent the window/service from starting.

    Backend imports are isolated here, including Linux backends that connect
    to X11 during import. Native evidence, not construction, enables hiding.
    Cocoa and GTK availability checks stay on their respective event loops.
    """
    logger = logging.getLogger(__name__)
    stopped = threading.Event()
    tray: Icon | None = None

    def report() -> None:
        if stopped.is_set():
            return
        try:
            available = tray is not None and tray.visible and _tray_is_available(tray)
        except Exception:
            available = False
            logger.info("Optional tray availability could not be confirmed")
        if not stopped.is_set():
            changed(available)

    def setup(icon: Icon) -> None:
        if stopped.is_set():
            _stop_tray(icon)
            return
        try:
            icon.visible = True
            schedule(report)
        except Exception:
            changed(False)
            logger.warning("Optional tray initialization failed", exc_info=True)
            _stop_tray(tray)

    def no_setup(_icon: Icon) -> None:
        pass

    def ready() -> None:
        nonlocal tray, schedule
        if stopped.is_set():
            return
        try:
            tray = create()  # Includes optional imports and image loading.
            if type(tray).__module__ in {"pystray._gtk", "pystray._appindicator"}:
                schedule = _gtk_schedule
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

    threading.Thread(
        target=_monitor_tray, args=(stopped, schedule, report, changed), daemon=True
    ).start()

    def stop() -> None:
        stopped.set()
        try:
            # webview.start returns on the main thread after the native loop
            # ends; another queued Cocoa callback would never be serviced.
            if threading.current_thread() is threading.main_thread():
                _stop_tray(tray)
            else:
                schedule(lambda: _stop_tray(tray))
        except Exception:
            logger.warning("Optional tray cleanup scheduling failed", exc_info=True)

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
