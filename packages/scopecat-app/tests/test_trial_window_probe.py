"""Fail closed before importing or constructing any runtime/window."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scopecat import trial_policy
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
