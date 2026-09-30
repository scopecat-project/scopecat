"""Native entries carry paths as data and do not run the desktop while installing."""

import json
import os
import plistlib
import shlex
import sys
from pathlib import Path

import pytest

from lab_tools import desktop_install, installation_paths
from lab_tools.installation_paths import InstallationPaths


def test_mac_bundle_uses_selected_python_without_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_install.sys, "platform", "darwin")
    home = tmp_path / "中文 space's"
    python = home / "runtime/bin/python"
    app = desktop_install.install_entry(home, python)
    assert app == home / "Scopecat.app"
    executable = app / "Contents/MacOS/Scopecat"
    assert shlex.split(executable.read_text(encoding="utf-8").splitlines()[1]) == [
        "exec",
        str(python),
        str(home / "lab.py"),
        "--action",
        "desktop",
    ]
    if os.name != "nt":
        assert executable.stat().st_mode & 0o111
    with (app / "Contents/Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleExecutable"] == "Scopecat"


def test_windows_shortcut_uses_pythonw_and_no_shell_path_interpolation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(desktop_install.sys, "platform", "win32")
    monkeypatch.setattr(desktop_install.shutil, "which", lambda _: "powershell.exe")
    home = tmp_path / "中文 space's"
    home.mkdir()
    seen = []

    def run(args, **kwargs):
        seen.append(json.loads((home / ".desktop-entry.json").read_text()))
        assert str(home) not in args[-1]
        assert kwargs == {"cwd": home, "check": True}

    monkeypatch.setattr(desktop_install.subprocess, "run", run)
    assert (
        desktop_install.install_entry(home, home / "python.exe")
        == home / "Scopecat.lnk"
    )
    assert seen[0]["python"] == str(home / "pythonw.exe")
    assert not (home / ".desktop-entry.json").exists()


def test_isolated_installation_does_not_use_daily_locations(tmp_path):
    paths = InstallationPaths.isolated(tmp_path)
    assert all(
        path.is_relative_to(tmp_path)
        for path in (
            paths.state,
            paths.software,
            paths.cache,
            paths.workspace,
        )
    )
    assert paths.entry is None or paths.entry.is_relative_to(tmp_path)


def test_mac_entry_is_outside_data_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(installation_paths.sys, "platform", "darwin")
    monkeypatch.setattr(installation_paths.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        installation_paths, "user_data_path", lambda *_a, **_kw: tmp_path / "support"
    )
    monkeypatch.setattr(
        installation_paths, "user_cache_path", lambda *_a, **_kw: tmp_path / "cache"
    )
    paths = InstallationPaths.current_user()
    assert paths.entry == tmp_path / "Applications/Scopecat.app"
    assert paths.software == paths.state / "software"
    assert not paths.software.is_relative_to(paths.entry)
    assert not paths.state.is_relative_to(paths.entry)
    python = paths.software / "releases/test/runtime/bin/python"
    assert (
        desktop_install.install_entry(paths.software, python, paths.entry)
        == paths.entry
    )
    assert (paths.entry / "Contents/MacOS/Scopecat").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Shell32 known folders")
def test_windows_user_program_and_start_menu_locations():
    paths = InstallationPaths.current_user()
    assert paths.software.is_absolute()
    assert paths.software.name == "software"
    assert paths.entry is not None and paths.entry.is_absolute()
    assert paths.entry.suffix == ".lnk"
    assert paths.workspace == Path.home() / "Scopecat/experiments"
    assert paths.software.is_relative_to(paths.state)


@pytest.fixture
def native_start(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from lab_tools import native_bootstrap

    paths = InstallationPaths.isolated(tmp_path / "isolated")
    paths.state.mkdir(parents=True)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "bundle.json").write_text('{"build": "first"}')
    selected = SimpleNamespace(python=Path(sys.executable), package="first")
    updates = []

    class Runtime:
        pending = paths.state / "installation-pending.json"

        def __init__(self, home):
            assert home == paths.state

        def configure(self, **_kwargs):
            return selected

        def installation(self):
            return selected

        def qualify(self, python, static_dir):
            assert static_dir == payload / "gui"
            return SimpleNamespace(
                python=python,
                package=json.loads((payload / "bundle.json").read_text())["build"],
            )

        def select(self, candidate):
            updates.append(candidate)
            selected.python = candidate.python
            selected.package = candidate.package

        def status(self):
            return SimpleNamespace(state="stopped")

    monkeypatch.setattr(native_bootstrap, "ApplicationRuntime", Runtime)
    args = SimpleNamespace(
        payload=payload,
        home=tmp_path / "isolated",
        check_result=tmp_path / "checked.json",
        entry=None,
    )
    return native_bootstrap, args, paths, selected, updates


def test_native_package_selects_its_own_version_before_reporting_ready(native_start):
    bootstrap, args, paths, selected, updates = native_start
    bootstrap.launch(args, paths)
    selected.python = paths.software / "first/python"
    bootstrap.launch(args, paths)
    assert len(updates) == 1
    bootstrap.launch(args, paths)
    assert len(updates) == 1
    assert selected.python == Path(sys.executable)


def test_native_initializer_failure_retries_without_marking_setup_complete(
    native_start, monkeypatch
):
    bootstrap, args, paths, _selected, _updates = native_start
    (args.payload / "initialize.py").write_text("# trusted laboratory setup")
    calls = []

    def run(command):
        calls.append(command)
        if len(calls) == 1:
            raise RuntimeError("setup interrupted")

    monkeypatch.setattr(bootstrap, "_run", run)
    with pytest.raises(RuntimeError, match="setup interrupted"):
        bootstrap.launch(args, paths)
    assert not (paths.state / "native-setup.json").exists()
    bootstrap.launch(args, paths)
    bootstrap.launch(args, paths)
    assert len(calls) == 2
    assert (paths.state / "native-setup.json").is_file()
    assert not (paths.state / "native-setup.pending").exists()


def test_native_package_refreshes_runtime_at_the_same_install_path(native_start):
    bootstrap, args, paths, selected, updates = native_start
    bootstrap.launch(args, paths)
    (args.payload / "bundle.json").write_text('{"build": "second"}')
    bootstrap.launch(args, paths)
    assert len(updates) == 1
    assert selected.package == "second"
    assert selected.python == Path(sys.executable)
    assert not paths.software.exists()
