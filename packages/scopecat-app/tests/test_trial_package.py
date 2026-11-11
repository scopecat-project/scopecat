"""Trial isolation is proven without starting a native host or a daemon."""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

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


@pytest.fixture
def trial_development(
    marked_trial: tuple[Path, TrialPolicy, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[TrialPolicy, Path]:
    from scripts import dev
    from scripts.app_build import dev_host_windows, dev_resources

    _app, policy, _digest = marked_trial
    source = policy.home.parent / "source"
    scripts = source / "scripts/app_build"
    scripts.mkdir(parents=True)
    for relative in (
        "dev.py",
        "app_build/dev_resources.py",
        "app_build/dev_host_windows.py",
    ):
        (source / "scripts" / relative).write_text("# Synthetic source fixture\n")
    (source / ".gitignore").write_text("node_modules/\n")
    for command in (
        ["init", "--quiet"],
        ["add", "."],
        [
            "-c",
            "user.name=Trial QA",
            "-c",
            "user.email=qa@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Synthetic source",
        ],
    ):
        subprocess.run(["git", *command], cwd=source, check=True, capture_output=True)  # noqa: S603, S607 - temporary fixture repository
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 - temporary fixture
        cwd=source,
        text=True,
    ).strip()
    payload = policy.resources / "payload"
    payload.mkdir()
    (payload / "bundle.json").write_text(
        json.dumps(
            {
                "format": 1,
                "sources": {"public": commit},
                "target": {},
                "runtime": {},
                "files": {},
            }
        )
    )
    activate(monkeypatch, policy)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(policy.resources / "python/python.exe"))
    monkeypatch.setenv(dev_resources.TRIAL_DEVELOPMENT, policy.identity)
    monkeypatch.setattr(dev, "__file__", str(source / "scripts/dev.py"))
    monkeypatch.setattr(dev_resources, "__file__", str(scripts / "dev_resources.py"))
    monkeypatch.setattr(
        dev_host_windows, "__file__", str(scripts / "dev_host_windows.py")
    )
    return policy, source


def test_trial_development_paths_are_fixed_without_creating_default_data(
    trial_development: tuple[TrialPolicy, Path], monkeypatch: pytest.MonkeyPatch
):
    from scripts.app_build import dev_resources

    policy, source = trial_development
    default = Mock(side_effect=AssertionError("Default path must not be consulted"))
    monkeypatch.setattr(dev_resources, "user_data_path", default)
    assert (
        dev_resources.trial_development_policy(source=source, home=policy.state)
        is policy
    )
    assert dev_resources.development_home(source).is_relative_to(
        policy.home / "development"
    )
    default.assert_not_called()
    assert not policy.home.exists()


@pytest.mark.parametrize(
    "damage",
    [
        "locator_missing",
        "locator_wrong",
        "mode_missing",
        "mode_wrong",
        "interpreter",
        "source",
        "dirty",
        "mac",
    ],
)
def test_trial_development_refuses_before_compiler_or_owner(
    trial_development: tuple[TrialPolicy, Path],
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
):
    from scripts import dev
    from scripts.app_build import dev_host_windows, dev_resources

    policy, source = trial_development
    if damage == "locator_missing":
        monkeypatch.delenv("SCOPECAT_LOCATOR_HOME")
    elif damage == "locator_wrong":
        monkeypatch.setenv("SCOPECAT_LOCATOR_HOME", str(source / "wrong"))
    elif damage == "mode_missing":
        monkeypatch.delenv(dev_resources.TRIAL_DEVELOPMENT)
    elif damage == "mode_wrong":
        monkeypatch.setenv(dev_resources.TRIAL_DEVELOPMENT, "wrong")
    elif damage == "interpreter":
        monkeypatch.setattr(sys, "executable", str(source / "python.exe"))
    elif damage == "source":
        source = source.parent / "other-source"
    elif damage == "dirty":
        (source / "scripts/dev.py").write_text("# Changed\n")
    else:
        monkeypatch.setattr(sys, "platform", "darwin")
    compiler = Mock()
    runtime = Mock()
    monkeypatch.setattr(dev_host_windows, "compiler_environment", compiler)
    monkeypatch.setattr(dev, "ApplicationRuntime", runtime)
    with pytest.raises(ValueError, match="Trial"):
        dev_host_windows.build_host(source)
    with pytest.raises(ValueError, match="Trial"):
        dev.run(source, policy.state, browser=False)
    compiler.assert_not_called()
    runtime.assert_not_called()
    assert not policy.home.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--temporary"],
        ["--home", "wrong"],
        ["--home", "STATE", "--home", "STATE"],
        ["--home", "STATE", "--browser"],
        ["--home", "STATE", "--trial-development"],
    ],
)
def test_trial_source_entry_rejects_invalid_arguments_before_launch(
    trial_development: tuple[TrialPolicy, Path],
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
):
    from scripts import dev
    from scripts.app_build import dev_host_windows

    policy, _source = trial_development
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dev",
            "--trial-development",
            *[str(policy.state) if v == "STATE" else v for v in arguments],
        ],
    )
    launch, runtime = Mock(), Mock()
    monkeypatch.setattr(dev_host_windows, "enter_host", launch)
    monkeypatch.setattr(dev, "ApplicationRuntime", runtime)
    with pytest.raises((ValueError, SystemExit)):
        dev.main()
    launch.assert_not_called()
    runtime.assert_not_called()
    assert not policy.home.exists()


