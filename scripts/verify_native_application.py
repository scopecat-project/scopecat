"""Qualify a built native app in an isolated directory without opening its UI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from lab_tools.bundle import inventory

RUNTIME_CHECK = r"""
import json, os, subprocess, sys
from pathlib import Path
import httpx2
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.author_environment import create_client_environment
home = Path(sys.argv[1])
runtime = ApplicationRuntime(home / "data")
selected = runtime.installation()
assert not (home / "software").exists()
workspace = home / "authors"
workspace.mkdir(exist_ok=True)
client = create_client_environment(runtime, workspace)
base = subprocess.check_output([str(client), "-I", "-c",
    "import sys, scopecat; print(sys.base_prefix)"], text=True).strip()
assert Path(base).is_relative_to(workspace / ".scopecat-python")
try:
    record = runtime.start()
    with httpx2.Client(trust_env=False) as http:
        assert http.get(record.base_url + "/api/v1/health").json()["status"] == "ok"
        assert http.get(record.base_url + "/").status_code == 200
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
    python = Path(cast("str", state["python"]))
    assert python.is_relative_to(relocated)
    _ = subprocess.run(  # noqa: S603 - fixed packaged runtime
        [str(python), "-I", "-B", "-c", RUNTIME_CHECK, str(home)],
        env=environment,
        check=True,
    )
    assert inventory(relocated, (".",)) == before, "Runtime modified application files"
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
