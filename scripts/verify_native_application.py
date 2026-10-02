"""Qualify a built native app in an isolated directory without opening its UI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from lab_tools.bundle import inventory
from lab_tools.macos_signing import verify as verify_signature

RUNTIME_CHECK = r"""
import json, os, subprocess, sys, threading
from pathlib import Path
from types import SimpleNamespace
import httpx2
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.author_environment import create_client_environment
from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_session import DesktopSession
from scopecat_server.scaffold import write_author_scaffold
home = Path(sys.argv[1])
runtime = ApplicationRuntime(home / "data")
selected = runtime.installation()
assert not (home / "software").exists()
workspace = home / "authors"
urls = []
api = DesktopAPI(
    DesktopSession(runtime, threading.Event()),
    lambda: SimpleNamespace(load_url=urls.append))
try:
    assert api.create_source(str(home), "authors") == str(workspace)
    assert "source=" in urls[-1] and urls[-1].endswith("#settings")
    client = create_client_environment(runtime, workspace)
    expected_source = {"directory": str(workspace), "python": str(client)}
    assert expected_source in api.status()["sources"]
    existing = home / "existing code"
    write_author_scaffold(existing)
    api.register_source(str(existing))
    assert {"directory": str(existing), "python": None} in api.status()["sources"]
    base = subprocess.check_output([str(client), "-I", "-c",
        "import sys, scopecat, ipykernel; print(sys.base_prefix)"], text=True).strip()
    assert Path(base).resolve().is_relative_to(
        (workspace / ".scopecat-python").resolve())
    subprocess.run([str(client), "-I", "-c", '''
from importlib.util import find_spec
for name in ("scopecat_server", "lab_tools", "lab_teaching", "webview", "jupyterlab"):
    assert find_spec(name) is None, name
import pip
'''], check=True)
    record = runtime.start()
    with httpx2.Client(trust_env=False) as http:
        assert http.get(record.base_url + "/api/v1/health").json()["status"] == "ok"
        assert http.get(record.base_url + "/").status_code == 200
    subprocess.run([str(client), "-I", str(workspace / "notebooks/02_edit_scan.py")],
        cwd=workspace, check=True)
finally:
    runtime.stop()
assert runtime.status().state == "stopped"
print("PASS: fixed packaged runtime starts and stops without installation")
"""


def verify(app: Path, home: Path, installer: Path | None = None) -> None:
    app = app.resolve()
    home = home.resolve()
    if home.exists():
        raise FileExistsError("Use a fresh isolated acceptance home")
    relocated = app.with_name("Relocated 中文 " + app.name)
    app.rename(relocated)
    home.mkdir(parents=True)
    if sys.platform == "darwin":
        verify_signature(relocated)
    result = home / "result.json"
    executable = relocated / (
        "Contents/MacOS/Scopecat" if sys.platform == "darwin" else "Scopecat.exe"
    )
    before = inventory(relocated, (".",))
    environment = dict(os.environ, PATH="", UV_PYTHON_DOWNLOADS="never")
    command = [str(executable), "--home", str(home), "--check-result", str(result)]
    _ = subprocess.run(command, env=environment, check=True)  # noqa: S603
    first = result.read_bytes()
    _ = subprocess.run(command, env=environment, check=True)  # noqa: S603
    assert result.read_bytes() == first
    assert inventory(relocated, (".",)) == before, "Native app was modified on launch"
    state = cast("dict[str, str | bool]", json.loads(first))
    assert state["status"] == "stopped"
    if sys.platform == "darwin":
        assert state["bundle_identifier"] == "org.scopecat.desktop", (
            "Cocoa lost the app identity; menu-bar registration can fail"
        )
    python = Path(cast("str", state["python"])).resolve()
    assert python.is_relative_to(relocated)
    _ = subprocess.run(  # noqa: S603 - fixed packaged runtime
        [str(python), "-I", "-B", "-c", RUNTIME_CHECK, str(home)],
        env=environment,
        check=True,
    )
    assert inventory(relocated, (".",)) == before, "Runtime modified application files"
    if sys.platform == "darwin":
        verify_signature(relocated)
    relocated.rename(relocated.with_name("Removed " + app.name))
    client = (
        home
        / "authors/.venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    client_check = [
        str(client),
        "-I",
        "-c",
        "import scopecat; print('PASS: independent author Python')",
    ]
    _ = subprocess.run(client_check, env=environment, check=True)  # noqa: S603
    if installer is not None:
        if sys.platform == "win32":
            installed = home / "Installed Scopecat"
            _ = subprocess.run(  # noqa: S603 - explicit acceptance-only installer
                [
                    str(installer.resolve()),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    f"/DIR={installed}",
                ],
                check=True,
            )
            _ = subprocess.run(  # noqa: S603 - installed native entry, still headless
                [str(installed / "Scopecat.exe"), *command[1:]],
                env=environment,
                check=True,
            )
            _ = subprocess.run(  # noqa: S603 - installer-owned uninstaller on CI
                [
                    str(installed / "unins000.exe"),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                ],
                check=True,
            )
            assert not (installed / "Scopecat.exe").exists()
            _ = subprocess.run(  # noqa: S603 - retained data after native uninstall
                client_check,
                env=environment,
                check=True,
            )
            print(
                "PASS: Windows setup, installed entry and uninstall "
                "preserve user environments"
            )
        else:
            _ = subprocess.run(  # noqa: S603 - read-only image integrity check
                ["/usr/bin/hdiutil", "verify", str(installer.resolve())],
                check=True,
            )
    print(
        "PASS: native relocation, empty PATH, repeat startup, "
        "immutable app and clean stop"
    )


if __name__ == "__main__":
    verify(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        Path(sys.argv[3]) if len(sys.argv) > 3 else None,
    )
