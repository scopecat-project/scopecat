"""Native entries carry paths as data and do not run the desktop while installing."""

import json
import plistlib
import shlex

from lab_tools import desktop_install


def test_mac_bundle_uses_selected_python_without_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_install.sys, "platform", "darwin")
    home = tmp_path / "中文 space's"
    python = home / "runtime/bin/python"
    app = desktop_install.install_entry(home, python)
    assert app == home / "Scopecat.app"
    executable = app / "Contents/MacOS/Scopecat"
    assert shlex.split(executable.read_text().splitlines()[1]) == [
        "exec",
        str(python),
        str(home / "lab.py"),
        "--action",
        "desktop",
    ]
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
