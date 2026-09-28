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
    seen = []

    def run(args, **kwargs):
        seen.append(json.loads((tmp_path / ".desktop-entry.json").read_text()))
        assert str(tmp_path) not in args[-1]
        assert kwargs == {"cwd": tmp_path, "check": True}

    monkeypatch.setattr(desktop_install.subprocess, "run", run)
    assert (
        desktop_install.install_entry(tmp_path, tmp_path / "python.exe")
        == tmp_path / "Scopecat.lnk"
    )
    assert seen[0]["python"] == str(tmp_path / "pythonw.exe")
    assert not (tmp_path / ".desktop-entry.json").exists()