def test_unmarked_source_cannot_enter_trial_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts import dev
    from scripts.app_build import dev_resources

    monkeypatch.setattr(scopecat, "_TRIAL_POLICY", None, raising=False)
    monkeypatch.delenv(dev_resources.TRIAL_DEVELOPMENT, raising=False)
    monkeypatch.setattr(
        sys, "argv", ["dev", "--trial-development", "--home", str(tmp_path / "unused")]
    )
    source = Mock()
    monkeypatch.setattr(dev, "source_root", source)
    with pytest.raises(ValueError, match="marked interpreter"):
        dev.main()
    source.assert_not_called()
    monkeypatch.setenv(dev_resources.TRIAL_DEVELOPMENT, "lost-core-marker")
    with pytest.raises(ValueError, match="marked package interpreter"):
        dev_resources.trial_development_policy()
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("target", ["source", "entry", "state"])
def test_trial_source_rejects_alias_before_path_resolution(
    trial_development: tuple[TrialPolicy, Path],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
):
    from scripts import dev
    from scripts.app_build import dev_resources

    policy, source = trial_development
    if target == "source":
        moved = source.with_name("moved")
        source.rename(moved)
        source.symlink_to(moved, target_is_directory=True)
    elif target == "entry":
        entry = source / "scripts/dev.py"
        renamed = entry.with_name("real.py")
        entry.rename(renamed)
        entry.symlink_to(renamed)
    else:
        policy.home.mkdir()
        policy.state.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink or junction"):
        dev_resources.trial_development_policy(
            source=source, home=policy.state, entry=Path(dev.__file__)
        )
    assert not (policy.state / "development.lock").exists()


@pytest.mark.parametrize("entry", ["resources", "logged", "native"])
def test_trial_never_builds_resources_or_replaces_native_dependencies(
    trial_development: tuple[TrialPolicy, Path],
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
):
    from scripts.app_build import dev_resources

    policy, source = trial_development
    outside = source.parent / "forbidden-default-cache"
    identity = Mock()
    monkeypatch.setattr(dev_resources, "resource_identity", identity)
    with pytest.raises(ValueError, match="only uses prepared"):
        if entry == "resources":
            dev_resources.prepare_resources(source, outside)
        elif entry == "logged":
            dev_resources.prepare_resources_logged(source, outside, outside / "log")
        else:
            dev_resources.prepare_native_dependency(outside, outside)
    identity.assert_not_called()
    assert not outside.exists() and not policy.home.exists()


def test_trial_reentry_preserves_exact_home_interpreter_mode_and_locator(
    trial_development: tuple[TrialPolicy, Path], monkeypatch: pytest.MonkeyPatch
):
    from scripts.app_build import dev_host, dev_host_windows, dev_resources

    policy, source = trial_development
    executable = policy.home / "development/hosts/Scopecat Dev.exe"
    monkeypatch.delenv(dev_host.MARKER, raising=False)
    monkeypatch.setattr(dev_host_windows, "build_host", Mock(return_value=executable))
    assert (
        dev_resources.trial_development_policy(source=source, home=policy.state)
        is policy
    )
    monkeypatch.setattr(
        dev_host_windows, "trial_development_policy", Mock(return_value=policy)
    )
    monkeypatch.setattr(
        dev_host_windows.psutil,
        "Process",
        Mock(return_value=SimpleNamespace(exe=lambda: sys.executable)),
    )
    launch = MagicMock()
    launch.return_value.__enter__.return_value.wait.return_value = 23
    monkeypatch.setattr(dev_host_windows.subprocess, "Popen", launch)
    with pytest.raises(SystemExit) as outcome:
        dev_host_windows.enter_host(source, policy.state)
    assert outcome.value.code == 23
    assert launch.call_args.args[0] == [
        str(executable),
        "-E",
        "-B",
        "-m",
        "scripts.dev",
        "--home",
        str(policy.state),
        "--trial-development",
    ]
    env = launch.call_args.kwargs["env"]
    assert env[dev_resources.TRIAL_DEVELOPMENT] == policy.identity
    assert env["SCOPECAT_LOCATOR_HOME"] == str(policy.locator)
    assert json.loads(env[dev_host.MARKER])["python"] == sys.executable
    assert launch.call_args.kwargs["cwd"] == source
    assert not policy.home.exists()


