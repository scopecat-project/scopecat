"""Native presentation transitions must not depend on a visible normal window."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

from lab_tools.desktop_platform import start_tray


def test_cocoa_tray_is_created_and_shown_on_the_main_loop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
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
