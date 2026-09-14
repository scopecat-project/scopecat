"""Actual notebook prepare latency; only the copied virtual reference project."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import tempfile
import time
from pathlib import Path
from typing import cast, override

import httpx2

from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.preparation import AuthorPreparationFailed
from scopecat.project import load_project
from scopecat_server.lifecycle import (  # noqa: TID251 - deployed benchmark
    start_project,
    stop_project,
)


class TimingTransport(httpx2.HTTPTransport):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []

    @override
    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        started = time.perf_counter()
        response = super().handle_request(request)
        phases = response.headers.get("server-timing")
        if phases is not None:
            self.calls.append(
                {
                    "path": request.url.path,
                    "seconds": time.perf_counter() - started,
                    "server_timing": phases,
                }
            )
        return response


def measure(root: Path, *, repetitions: int) -> dict[str, object]:
    source = Path(__file__).resolve().parents[2] / "examples/reference_lab"
    for name in ("src", "config"):
        shutil.copytree(source / name, root / name)
    shutil.copy2(source / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    started = time.perf_counter()
    daemon = start_project(project)
    startup = time.perf_counter() - started
    samples: list[dict[str, object]] = []
    try:
        transport = TimingTransport()
        with AuthorProject(daemon.base_url, transport=transport) as author:
            for index in range(repetitions + 1):
                started = time.perf_counter()
                prepared = author.prepare("signal")
                samples.append(
                    {
                        "operation": "first" if index == 0 else "repeat",
                        "seconds": time.perf_counter() - started,
                        "points": prepared.preview.point_count,
                    }
                )
            started = time.perf_counter()
            prepared = author.prepare("signal", fixed={"gain": 2.0})
            samples.append(
                {
                    "operation": "edit_input",
                    "seconds": time.perf_counter() - started,
                    "points": prepared.preview.point_count,
                }
            )
            started = time.perf_counter()
            prepared = author.prepare("signal", scans={"frequency": (4.7, 4.8, 4.9)})
            samples.append(
                {
                    "operation": "edit_scan",
                    "seconds": time.perf_counter() - started,
                    "points": prepared.preview.point_count,
                }
            )
            source_file = root / "src/reference_lab/workflows/authored/signal.py"
            source_file.write_text(
                source_file.read_text(encoding="utf-8")
                + "\n# benchmark source revision\n",
                encoding="utf-8",
            )
            started = time.perf_counter()
            author.refresh()
            samples.append(
                {"operation": "refresh", "seconds": time.perf_counter() - started}
            )
            for operation in ("after_refresh", "repeat_after_refresh"):
                started = time.perf_counter()
                prepared = author.prepare("signal")
                samples.append(
                    {
                        "operation": operation,
                        "seconds": time.perf_counter() - started,
                        "points": prepared.preview.point_count,
                    }
                )
            started = time.perf_counter()
            author.refresh()
            samples.append(
                {
                    "operation": "unchanged_refresh",
                    "seconds": time.perf_counter() - started,
                }
            )
            started = time.perf_counter()
            author.prepare("signal")
            samples.append(
                {
                    "operation": "after_unchanged_refresh",
                    "seconds": time.perf_counter() - started,
                }
            )
            valid_source = source_file.read_text(encoding="utf-8")
            source_file.write_text(valid_source + "\ndef invalid(:\n", encoding="utf-8")
            started = time.perf_counter()
            try:
                author.refresh()
            except AuthorPreparationFailed as error:
                if error.operation.status != "failed":
                    raise
                samples.append(
                    {
                        "operation": "failed_refresh",
                        "seconds": time.perf_counter() - started,
                    }
                )
            else:
                raise AssertionError("invalid source refresh unexpectedly succeeded")
            finally:
                source_file.write_text(valid_source, encoding="utf-8")
            started = time.perf_counter()
            author.prepare("signal")
            samples.append(
                {
                    "operation": "after_failed_refresh",
                    "seconds": time.perf_counter() - started,
                }
            )
    finally:
        stop_project(project)
    return {
        **benchmark_record_header(case_id="author-prepare", case_version=2, kind="e2e"),
        "host": platform.platform(),
        "python": platform.python_version(),
        "daemon_start_seconds": startup,
        "samples": samples,
        "http_calls": transport.calls,
        "scope": "Copied virtual reference project; prepare only, no acquisition",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="scopecat-author-benchmark-") as directory:
        print(
            BENCHMARK_RESULT_PREFIX
            + json.dumps(
                measure(Path(directory), repetitions=cast("int", args.repetitions))
            )
        )


if __name__ == "__main__":
    main()
