"""Measure a ready-to-run packaged Python without installing a service environment.

Uses a fresh, explicit experiment directory. Does not open windows, connect devices,
run private initialization, or use the daily application. Timings include imports;
RSS is per-process resident memory, not unique physical memory consumption.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol, TypedDict, cast


class Sample(TypedDict):
    ready_seconds: float
    server_rss_bytes: int
    controller_rss_bytes: int
    python: str
    empty_path: bool
    stop_seconds: float


class Arguments(Protocol):
    app: Path | None
    output: Path | None
    repeats: int
    worker: Path | None
    gui: Path | None


def worker(root: Path, gui: Path) -> None:
    started = time.perf_counter()
    import httpx2
    import psutil

    from scopecat.project import open_project
    from scopecat_server.lifecycle import (  # noqa: TID251 - server lifecycle qualification
        inspect_daemon,
        start_project,
        stop_project,
    )

    root.mkdir(exist_ok=True)
    manifest = root / "scopecat.toml"
    if not manifest.exists():
        _ = manifest.write_text("[lab]\n[authors]\ndependencies = []\n")
    project = open_project(root)
    try:
        record = start_project(project, static_dir=gui, timeout=60)
        with httpx2.Client(trust_env=False, timeout=10) as http:
            health = http.get(record.base_url + "/api/v1/health")
            health.raise_for_status()
            assert health.json()["status"] == "ok"
            assert http.get(record.base_url + "/").status_code == 200
        process = psutil.Process(record.pid)
        result: Sample = {
            "ready_seconds": time.perf_counter() - started,
            "server_rss_bytes": cast("int", process.memory_info().rss),
            "controller_rss_bytes": cast("int", psutil.Process().memory_info().rss),
            "python": sys.executable,
            "empty_path": os.environ["PATH"] == "",
            "stop_seconds": 0,
        }
    finally:
        before_stop = time.perf_counter()
        _ = stop_project(project)
        assert inspect_daemon(project).state == "stopped"
    result["stop_seconds"] = time.perf_counter() - before_stop
    print(json.dumps(result))


def measure(app: Path, destination: Path, repeats: int) -> None:
    from lab_tools.bundle import inventory

    app = app.resolve()
    destination = destination.resolve()
    resources = app / (
        "Contents/Resources" if sys.platform == "darwin" else "resources"
    )
    python = (
        resources
        / "python"
        / ("python.exe" if sys.platform == "win32" else "bin/python3")
    )
    if not python.is_file():
        raise FileNotFoundError(python)
    before = inventory(app, (".",))
    destination.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ, PATH="", PYTHONDONTWRITEBYTECODE="1")
    for name in ("PYTHONHOME", "PYTHONPATH", "SCOPECAT_DAEMON_URL"):
        environment.pop(name, None)
    samples: list[Sample] = []
    for _ in range(repeats):
        completed = subprocess.run(  # noqa: S603 - explicit packaged runtime
            [
                str(python),
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--worker",
                str(destination / "workspace"),
                "--gui",
                str(resources / "payload/gui"),
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        samples.append(cast("Sample", json.loads(completed.stdout)))
    assert inventory(app, (".",)) == before, "Packaged runtime changed during use"
    report = {
        "app": str(app),
        "platform": sys.platform,
        "samples": samples,
        "median_ready_seconds": statistics.median(
            sample["ready_seconds"] for sample in samples
        ),
        "notes": [
            "First sample creates an empty data store; later samples reopen it.",
            "OS disk cache is not flushed; these are not cold-machine timings.",
            "Includes a Python lifecycle controller; not a minimal Rust-host RSS.",
            "No package installation or application initializer is invoked.",
        ],
    }
    path = destination / "report.json"
    _ = path.write_text(json.dumps(report, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--gui", type=Path, help=argparse.SUPPRESS)
    args = cast("Arguments", cast("object", parser.parse_args()))
    if args.worker:
        if args.gui is None:
            parser.error("--worker requires --gui")
        worker(args.worker, args.gui)
    else:
        if args.app is None or args.output is None or args.repeats < 1:
            parser.error("provide --app, a fresh --output and positive --repeats")
        measure(args.app, args.output, args.repeats)
