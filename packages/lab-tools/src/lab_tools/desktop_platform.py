# PyObjC exposes Cocoa symbols dynamically, without Python type declarations.
# pyright: reportAttributeAccessIssue=false, reportMissingImports=false
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
"""Platform activation glue; service and quit decisions stay in the desktop host."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable


def install_reopen_handler(show: Callable[[], None]) -> None:
    if sys.platform != "darwin":
        return

    import objc
    from AppKit import NSApplication

    # Add the Cocoa reopen delegate method as an Objective-C category. Preserve
    # pywebview's existing Quit/window delegates. Unlike activation notification,
    # reopen also fires when the app is already active but its window is hidden.
    def reopen(_self: object, _app: object, _visible: bool) -> bool:
        threading.Thread(target=show, daemon=True).start()
        return True

    objc.classAddMethods(
        type(NSApplication.sharedApplication().delegate()),
        [
            objc.selector(
                reopen,
                selector=b"applicationShouldHandleReopen:hasVisibleWindows:",
                signature=b"B@:@B",
            )
        ],
    )
