"""Qualify a native package; opt into real WebViews only on disposable hosted CI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Protocol, cast

from lab_tools.bundle import file_hash, inventory
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
navigation = []
api = DesktopAPI(
    DesktopSession(runtime, threading.Event()),
    lambda: SimpleNamespace(run_js=navigation.append))
try:
    assert api.create_source(str(home), "authors") == str(workspace)
    assert navigation[-1].startswith("window.location.replace(")
    url = json.loads(
        navigation[-1].removeprefix("window.location.replace(").removesuffix(");"))
    assert "source=" in url and url.endswith("#settings")
    client = create_client_environment(runtime, workspace)
    binding = next(item for item in api.status()["sources"]
                   if item["directory"] == str(workspace))
    assert binding["python"] == str(client)
    execution = binding["execution_python"]
    assert execution != str(selected.python)
    existing = home / "existing code"
    write_author_scaffold(existing)
    before = runtime.status().record
    api.register_source(str(existing), execution)
    assert runtime.status().record == before
    assert {"directory": str(existing), "python": None,
            "execution_python": execution} in api.status()["sources"]
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
    subprocess.run([str(client), "-I", "-c", '''
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
result = runpy.run_path(str(root / "notebooks/02_edit_scan.py"))
(root / "saved-run.json").write_text(json.dumps({"run_id": result["run"].id}))
''', str(workspace)], cwd=workspace, check=True)
finally:
    runtime.stop()
assert runtime.status().state == "stopped"
print("PASS: fixed packaged runtime starts and stops without installation")
"""

ANALYSIS_CHECK = r"""
from dataclasses import dataclass
from pathlib import Path
from importlib.util import find_spec
import sys
import scopecat as sc
from scopecat.measurements.dataset import Dataset

for name in ("scopecat_server", "lab_tools", "lab_teaching", "scopecat_lab"):
    assert find_spec(name) is None, name

@dataclass(frozen=True)
class Mean:
    value: float

@sc.analysis_function
def mean(data: Dataset) -> Mean:
    values = data["result"].require_values()
    return Mean(float(sum(values) / len(values)))

root = Path(sys.argv[1])
with sc.open_capture(
    root / "raw.scopecat", output=root / "analyzed.scopecat"
) as capture:
    publication = capture.analyze(capture.run_ids[0], mean(), key="mean")
    assert abs(publication.result_as(Mean).value.value - 2 / 3) < 1e-12
with sc.open_capture(root / "analyzed.scopecat") as capture:
    saved = capture.published_analysis("mean").result_as(Mean)
    assert abs(saved.value.value - 2 / 3) < 1e-12
"""

DATA_CHECK = r"""
import json, subprocess, sys
from pathlib import Path
import httpx2
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.bundle import file_hash
from lab_tools.desktop_files import export_run, import_capture, save_capture

home = Path(sys.argv[1])
runtime = ApplicationRuntime(home / "data")
selected = runtime.installation()
run_id = json.loads((home / "authors/saved-run.json").read_text())["run_id"]
try:
    record = runtime.start()
    export_run(record.base_url, run_id, home / "raw.scopecat")
finally:
    runtime.stop()
original = file_hash(home / "raw.scopecat")
analysis = home / "analyze.py"
analysis.write_text(sys.argv[3], encoding="utf-8")
# No application is running during external analysis, and the original source
# is unavailable to the isolated client interpreter.
subprocess.run([sys.argv[2], "-I", str(analysis), str(home)], check=True)
assert file_hash(home / "raw.scopecat") == original

viewer = ApplicationRuntime(home / "data-only")
viewer.configure(static_dir=selected.static_dir)
try:
    record = viewer.start()
    first = import_capture(record.base_url, home / "analyzed.scopecat")
    assert first.created
    repeated = import_capture(record.base_url, home / "analyzed.scopecat")
    assert not repeated.created and repeated.capture == first.capture
    save_capture(record.base_url, first.capture.content_hash, home / "copy.scopecat")
    assert file_hash(home / "copy.scopecat") == file_hash(home / "analyzed.scopecat")
    with httpx2.Client(trust_env=False) as http:
        assert http.get(record.base_url + "/api/v1/runs").json()["items"] == []
    assert not (viewer.home / "environments").exists()
finally:
    viewer.stop()
assert runtime.status().state == viewer.status().state == "stopped"
(home / "data-journey.json").write_text(json.dumps({
    "export_external_analysis_import": "passed",
    "original_unchanged": "passed",
    "data_only_without_execution_environment": "passed",
    "human_interaction": "not-evaluated",
}, indent=2) + "\n")
print("PASS: native export, independent analysis and data-only import")
"""


