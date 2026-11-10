"""Trial isolation is proven without starting a native host or a daemon."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts.app_build.trial_package import (
    TrialBuild,
    inspect,
    install_policy,
    refuse_automatic_verification,
)

import scopecat
from scopecat import trial_policy
from scopecat.application_locator import locator_home
from scopecat.trial_policy import TrialPolicy
from scopecat_app import native_bootstrap
from scopecat_app.application_runtime import ApplicationRuntime


@pytest.fixture
def marked_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, TrialPolicy, str]:
    app = tmp_path / "trial-package"
    resources = app / "resources"
    core = resources / "python/Lib/site-packages/scopecat"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("")
    shutil.copyfile(Path(trial_policy.__file__), core / "trial_policy.py")
    home = tmp_path / "isolated-trial"
    digest = install_policy(resources, TrialBuild(home, home / "locator", "qa-1491"))
    (resources / "bootstrap.py").write_text(f"main(expected_trial_digest={digest!r})\n")
    policy = trial_policy.read_policy(resources, digest)
    monkeypatch.setenv("SCOPECAT_LOCATOR_HOME", str(policy.locator))
    return app, policy, digest


def activate(monkeypatch: pytest.MonkeyPatch, policy: TrialPolicy) -> None:
    monkeypatch.setattr(scopecat, "_TRIAL_POLICY", policy, raising=False)


def test_inspection_is_read_only_and_automatic_verification_is_refused(
    marked_trial: tuple[Path, TrialPolicy, str],
):
    app, policy, digest = marked_trial
    before = {p: p.read_bytes() for p in app.rglob("*") if p.is_file()}
    result = inspect(app)
    assert result["policy_sha256"] == digest
    assert result["state"] == str(policy.state)
    assert not policy.home.exists()
    with pytest.raises(ValueError, match="read-only"):
        refuse_automatic_verification(app)
    assert before == {p: p.read_bytes() for p in app.rglob("*") if p.is_file()}


@pytest.mark.parametrize("damage", ["missing", "modified", "mismatch"])
def test_marked_runtime_cannot_fall_back_when_policy_is_damaged(
    marked_trial: tuple[Path, TrialPolicy, str], damage: str
):
    _app, policy, _digest = marked_trial
    target = policy.resources / "trial-policy.json"
    if damage == "missing":
        target.unlink()
    elif damage == "modified":
        target.write_text("{}")
    else:
        bootstrap = policy.resources / "bootstrap.py"
        bootstrap.write_text(f"main(expected_trial_digest={'0' * 64!r})\n")
        with pytest.raises(ValueError, match="markers"):
            inspect(policy.resources.parent)
        return
    result = import_trial(policy)
    assert result.returncode != 0
    assert not policy.home.exists()


def import_trial(
    policy: TrialPolicy, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    site = policy.resources / "python/Lib/site-packages"
    return subprocess.run(  # noqa: S603 - import-only isolated temporary test package
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            (
                f"import sys; sys.path.insert(0, {str(site)!r}); "
                "import scopecat; print(scopecat._TRIAL_POLICY.home)"
            ),
        ],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("locator", [None, "wrong"])
def test_import_fails_before_any_state_write_if_environment_is_lost(
    marked_trial: tuple[Path, TrialPolicy, str], locator: str | None
):
    _app, policy, _digest = marked_trial
    env = dict(os.environ)
    env.pop("SCOPECAT_LOCATOR_HOME", None)
    if locator:
        env["SCOPECAT_LOCATOR_HOME"] = str(policy.home.parent / locator)
    assert import_trial(policy, env).returncode != 0
    assert not policy.home.exists()
    assert import_trial(policy).returncode == 0
    assert not policy.home.exists()


@pytest.mark.parametrize("target", ["other", "root", "relative"])
def test_application_and_locator_guard_before_reading_or_creating_state(
    marked_trial: tuple[Path, TrialPolicy, str],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
):
    _app, policy, _digest = marked_trial
    activate(monkeypatch, policy)
    home = (
        policy.home.parent / "other"
        if target == "other"
        else policy.home
        if target == "root"
        else Path("relative")
    )
    with pytest.raises(ValueError, match=r"Trial|trial"):
        ApplicationRuntime(home)
    assert not policy.home.exists()
    assert ApplicationRuntime(policy.state).home == policy.state
    assert locator_home() == policy.locator
    assert not policy.home.exists()
    monkeypatch.delenv("SCOPECAT_LOCATOR_HOME")
    with pytest.raises(ValueError, match="LOCATOR"):
        locator_home()


@pytest.mark.parametrize(
    "name",
    [
        "data",
        "locator",
        "cache",
        "experiments",
        "data/native-start.log",
        "data/native-start.lock",
        "data/state",
        "trial-window-probe.json",
    ],
)
def test_policy_rejects_symlink_escape(
    marked_trial: tuple[Path, TrialPolicy, str], tmp_path: Path, name: str
):
    _app, policy, digest = marked_trial
    policy.home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (policy.home / name).parent.mkdir(parents=True, exist_ok=True)
        (policy.home / name).symlink_to(
            outside / "missing-target", target_is_directory=True
        )
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this test host")
    with pytest.raises(ValueError, match="symlink"):
        trial_policy.read_policy(policy.resources, digest)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--home", "WRONG"],
        ["--home", "HOME", "--home", "HOME"],
        ["--home=HOME", "--home=HOME"],
        ["--home", "HOME", "--payload", "OTHER", "--payload", "OTHER"],
    ],
)
def test_native_arguments_fail_before_state_or_logging(
    marked_trial: tuple[Path, TrialPolicy, str],
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
):
    _app, policy, digest = marked_trial
    activate(monkeypatch, policy)
    argv = [value.replace("HOME", str(policy.home)) for value in arguments]
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", *argv])
    launch = Mock()
    monkeypatch.setattr(native_bootstrap, "launch", launch)
    with pytest.raises(ValueError, match=r"Trial|trial"):
        native_bootstrap.main(expected_trial_digest=digest)
    launch.assert_not_called()
    assert not policy.home.exists()


def test_trial_native_requires_both_markers_and_exact_payload(
    marked_trial: tuple[Path, TrialPolicy, str], monkeypatch: pytest.MonkeyPatch
):
    _app, policy, digest = marked_trial
    activate(monkeypatch, policy)
    argv = [
        "bootstrap.py",
        "--home",
        str(policy.home),
        "--payload",
        str(policy.resources / "payload"),
        "--entry",
        str(policy.resources.parent / "Scopecat.exe"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(native_bootstrap.sys, "platform", "win32")
    launch = Mock()
    monkeypatch.setattr(native_bootstrap, "launch", launch)
    with pytest.raises(ValueError, match="do not match"):
        native_bootstrap.main()
    assert not policy.home.exists()
    argv[4] = str(policy.resources.parent / "wrong-payload")
    with pytest.raises(ValueError, match="payload"):
        native_bootstrap.main(expected_trial_digest=digest)
    assert not policy.home.exists()
    argv[4] = str(policy.resources / "payload")
    native_bootstrap.main(expected_trial_digest=digest)
    launch.assert_called_once()
    assert launch.call_args.args[1].state == policy.state


def test_normal_install_has_no_trial_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(scopecat, "_TRIAL_POLICY", None, raising=False)
    assert trial_policy.current_policy() is None
    trial_policy.validate_arguments([])
    assert ApplicationRuntime(tmp_path / "normal-data").home == tmp_path / "normal-data"


def test_required_hook_rejects_symlinked_resource_chain_before_resolving(
    marked_trial: tuple[Path, TrialPolicy, str], tmp_path: Path
):
    _app, policy, _digest = marked_trial
    actual = tmp_path / "moved-resources"
    policy.resources.rename(actual)
    try:
        policy.resources.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this test host")
    result = import_trial(policy)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not policy.home.exists()


def test_cached_policy_rechecks_dangling_log_before_runtime_entry(
    marked_trial: tuple[Path, TrialPolicy, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _app, policy, _digest = marked_trial
    activate(monkeypatch, policy)
    policy.state.mkdir(parents=True)
    outside = tmp_path / "outside-log"
    try:
        (policy.state / "native-start.log").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this test host")
    with pytest.raises(ValueError, match="symlink"):
        ApplicationRuntime(policy.state)
    assert not outside.exists()


def test_unreadable_trial_tree_is_not_silently_accepted(
    marked_trial: tuple[Path, TrialPolicy, str], monkeypatch: pytest.MonkeyPatch
):
    _app, policy, digest = marked_trial
    policy.home.mkdir()

    def denied_walk(
        root: Path, *, follow_symlinks: bool, on_error: Callable[[OSError], None]
    ) -> Iterator[tuple[Path, list[str], list[str]]]:
        on_error(PermissionError("trial directory cannot be inspected"))
        yield root, [], []

    monkeypatch.setattr(Path, "walk", denied_walk)
    with pytest.raises(PermissionError, match="cannot be inspected"):
        trial_policy.read_policy(policy.resources, digest)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_native_trial_probe_dispatches_only_fixed_module_after_validation(
    marked_trial: tuple[Path, TrialPolicy, str],
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
):
    import types

    from scopecat_app import desktop_platform

    _app, policy, digest = marked_trial
    activate(monkeypatch, policy)
    monkeypatch.setattr(sys, "platform", platform)
    entry = (
        policy.resources.parents[1]
        if platform == "darwin"
        else policy.resources.parent / "Scopecat.exe"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap.py",
            "--home",
            str(policy.home),
            "--payload",
            str(policy.resources / "payload"),
            "--entry",
            str(entry),
            "--trial-window-probe",
        ],
    )
    monkeypatch.setattr(
        desktop_platform,
        "macos_bundle_identifier",
        lambda: f"org.scopecat.trial.{policy.identity}",
    )
    probe = Mock()
    module = types.ModuleType("scopecat_app.trial_window_probe")
    monkeypatch.setattr(module, "run", probe, raising=False)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    launch = Mock()
    monkeypatch.setattr(native_bootstrap, "launch", launch)
    native_bootstrap.main(expected_trial_digest=digest)
    probe.assert_called_once_with(policy.home)
    launch.assert_not_called()
    assert not policy.home.exists(), (
        "Dispatch must not prepare or create logs before the probe guard"
    )


@pytest.mark.parametrize(
    "identity", [None, "org.scopecat.desktop", "org.python.python"]
)
def test_native_trial_probe_rejects_wrong_cocoa_identity_before_state_access(
    marked_trial: tuple[Path, TrialPolicy, str],
    monkeypatch: pytest.MonkeyPatch,
    identity: str | None,
):
    from scopecat_app import desktop_platform

    _app, policy, digest = marked_trial
    activate(monkeypatch, policy)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap.py",
            "--home",
            str(policy.home),
            "--payload",
            str(policy.resources / "payload"),
            "--entry",
            str(policy.resources.parents[1]),
            "--trial-window-probe",
        ],
    )
    monkeypatch.setattr(desktop_platform, "macos_bundle_identifier", lambda: identity)
    with pytest.raises(ValueError, match="bundle identity"):
        native_bootstrap.main(expected_trial_digest=digest)
    assert not policy.home.exists()


def test_normal_package_rejects_probe_switch_before_creating_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(scopecat, "_TRIAL_POLICY", None, raising=False)
    home = tmp_path / "normal-home"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap.py",
            "--home",
            str(home),
            "--payload",
            str(tmp_path / "payload"),
            "--trial-window-probe",
        ],
    )
    with pytest.raises(ValueError, match="marked trial"):
        native_bootstrap.main()
    assert not home.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--trial-window-probe"],
        ["--check-result", "report.json"],
        ["arbitrary-script.py"],
    ],
)
def test_probe_rejects_duplicate_or_conflicting_modes_and_script_arguments(
    marked_trial: tuple[Path, TrialPolicy, str],
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
):
    _app, policy, digest = marked_trial
    activate(monkeypatch, policy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap.py",
            "--home",
            str(policy.home),
            "--payload",
            str(policy.resources / "payload"),
            "--trial-window-probe",
            *extra,
        ],
    )
    with pytest.raises((ValueError, SystemExit)):
        native_bootstrap.main(expected_trial_digest=digest)
    assert not policy.home.exists()