def test_trial_preparation_reuses_only_matching_prepared_package(
    trial_development: tuple[TrialPolicy, Path], monkeypatch: pytest.MonkeyPatch
):
    from scripts import dev

    from scopecat_app import trial_window_probe

    policy, source = trial_development
    payload = policy.resources / "payload"
    runtime = Mock(home=policy.state)
    selected = SimpleNamespace(
        python=Path(sys.executable), delivery_root=payload, static_dir=payload / "gui"
    )
    runtime.installation.return_value = selected
    build = Mock()
    monkeypatch.setattr(dev, "prepare_resources_logged", build)
    check = Mock()
    monkeypatch.setattr(trial_window_probe, "platform_preconditions", check)
    assert (
        dev.prepare_application(runtime, source, policy.home / "unused-cache")
        == payload
    )
    check.assert_called_once_with(policy)
    build.assert_not_called()
    runtime.configure.assert_not_called()
    runtime.qualify.assert_not_called()
    runtime.select.assert_not_called()
    selected.delivery_root = source
    with pytest.raises(ValueError, match="prepared package installation"):
        dev.prepare_application(runtime, source, policy.home / "unused-cache")
    assert not policy.home.exists()


@pytest.mark.parametrize("path", ["endpoint", "log", "dependencies"])
def test_trial_vite_refuses_escaped_paths_or_missing_dependencies_before_spawn(
    trial_development: tuple[TrialPolicy, Path],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
):
    from scripts import dev

    policy, source = trial_development
    endpoint = policy.state / "backend.json"
    log = policy.state / "logs/vite.log"
    if path == "endpoint":
        endpoint = source / "backend.json"
    if path == "log":
        log = source / "vite.log"
    launch, install = Mock(), Mock()
    monkeypatch.setattr(dev, "trial_development_policy", Mock(return_value=policy))
    monkeypatch.setattr(dev.subprocess, "Popen", launch)
    monkeypatch.setattr(dev.subprocess, "run", install)
    monkeypatch.setattr(dev.shutil, "which", Mock(return_value="node"))
    with pytest.raises(ValueError, match=r"Trial Vite|locked frontend"):
        dev.start_vite(source, endpoint, log)
    launch.assert_not_called()
    install.assert_not_called()
    assert not policy.home.exists()


def test_trial_native_host_missing_reentry_marker_cannot_recompile_or_respawn(
    trial_development: tuple[TrialPolicy, Path], monkeypatch: pytest.MonkeyPatch
):
    from scripts.app_build import dev_host, dev_host_windows, dev_resources

    policy, source = trial_development
    host = policy.home / "development/own/hosts/reviewed/Scopecat Dev.exe"
    monkeypatch.delenv(dev_host.MARKER, raising=False)
    monkeypatch.setattr(
        dev_host_windows.psutil,
        "Process",
        Mock(return_value=SimpleNamespace(exe=lambda: str(host))),
    )
    assert (
        dev_resources.trial_development_policy(source=source, home=policy.state)
        is policy
    )
    monkeypatch.setattr(
        dev_host_windows, "trial_development_policy", Mock(return_value=policy)
    )
    compiler, build, launch = Mock(), Mock(), Mock()
    monkeypatch.setattr(dev_host_windows, "compiler_environment", compiler)
    monkeypatch.setattr(dev_host_windows, "build_host", build)
    monkeypatch.setattr(dev_host_windows.subprocess, "Popen", launch)
    with pytest.raises(ValueError, match="lost its reentry marker"):
        dev_host_windows.enter_host(source, policy.state)
    compiler.assert_not_called()
    build.assert_not_called()
    launch.assert_not_called()
    assert not policy.home.exists()


