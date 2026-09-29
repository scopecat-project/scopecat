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
assert Path(sys.base_prefix).is_relative_to(home / "software")
workspace = home / "authors"
workspace.mkdir(exist_ok=True)
client = create_client_environment(runtime, workspace)
base = subprocess.check_output([str(client), "-I", "-c",
    "import sys, scopecat; print(sys.base_prefix)"], text=True).strip()
assert Path(base) == Path(sys.base_prefix)
try:
    record = runtime.start()
    with httpx2.Client(trust_env=False) as http:
        assert http.get(record.base_url + "/api/v1/health").json()["status"] == "ok"
        assert http.get(record.base_url + "/").status_code == 200
finally:
    runtime.stop()
assert runtime.status().state == "stopped"
print("PASS: retained runtime and author Python survive removal of native app")
"""


def verify(app: Path, home: Path) -> None:
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
    assert state["status"] == "stopped" and state["update_available"] is False
    python = Path(cast("str", state["python"]))
    assert python.is_relative_to(home / "software")
    relocated.rename(relocated.with_name("Removed " + app.name))
    _ = subprocess.run(  # noqa: S603 - retained runtime after app removal
        [str(python), "-I", "-c", RUNTIME_CHECK, str(home)],
        env=environment,
        check=True,
    )
    print(
        "PASS: native relocation, empty PATH, repeat startup, "
        "immutable app and clean stop"
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