def verify(
    app: Path,
    home: Path,
    installer: Path | None = None,
    *,
    keep_work: bool = False,
    native_windows: bool = False,
) -> None:
    """Retain reports only; never rename or mutate the caller's package."""
    app = app.resolve()
    home = home.resolve()
    if home.is_relative_to(app):
        raise ValueError("Acceptance reports must be outside the application")
    if native_windows:
        subprocess.run(  # noqa: S603 - refuse unsafe hosts before any native execution
            [
                sys.executable,
                str(Path(__file__).with_name("verify_native_windows.py").resolve()),
                "--check-hosted",
                str(home),
            ],
            check=True,
        )
    home.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(
        prefix="work-", dir=home, delete=not keep_work
    ) as directory:
        work = Path(directory)
        copied = work / app.name
        shutil.copytree(app.resolve(), copied, symlinks=True)
        state = work / "acceptance"
        try:
            _verify(copied, state, installer, native_windows=native_windows)
        finally:
            for relative in (
                "result.json",
                "data-journey.json",
                "reset-recovery/result.json",
                "reset-recovery/reset.json",
                "reset-recovery/reopen.json",
                "reset-recovery/reset.log",
                "reset-recovery/reopen.log",
                "reset-recovery/reset-python.log",
                "reset-recovery/reopen-python.log",
                "reset-recovery/cleanup.log",
                "reset-recovery/desktop.log",
                "reset-recovery/candidate-daemon.log",
                "native-windows/result.json",
                "native-windows/sequence.json",
                "native-windows/identity.json",
                "native-windows/host.log",
                "native-windows/python.log",
                "native-windows/cleanup.log",
                "native-windows/acquisition.log",
                "native-windows/acquisition-process.json",
                "windows-storage/result.json",
                "windows-storage/draft-fixture.json",
                "windows-storage/A.json",
                "windows-storage/B.json",
                "windows-storage/C.json",
                "windows-storage/A.log",
                "windows-storage/B.log",
                "windows-storage/C.log",
                "cocoa-storage/result.json",
                "cocoa-storage/draft-fixture.json",
                "cocoa-storage/A.json",
                "cocoa-storage/B.json",
                "cocoa-storage/C.json",
                "cocoa-storage/A.log",
                "cocoa-storage/B.log",
                "cocoa-storage/C.log",
                "configuration-sharing/result.json",
                "configuration-sharing/sender-daemon.log",
                "configuration-sharing/receiver-daemon.log",
                "data/native-start.log",
                "data/desktop/desktop.log",
            ):
                source = state / relative
                if source.is_file():
                    target = home / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            if keep_work:
                print(f"Retained acceptance workspace: {work}")
    if installer is not None:
        resources = app / (
            "Contents/Resources" if sys.platform == "darwin" else "resources"
        )
        manifest = resources / "payload/bundle.json"
        bundle = cast("dict[str, object]", json.loads(manifest.read_bytes()))
        shutil.copyfile(manifest, home / "bundle.json")
        (home / "native-release.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "installer": installer.name,
                    "sha256": file_hash(installer),
                    "bundle_sha256": file_hash(manifest),
                    "sources": bundle["sources"],
                    "target": bundle["target"],
                    "runtime": bundle["runtime"],
                    "build_id": bundle["build_id"],
                    "distribution": "prerelease; no trusted signing/notarization",
                    "external": [
                        "author source and dependencies",
                        "vendor SDK/firmware",
                    ],
                    "native_interaction": "not-evaluated",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def verify_recovery_probes(app: Path, home: Path) -> None:
    """Collect both bounded journeys, but never cross an uncertain cleanup."""
    reports = home / "native-windows"
    sequence: dict[str, object] = {}
    failures: list[Exception] = []
    try:
        try:
            subprocess.run(  # noqa: S603 - fixed isolated probe
                [
                    sys.executable,
                    str(Path(__file__).with_name("verify_native_windows.py").resolve()),
                    str(app),
                    str(home),
                ],
                check=True,
            )
            sequence["windows"] = {"exit_code": 0}
        except Exception as error:
            failures.append(error)
            sequence["windows"] = {
                "exit_code": error.returncode
                if isinstance(error, subprocess.CalledProcessError)
                else None,
                "error": traceback.format_exc(),
            }
        try:
            result = cast(
                "dict[str, object]", json.loads((reports / "result.json").read_bytes())
            )
            if (
                result.get("host_stopped") is not True
                or result.get("cleanup_completed") is not True
            ):
                raise RuntimeError("Window host/service cleanup was not confirmed")
        except Exception as error:
            failures.append(error)
            sequence["reset"] = {
                "status": "not-run",
                "reason": "Preceding probe cleanup is incomplete or unverified",
                "error": traceback.format_exc(),
            }
        else:
            try:
                subprocess.run(  # noqa: S603 - separate reset copy/home after cleanup
                    [
                        sys.executable,
                        str(
                            Path(__file__).with_name("verify_native_reset.py").resolve()
                        ),
                        str(app),
                        str(home),
                    ],
                    check=True,
                )
                sequence["reset"] = {"status": "passed", "exit_code": 0}
            except Exception as error:
                failures.append(error)
                sequence["reset"] = {
                    "status": "failed",
                    "exit_code": error.returncode
                    if isinstance(error, subprocess.CalledProcessError)
                    else None,
                    "error": traceback.format_exc(),
                }
    finally:
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "sequence.json").write_text(
            json.dumps(sequence, indent=2), encoding="utf-8"
        )
    if failures:
        raise ExceptionGroup("Native window/reset qualification failed", failures)


