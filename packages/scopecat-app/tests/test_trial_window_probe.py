"""Fail closed before importing or constructing any runtime/window."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from scopecat import trial_policy
from scopecat_app.desktop import DesktopView, DesktopWindows
from scopecat_app.trial_window_probe import (
    guard,
    is_reopened_controller,
    parse_home,
    run,
)


def _identity(path: Path) -> Path:
    return path


def test_unmarked_probe_refuses_before_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(trial_policy, "current_policy", lambda: None)
    with pytest.raises(ValueError, match="marked trial"):
        run(Path("/should-not-be-read"))


def test_wrong_home_fails_before_state_access(monkeypatch: pytest.MonkeyPatch):
    policy = SimpleNamespace(home=Path("/exact-trial"))
    monkeypatch.setattr(trial_policy, "current_policy", lambda: policy)
    validate = Mock()
    monkeypatch.setattr(trial_policy, "validate_environment", validate)
    monkeypatch.setattr(trial_policy, "checked_path", _identity)
    with pytest.raises(ValueError, match="exactly match"):
        run(Path("/other-trial"))
    validate.assert_called_once_with(policy)


def test_invalid_environment_prevents_path_check(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(trial_policy, "current_policy", lambda: object())
    validate = Mock(side_effect=ValueError("locator mismatch"))
    path = Mock()
    monkeypatch.setattr(trial_policy, "validate_environment", validate)
    monkeypatch.setattr(trial_policy, "checked_path", path)
    with pytest.raises(ValueError, match="locator mismatch"):
        guard(Path("/exact-trial"))
    path.assert_not_called()


def test_exact_policy_is_returned(monkeypatch: pytest.MonkeyPatch):
    policy = SimpleNamespace(home=Path("/exact-trial"))
    monkeypatch.setattr(trial_policy, "current_policy", lambda: policy)
    monkeypatch.setattr(trial_policy, "validate_environment", Mock())
    monkeypatch.setattr(trial_policy, "checked_path", _identity)
    assert guard(policy.home) is policy


def test_duplicate_home_is_rejected():
    with pytest.raises(SystemExit):
        parse_home(["--home", "/one", "--home", "/one"])


def test_missing_home_is_rejected():
    with pytest.raises(SystemExit):
        parse_home([])


def test_reopen_wait_does_not_accept_main_old_or_wrong_device():
    main = Mock(device_id=None)
    previous = Mock(device_id="virtual-vna")
    wrong = Mock(device_id="other-device")
    replacement = Mock(device_id="virtual-vna")
    assert not is_reopened_controller(main, main, previous)
    assert not is_reopened_controller(previous, main, previous)
    assert not is_reopened_controller(wrong, main, previous)
    assert is_reopened_controller(replacement, main, previous)


def test_reopen_requires_device_identity():
    main = Mock(device_id=None)
    previous = Mock(device_id=None)
    assert not is_reopened_controller(Mock(device_id=None), main, previous)


def test_cocoa_probe_rejects_bundleless_or_wrong_host():
    from scopecat_app.trial_window_probe import (
        COCOA_SHA256,
        COCOA_VERSION,
        check_cocoa_identity,
    )

    for identity in (None, "org.scopecat.desktop", "org.scopecat.trial.other"):
        with pytest.raises(ValueError, match="original unique Cocoa host"):
            check_cocoa_identity(
                identity, "org.scopecat.trial.qa", COCOA_VERSION, COCOA_SHA256
            )


def test_cocoa_probe_requires_exact_patch():
    from scopecat_app.trial_window_probe import (
        COCOA_SHA256,
        COCOA_VERSION,
        check_cocoa_identity,
    )

    for version, digest in (("6.2.1", COCOA_SHA256), (COCOA_VERSION, "wrong")):
        with pytest.raises(ValueError, match="private-store Cocoa patch"):
            check_cocoa_identity(
                "org.scopecat.trial.qa", "org.scopecat.trial.qa", version, digest
            )
    check_cocoa_identity(
        "org.scopecat.trial.qa", "org.scopecat.trial.qa", COCOA_VERSION, COCOA_SHA256
    )


def test_platform_failure_precedes_runtime_construction(
    monkeypatch: pytest.MonkeyPatch,
):
    from scopecat_app import application_runtime, trial_window_probe

    policy = Mock()
    runtime = Mock()
    monkeypatch.setattr(trial_window_probe, "guard", Mock(return_value=policy))
    monkeypatch.setattr(
        trial_window_probe,
        "platform_preconditions",
        Mock(side_effect=ValueError("wrong Cocoa host")),
    )
    monkeypatch.setattr(application_runtime, "ApplicationRuntime", runtime)
    with pytest.raises(ValueError, match="wrong Cocoa host"):
        run(Path("/no-state-access"))
    runtime.assert_not_called()


def test_navigation_waits_for_ready_then_clicks_and_confirms():
    from scopecat_app.trial_window_probe import navigate_workspace

    evaluate = Mock(side_effect=[False, True, None, False, True])
    navigate_workspace(evaluate, "设置 / Settings")
    calls = [call.args[0] for call in evaluate.call_args_list]
    assert "nav button[aria-current=page]" in calls[0]
    assert calls[0] == calls[1]
    assert '"click"' in calls[2]
    assert "getAttribute('aria-current')" in calls[3]
    assert calls[3] == calls[4]
    assert all("location.hash=" not in script for script in calls)


def test_diagnostics_refuse_non_owned_origin():
    from scopecat_app.trial_window_probe import page_diagnostics

    view = Mock()
    view.window.get_current_url.return_value = "https://example.com/private"
    result = page_diagnostics(view, "http://127.0.0.1:1234")
    assert result == {"error": "Diagnostic page is not the owned daemon origin"}
    view.window.evaluate_js.assert_not_called()


def test_diagnostics_capture_structure_not_input_values():
    from scopecat_app.trial_window_probe import page_diagnostics

    view = Mock()
    view.window.get_current_url.return_value = "http://127.0.0.1:1234/#settings"
    view.window.evaluate_js.return_value = {"readyState": "complete"}
    assert page_diagnostics(view, "http://127.0.0.1:1234") == {"readyState": "complete"}
    script = view.window.evaluate_js.call_args.args[0]
    assert "visibleText" in script and "labels:" in script and "htmlFor" in script
    assert ".value" not in script and "Storage" not in script


@pytest.mark.parametrize(
    "close_request,busy,locked",
    [
        ("pending", False, False),
        (None, True, False),
        (None, False, True),
        (None, False, False),
    ],
)
def test_close_cancel_waits_for_full_native_completion(
    close_request: str | None, busy: bool, locked: bool
):
    from scopecat_app.trial_window_probe import close_preflight_idle

    view = SimpleNamespace(close_request=close_request, close_in_progress=busy)
    windows = SimpleNamespace(_preflight_lock=SimpleNamespace(locked=lambda: locked))
    assert close_preflight_idle(
        cast("DesktopWindows", cast("object", windows)),
        cast("DesktopView", cast("object", view)),
    ) == (close_request is None and not busy and not locked)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_native_close_uses_owned_platform_user_path(
    monkeypatch: pytest.MonkeyPatch, platform: str
):
    import scopecat_app.trial_window_probe as probe

    window = SimpleNamespace(uid="own", destroy=Mock())
    native_window = SimpleNamespace(performClose_=Mock())
    native = SimpleNamespace(pywebview_window=window, window=native_window)
    window.native = native_window if platform == "darwin" else native
    scheduled = Mock()
    backend = SimpleNamespace(
        BrowserView=SimpleNamespace(instances={"own": native}),
        AppHelper=SimpleNamespace(callAfter=scheduled),
    )
    monkeypatch.setattr(probe.sys, "platform", platform)

    def imported(_name: str) -> SimpleNamespace:
        return backend

    monkeypatch.setattr(probe, "import_module", imported)
    probe.schedule_native_close(
        cast("DesktopView", cast("object", SimpleNamespace(window=window)))
    )
    if platform == "darwin":
        scheduled.assert_called_once_with(native_window.performClose_, None)
        window.destroy.assert_not_called()
        native_window.performClose_.assert_not_called()
    else:
        window.destroy.assert_called_once_with()
        scheduled.assert_not_called()
    native.pywebview_window = object()
    with pytest.raises(ValueError, match="not this probe"):
        probe.schedule_native_close(
            cast("DesktopView", cast("object", SimpleNamespace(window=window)))
        )


def test_request_close_requires_real_event_and_removes_observer(
    monkeypatch: pytest.MonkeyPatch,
):
    from webview.event import Event

    import scopecat_app.trial_window_probe as probe

    window = SimpleNamespace(
        events=SimpleNamespace(closing=Event(None, should_lock=True))
    )
    view = cast("DesktopView", cast("object", SimpleNamespace(window=window)))

    def emit_closing(target: DesktopView) -> None:
        target.window.events.closing.set()

    monkeypatch.setattr(probe, "schedule_native_close", emit_closing)
    probe.request_native_close(view)
    assert len(window.events.closing) == 0

    def no_event(_view: DesktopView) -> None:
        pass

    monkeypatch.setattr(probe, "schedule_native_close", no_event)

    def assert_ready(predicate: Callable[[], object], message: str) -> None:
        assert predicate(), message

    monkeypatch.setattr(probe, "wait", assert_ready)
    with pytest.raises(AssertionError, match="event was not observed"):
        probe.request_native_close(view)
    assert len(window.events.closing) == 0


def test_winforms_backend_requires_reviewed_package():
    from scopecat_app.trial_window_probe import (
        WINFORMS_SHA256,
        WINFORMS_VERSION,
        check_winforms_identity,
    )

    check_winforms_identity(WINFORMS_VERSION, WINFORMS_SHA256)
    with pytest.raises(ValueError, match="reviewed WinForms"):
        check_winforms_identity("6.2.1", WINFORMS_SHA256)
    with pytest.raises(ValueError, match="reviewed WinForms"):
        check_winforms_identity(WINFORMS_VERSION, "modified")
