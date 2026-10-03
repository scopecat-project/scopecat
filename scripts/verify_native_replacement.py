"""Replace two distinct native builds in an isolated home and retain user work."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol, cast

from lab_tools.bundle import file_hash, inventory

SEED = r'''
import json, subprocess, sys
from pathlib import Path
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.author_environment import (
    create_client_environment, prepare_execution_environment,
)
from scopecat_server.scaffold import write_author_scaffold
home = Path(sys.argv[1])
runtime = ApplicationRuntime(home / "data")
source = home / "authors"
write_author_scaffold(source)
client = create_client_environment(runtime, source)
execution = prepare_execution_environment(runtime, source, offline=True)
identity = runtime.register_source(source, python=execution)
assert execution != client and execution != runtime.installation().python
try:
    runtime.start()
    subprocess.run([str(client), "-I", "-c", """
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
result = runpy.run_path(str(root / 'notebooks/02_edit_scan.py'))
(root / 'saved-run.json').write_text(json.dumps({'run_id': result['run'].id}))
""", str(source)], check=True)
finally:
    runtime.stop()
assert runtime.status().state == "stopped"
(home / "source-id.txt").write_text(identity)
'''

READ = r"""
import json, sys
from pathlib import Path
import scopecat as sc
source = Path(sys.argv[1])
saved = json.loads((source / "saved-run.json").read_text())
with sc.open_project(source).authoring() as session:
    assert [item.run_id for item in session.list_runs().items] == [saved["run_id"]]
    values = session.run(saved["run_id"]).measurements()["result"].require_values()
    assert list(values) == [0.5, 1.0, 0.5]
print("PASS: original user Python reads the original measurement without reacquiring")
"""

CHECK = r"""
import subprocess, sys
from pathlib import Path
from lab_tools.application_runtime import ApplicationRuntime
home = Path(sys.argv[1])
runtime = ApplicationRuntime(home / "data")
assert runtime.source(home / "authors") == (home / "source-id.txt").read_text()
try:
    runtime.start()
    subprocess.run(
        [sys.argv[2], "-I", "-c", sys.argv[3], str(home / "authors")], check=True)
finally:
    runtime.stop()
assert runtime.status().state == "stopped"
"""


def verify(
    previous: Path, current: Path, home: Path, *, keep_work: bool = False
) -> None:
    previous, current, home = previous.resolve(), current.resolve(), home.resolve()
    if any(home.is_relative_to(app) for app in (previous, current)):
        raise ValueError("Acceptance reports must be outside the applications")
    home.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(
        prefix="work-", dir=home, delete=not keep_work
    ) as directory:
        workspace = Path(directory) / "replacement"
        try:
            _verify(previous, current, workspace)
        finally:
            for relative in (
                "before.json",
                "after.json",
                "replacement.json",
                "data/native-start.log",
                "data/desktop/desktop.log",
            ):
                source = workspace / relative
                if source.is_file():
                    target = home / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            if keep_work:
                print(f"Retained replacement workspace: {directory}")


def _verify(previous: Path, current: Path, home: Path) -> None:
    previous, current, home = previous.resolve(), current.resolve(), home.resolve()
    resources = Path("Contents/Resources" if sys.platform == "darwin" else "resources")
    manifests = [app / resources / "payload/bundle.json" for app in (previous, current)]
    builds = [json.loads(path.read_text()) for path in manifests]
    if builds[0]["files"] == builds[1]["files"]:
        raise ValueError(
            "Provide distinct builds; reinstalling one build is not an update"
        )
    home.mkdir(parents=True, exist_ok=False)
    installed = home / previous.name
    executable = installed / (
        "Contents/MacOS/Scopecat" if sys.platform == "darwin" else "Scopecat.exe"
    )
    python = (
        installed
        / resources
        / "python"
        / ("bin/python3" if sys.platform == "darwin" else "python.exe")
    )
    client = (
        home
        / "authors/.venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    environment = dict(os.environ, PATH="", UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")

    def run(arguments: list[str]) -> None:
        subprocess.run(arguments, env=environment, check=True)  # noqa: S603

    def launch(label: str) -> None:
        run([str(executable), "--home", str(home), "--check-result", str(home / label)])

    shutil.copytree(previous, installed)
    launch("before.json")
    run([str(python), "-I", "-B", "-c", SEED, str(home)])
    source_files = inventory(home / "authors", ("src",))
    client_configuration = (home / "authors/.venv/pyvenv.cfg").read_bytes()
    installed.rename(home / "retired-package")
    shutil.copytree(current, installed)
    application_files = inventory(installed, (".",))
    selection = home / "data/installation.json"
    before_failure = selection.read_bytes()
    gui = installed / resources / "payload/gui"
    unavailable = gui.with_name("gui-unavailable")
    gui.rename(unavailable)
    try:
        failed = subprocess.run(  # noqa: S603 - isolated incomplete package probe
            [
                str(executable),
                "--home",
                str(home),
                "--check-result",
                str(home / "failed.json"),
            ],
            env=environment,
            check=False,
        )
        assert failed.returncode != 0
        assert not (home / "failed.json").exists()
        assert selection.read_bytes() == before_failure
    finally:
        unavailable.rename(gui)
    launch("after.json")
    run([str(python), "-I", "-B", "-c", CHECK, str(home), str(client), READ])
    assert inventory(installed, (".",)) == application_files
    assert inventory(home / "authors", ("src",)) == source_files
    assert (home / "authors/.venv/pyvenv.cfg").read_bytes() == client_configuration
    (home / "replacement.json").write_text(
        json.dumps(
            {
                "previous_manifest": file_hash(manifests[0]),
                "current_manifest": file_hash(manifests[1]),
                "replacement": "passed",
                "failed_start_retry": "passed",
                "retained_measurement": "passed",
                "independent_user_python": "passed",
                "native_interaction": "not-evaluated",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("PASS: package replacement preserves source, identity and scientific data")


class Arguments(Protocol):
    previous: Path
    current: Path
    reports: Path
    keep_work: bool


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args()))
    verify(args.previous, args.current, args.reports, keep_work=args.keep_work)