def _verify(
    app: Path,
    home: Path,
    installer: Path | None = None,
    *,
    native_windows: bool = False,
) -> None:
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
    environment = dict(
        os.environ,
        PATH="",
        UV_PYTHON_DOWNLOADS="never",
        UV_OFFLINE="1",
        UV_CACHE_DIR=str(home / "empty-cache"),
    )
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
    if native_windows:
        verify_recovery_probes(relocated, home)
        if sys.platform == "darwin":
            subprocess.run(  # noqa: S603 - same live disposable acceptance state
                [
                    sys.executable,
                    str(Path(__file__).with_name("verify_cocoa_storage.py").resolve()),
                    str(relocated),
                    str(home),
                ],
                check=True,
            )
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603 - same live disposable acceptance state
                [
                    sys.executable,
                    str(
                        Path(__file__).with_name("verify_windows_storage.py").resolve()
                    ),
                    str(relocated),
                    str(home),
                ],
                check=True,
            )
        assert inventory(relocated, (".",)) == before, (
            "Native probes modified application"
        )
    client = (
        home
        / "authors/.venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    _ = subprocess.run(  # noqa: S603 - packaged data API and independent client
        [
            str(python),
            "-I",
            "-B",
            "-c",
            DATA_CHECK,
            str(home),
            str(client),
            ANALYSIS_CHECK,
        ],
        env=environment,
        check=True,
    )
    assert inventory(relocated, (".",)) == before, "Data journey modified application"
    payload = relocated / (
        "Contents/Resources/payload"
        if sys.platform == "darwin"
        else "resources/payload"
    )
    subprocess.run(  # noqa: S603 - installed Python, bounded analytic sharing journey
        [
            str(python),
            "-I",
            "-B",
            str(Path(__file__).with_name("verify_configuration_sharing.py").resolve()),
            str(home / "configuration-sharing"),
            str(payload),
        ],
        env=environment,
        check=True,
    )
    assert inventory(relocated, (".",)) == before, "Sharing modified application"
    if sys.platform == "darwin":
        verify_signature(relocated)
    relocated.rename(relocated.with_name("Removed " + app.name))
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


class Arguments(Protocol):
    app: Path
    reports: Path
    installer: Path | None
    keep_work: bool
    native_windows: bool


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("reports", type=Path)
    parser.add_argument("installer", type=Path, nargs="?")
    parser.add_argument(
        "--keep-work", action="store_true", help="Retain disposable work for diagnosis"
    )
    parser.add_argument(
        "--native-windows",
        action="store_true",
        help="Real WebViews; disposable GitHub-hosted Mac/Windows runners only",
    )
    args = cast("Arguments", cast("object", parser.parse_args()))
    verify(
        args.app,
        args.reports,
        args.installer,
        keep_work=args.keep_work,
        native_windows=args.native_windows,
    )
