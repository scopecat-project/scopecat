"""Install a local pilot bundle and exercise its GUI and durable virtual run.

Run with workspace Python and uv; the journey itself runs with only the installed
bundle in a fresh environment, outside the checkout and without Node on PATH.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from scopecat.project import Project


@dataclass(frozen=True)
class _Summary:
    mean: float
    points: int


class _Manifest(TypedDict):
    files: dict[str, str]
    packages: dict[str, str]
    ui_version: str


class _Arguments(argparse.Namespace):
    bundle: Path = Path()
    installed: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(namespace=_Arguments())
    bundle = arguments.bundle.resolve()
    if arguments.installed:
        _installed_journey(bundle)
        return
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to create the isolated installation")
    manifest = cast("_Manifest", json.loads((bundle / "manifest.json").read_text()))
    for name, digest in manifest["files"].items():
        with (bundle / name).open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == digest, name
    with tempfile.TemporaryDirectory(prefix="scopecat-installed-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        _run([uv, "venv", str(environment), "--python", sys.executable], cwd=root)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        _run(
            [
                uv,
                "pip",
                "sync",
                "--python",
                str(python),
                "--require-hashes",
                "--only-binary",
                ":all:",
                "requirements.txt",
            ],
            cwd=bundle,
        )
        fixture = root / "installed-author-lab"
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "fixtures/installed_author_lab",
            fixture,
        )
        wheels = root / "method-wheels"
        _run([uv, "build", str(fixture), "--wheel", "--out-dir", str(wheels)], cwd=root)
        [wheel] = wheels.glob("*.whl")
        _run(
            [uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            cwd=root,
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("SCOPECAT_DAEMON_URL", None)
        env["PATH"] = str(binaries)
        print(
            _run(
                [
                    str(python),
                    str(Path(__file__).resolve()),
                    str(bundle),
                    "--installed",
                ],
                cwd=root,
                env=env,
            ).strip()
        )


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - controlled local interpreter/uv arguments
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{command!r}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def _installed_journey(bundle: Path) -> None:
    from importlib.metadata import version

    import httpx2

    import scopecat
    from scopecat.daemon.client import DaemonClient
    from scopecat.daemon.endpoint import read_daemon_endpoint_record
    from scopecat.project import open_project
    from scopecat.records.measurement import MeasurementScalar
    from scopecat_server.lifecycle import (  # noqa: TID251 - installed-server integration check
        open_project_gui,
        stop_project,
    )

    assert Path(scopecat.__file__).is_relative_to(Path(sys.prefix))
    assert importlib.util.find_spec("reference_lab") is None
    assert importlib.util.find_spec("scopecat_testkit") is None
    assert shutil.which("node") is None
    manifest = cast("_Manifest", json.loads((bundle / "manifest.json").read_text()))
    for package, expected in manifest["packages"].items():
        assert version(package) == expected
    project_root = Path.cwd() / "project with spaces"
    cli = [sys.executable, "-m", "scopecat_server.cli"]
    _run([*cli, "init", str(project_root)], cwd=Path.cwd())
    _run([*cli, "config", "check", str(project_root)], cwd=Path.cwd())
    project_manifest = project_root / "scopecat.toml"
    project_manifest.write_text(
        project_manifest.read_text()
        + '\n[authors.packages]\npilot_methods = "scopecat-pilot-methods"\n'
    )
    application = project_root / "src/scopecat_lab/application.py"
    application.write_text(
        application.read_text().replace(
            'author_modules=("scopecat_lab.authored",)',
            'author_modules=("pilot_methods", "scopecat_lab.authored")',
        )
    )
    local = project_root / "src/scopecat_lab/authored/signal.py"
    local.write_text(
        local.read_text().replace(
            "return 1.0 / (1.0 + (position - center) ** 2)",
            "from pilot_methods.signal import response as shared_response\n"
            "    return shared_response(position, center)",
        )
    )
    project = open_project(project_root)
    try:
        _run([*cli, "start", str(project_root)], cwd=project_root)
        endpoint = read_daemon_endpoint_record(project_root)
        assert endpoint is not None
        with httpx2.Client(base_url=endpoint.base_url, trust_env=False) as http:
            index = http.get("/")
            assert index.status_code == 200
            assets = cast(
                "list[str]", re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.text)
            )
            assert assets
            for asset in assets:
                response = http.get(asset)
                assert response.status_code == 200
                assert "text/html" not in response.headers["content-type"]
            assert (
                http.get("/build-info.json").json()["ui_version"]
                == manifest["ui_version"]
            )
        with patch("webbrowser.open", return_value=True) as browser:
            assert open_project_gui(project) == endpoint.base_url
            browser.assert_called_once_with(endpoint.base_url)
        output = _run(
            [sys.executable, str(project_root / "notebooks/01_first_run.py")],
            cwd=project_root,
        )
        summary_line, console_url = output.strip().splitlines()
        summary = cast("dict[str, str]", ast.literal_eval(summary_line))
        assert summary["status"] == "completed", output
        run_id = summary["run_id"]
        assert parse_qs(urlparse(console_url).query) == {"run": [run_id]}
        with DaemonClient(endpoint.base_url) as client:
            preview = client.measurement_preview(run_id)
        [record] = preview.items
        temperature = record.observables["temperature"]
        assert isinstance(temperature, MeasurementScalar)
        assert temperature.value == 0.02 and temperature.unit == "K"
        assert record.acquisition_evidence.events[0].instrument_id == "thermometer"
        scan_output = _run(
            [sys.executable, str(project_root / "notebooks/02_edit_scan.py")],
            cwd=project_root,
        )
        scan = cast(
            "dict[str, object]", ast.literal_eval(scan_output.strip().splitlines()[0])
        )
        assert scan["points"] == 3 and scan["mean"] == 2 / 3
        shared_run_id = _shared_author_journey(project, str(scan["run_id"]))
        _run([*cli, "stop", str(project_root)], cwd=project_root)
        _run([*cli, "start", str(project_root)], cwd=project_root)
        restarted = read_daemon_endpoint_record(project_root)
        assert restarted is not None
        with DaemonClient(restarted.base_url) as client:
            assert client.measurement_preview(run_id) == preview
        with project.authoring() as author:
            retained = author.run(str(scan["run_id"]))
            assert retained.measurements()["result"].require_values() == (0.5, 1.0, 0.5)
            assert (
                retained.published_analysis(str(scan["analysis_id"]))
                .fact("result")
                .value
                is not None
            )
            restored = author.analyze_as(
                shared_run_id, "pilot_methods.signal:summarize", _Summary
            )
            assert restored.value.mean == 2 / 3
        assert "running" in _run([*cli, "status", str(project_root)], cwd=project_root)
        print(f"installed GUI, virtual measurement {run_id}, and restart verified")
    except Exception:
        log = project_root / ".scopecat" / "daemon.log"
        if log.is_file():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 16_384))
                tail = stream.read().decode("utf-8", errors="replace")
            print(f"Daemon log tail ({log}):\n{tail}", file=sys.stderr)
        raise
    finally:
        stop_project(project)
    assert read_daemon_endpoint_record(project_root) is None
    _verify_changed_artifact(project)


def _shared_author_journey(project: Project, local_run_id: str) -> str:
    """Exercise installed discovery and retained local analysis in the same daemon."""
    with project.authoring() as author:
        shared = author.prepare(
            "shared_signal",
            inputs={"center": 0.0},
            scans={"position": [-1.0, 0.0, 1.0]},
        )
        run = shared.run().wait(timeout=120).result()
        report = author.analyze_as(run.id, "pilot_methods.signal:summarize", _Summary)
        assert report.value.mean == 2 / 3
        local = project.root / "src/scopecat_lab/authored/signal.py"
        local.write_text(
            local.read_text().replace(
                "mean=sum(values) / len(values)", "mean=1 + sum(values) / len(values)"
            )
        )
        author.refresh()
        original = author.analyze_as(
            local_run_id,
            "scopecat_lab.authored.signal:summarize",
            _Summary,
            source="original",
        )
        current = author.analyze_as(
            local_run_id,
            "scopecat_lab.authored.signal:summarize",
            _Summary,
            source="current",
        )
        assert original.value.mean == 2 / 3
        assert current.value.mean == 1 + 2 / 3
        print("installed/local methods and original/current analysis verified")
        return run.id


def _verify_changed_artifact(project: Project) -> None:
    from importlib.metadata import distribution

    from scopecat.project_sources import capture_sources, require_environment

    retained = capture_sources(project).manifest
    assert "scopecat-pilot-methods" in retained.packages
    source = Path(
        str(
            distribution("scopecat-pilot-methods").locate_file(
                "pilot_methods/signal.py"
            )
        )
    )
    original = source.read_bytes()
    try:
        source.write_bytes(original + b"\n# same-version rebuild\n")
        try:
            require_environment(retained)
        except ValueError as error:
            assert "package content changed" in str(error)  # noqa: PT017 - wheel environment has no pytest
        else:
            raise AssertionError("same-version installed source change was accepted")
    finally:
        source.write_bytes(original)
    require_environment(retained)
    print("installed artifact mismatch rejected and original bytes restored")


if __name__ == "__main__":
    main()