@pytest.mark.parametrize("missing", ["mode", "locator", "endpoint", None])
def test_trial_node_child_checks_inheritance_before_loading_dependencies(
    tmp_path: Path, missing: str | None
):
    from scripts import dev
    from scripts.app_build.dev_resources import TRIAL_DEVELOPMENT

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required to execute the fixed trial child script")
    policy = TrialPolicy(
        "child-qa", tmp_path / "home", tmp_path / "home/locator", tmp_path / "resources"
    )
    ui = tmp_path / "source/apps/scopecat-ui"
    entry = tmp_path / "fixture.mjs"
    entry.write_text(
        "export async function createServer(config) { "
        "console.log(JSON.stringify(config)); return {listen: async () => {}}; }"
    )
    endpoint = policy.state / "backend.json"
    environment = dict(
        os.environ,
        **{
            TRIAL_DEVELOPMENT: policy.identity,
            "SCOPECAT_LOCATOR_HOME": str(policy.locator),
            "SCOPECAT_DEV_ENDPOINT_FILE": str(endpoint),
        },
    )
    if missing:
        environment.pop(
            {
                "mode": TRIAL_DEVELOPMENT,
                "locator": "SCOPECAT_LOCATOR_HOME",
                "endpoint": "SCOPECAT_DEV_ENDPOINT_FILE",
            }[missing]
        )
    result = subprocess.run(  # noqa: S603 - inert temporary Node fixture, no server or app
        [
            node,
            "--input-type=module",
            "-e",
            dev.trial_vite_script(policy, ui, entry, endpoint, 32123),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if missing:
        assert result.returncode != 0
        assert "Trial Vite inherited scope mismatch" in result.stderr
        assert result.stdout == ""
    else:
        assert result.returncode == 0, result.stderr
        config = json.loads(result.stdout)
        assert config["cacheDir"] == str(policy.state / "vite-cache")
        assert config["configLoader"] == "native"
        assert config["root"] == str(ui)
        assert config["configFile"] == str(ui / "vite.config.ts")
        assert config["server"] == {
            "host": "127.0.0.1",
            "port": 32123,
            "strictPort": True,
        }
    assert not policy.home.exists()


def test_trial_vite_launch_uses_private_cache_without_installing_or_node_overrides(
    trial_development: tuple[TrialPolicy, Path], monkeypatch: pytest.MonkeyPatch
):
    from scripts import dev

    policy, source = trial_development
    ui = source / "apps/scopecat-ui"
    entry = ui / "node_modules/vite/dist/node/index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// Existing synthetic dependency\n")
    log = policy.state / "logs/vite.log"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(dev, "trial_development_policy", Mock(return_value=policy))
    monkeypatch.setattr(dev.shutil, "which", Mock(return_value="node"))
    monkeypatch.setattr(dev, "available_port", lambda: 32123)
    for name in ("NODE_OPTIONS", "NODE_PATH", "NODE_COMPILE_CACHE"):
        monkeypatch.setenv(name, "outside-trial")
    launch = Mock(return_value=SimpleNamespace(poll=lambda: None))
    install = Mock()
    monkeypatch.setattr(dev.subprocess, "Popen", launch)
    monkeypatch.setattr(dev.subprocess, "run", install)
    client = MagicMock()
    client.return_value.__enter__.return_value.get.return_value.status_code = 200
    monkeypatch.setattr(dev.httpx2, "Client", client)
    _process, url = dev.start_vite(source, policy.state / "backend.json", log)
    assert url == "http://127.0.0.1:32123"
    command = launch.call_args.args[0]
    assert command[:3] == ["node", "--input-type=module", "-e"]
    assert "vite-cache" in command[3] and "configLoader" in command[3]
    environment = launch.call_args.kwargs["env"]
    assert not any(
        name in environment
        for name in ("NODE_OPTIONS", "NODE_PATH", "NODE_COMPILE_CACHE")
    )
    assert environment["SCOPECAT_DEV_ENDPOINT_FILE"] == str(
        policy.state / "backend.json"
    )
    install.assert_not_called()


def test_trial_source_main_requires_stopped_prepared_state_before_compiling(
    trial_development: tuple[TrialPolicy, Path], monkeypatch: pytest.MonkeyPatch
):
    from scripts import dev
    from scripts.app_build import dev_host_windows

    policy, source = trial_development
    monkeypatch.setattr(
        sys, "argv", ["dev", "--home", str(policy.state), "--trial-development"]
    )
    monkeypatch.setattr(dev, "source_root", lambda: source)
    runtime = Mock()
    runtime.status.return_value.state = "running"
    monkeypatch.setattr(dev, "ApplicationRuntime", Mock(return_value=runtime))
    monkeypatch.setattr(
        dev, "prepare_application", Mock(return_value=policy.resources / "payload")
    )
    launch = Mock()
    monkeypatch.setattr(dev_host_windows, "enter_host", launch)
    with pytest.raises(ValueError, match="prepared and stopped"):
        dev.main()
    launch.assert_not_called()
    assert not policy.home.exists()
