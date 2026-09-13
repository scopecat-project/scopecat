"""Ordinary author submission to measured, ingested and client-visible data."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import cast

from benchmarks.e2e.author_prepare import TimingTransport
from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from scopecat.application.author_project import AuthorProject
from scopecat.kernel.interaction_timing import TIMING_DIRECTORY_ENV, record_timing
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project  # noqa: TID251


def observe_result(url: str, receipt: Path, stop: Event) -> dict[str, object]:
    # Independent normal client: never use trace files to discover a run early.
    with AuthorProject(url) as author:
        job = author.reopen(receipt)
        deadline = time.monotonic() + 60
        polls = 0
        while not stop.is_set():
            polls += 1
            try:
                run = job.result()
            except KeyError, RuntimeError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "No retained step result within observer budget"
                    ) from None
                stop.wait(0.2)
                continue
            values = run.measurements()["result"].require_values()
            record_timing("client_result_visible", run_id=run.id)
            return {"run_id": run.id, "observer_polls": polls, "values": len(values)}
        raise RuntimeError("Result observer stopped")


def measure(root: Path, *, repetitions: int) -> dict[str, object]:
    source = Path(__file__).resolve().parents[2] / "examples/reference_lab"
    for name in ("src", "config"):
        shutil.copytree(source / name, root / name)
    shutil.copy2(source / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    trace = root / "timing"
    trace.mkdir()
    previous = os.environ.get(TIMING_DIRECTORY_ENV)
    os.environ[TIMING_DIRECTORY_ENV] = str(trace)
    transport = TimingTransport()
    samples: list[dict[str, object]] = []
    started = time.monotonic_ns()
    try:
        daemon = start_project(project)
        startup = (time.monotonic_ns() - started) / 1e9
        try:
            with (
                AuthorProject(
                    daemon.base_url, receipts=root / "receipts", transport=transport
                ) as author,
                ThreadPoolExecutor(max_workers=1) as observer,
            ):
                for operation in [
                    "first",
                    *(["repeat"] * repetitions),
                    "edit_input",
                    "after_refresh",
                ]:
                    if operation == "after_refresh":
                        path = root / "src/reference_lab/workflows/authored/signal.py"
                        path.write_text(
                            path.read_text(encoding="utf-8")
                            + "\n# benchmark refresh\n",
                            encoding="utf-8",
                        )
                        author.refresh()
                    prepare_start = time.monotonic_ns()
                    prepared = author.prepare(
                        "signal",
                        fixed={"gain": 2.0 if operation == "edit_input" else 1.0},
                        scans={"frequency": [4.7, 4.8, 4.9]},
                    )
                    submit_start = time.monotonic_ns()
                    job = prepared.run()
                    acknowledged = time.monotonic_ns()
                    stop = Event()
                    future = observer.submit(
                        observe_result, daemon.base_url, job.receipt, stop
                    )
                    try:
                        job.wait()
                        waited = time.monotonic_ns()
                        result = future.result(timeout=60)
                    finally:
                        stop.set()
                    samples.append(
                        {
                            "operation": operation,
                            "procedure_id": job.id,
                            **result,
                            "revision": prepared.preview.code_revision.content_hash
                            if prepared.preview.code_revision
                            else None,
                            "submit_start_ns": submit_start,
                            "prepare_seconds": (submit_start - prepare_start) / 1e9,
                            "acknowledgement_seconds": (acknowledged - submit_start)
                            / 1e9,
                            "wait_return_seconds": (waited - submit_start) / 1e9,
                        }
                    )
        finally:
            stop_project(project)
    finally:
        if previous is None:
            os.environ.pop(TIMING_DIRECTORY_ENV, None)
        else:
            os.environ[TIMING_DIRECTORY_ENV] = previous
    events: list[dict[str, object]] = []
    for path in trace.glob("timing-*.jsonl"):
        events.extend(
            cast("dict[str, object]", json.loads(line))
            for line in path.read_text().splitlines()
        )
    for sample in samples:
        selected = [
            event
            for event in events
            if event.get("procedure_id") == sample["procedure_id"]
            or event.get("run_id") == sample["run_id"]
        ]
        selected.sort(key=lambda event: cast("int", event["monotonic_ns"]))
        required = {
            "procedure_dispatch",
            "procedure_worker_entry",
            "framework_ready",
            "source_restore_start",
            "application_load_start",
            "application_ready",
            "run_admitted",
            "first_measurement_ready",
            "first_measurement_ingested",
            "run_terminal_committed",
            "client_result_visible",
        }
        missing = required - {event["phase"] for event in selected}
        if missing:
            raise AssertionError(f"Incomplete interaction trace: {sorted(missing)}")
        sample["events"] = [
            {
                **event,
                "since_submit_seconds": (
                    cast("int", event["monotonic_ns"])
                    - cast("int", sample["submit_start_ns"])
                )
                / 1e9,
            }
            for event in selected
        ]
    return {
        **benchmark_record_header(
            case_id="author-first-data", case_version=1, kind="e2e"
        ),
        "host": platform.platform(),
        "python": platform.python_version(),
        "daemon_start_seconds": startup,
        "samples": samples,
        "http_calls": transport.calls,
        "scope": (
            "Copied virtual signal, three computed points; "
            "normal admission/publication. "
            "Independent 0.2s retained-result observer; "
            "no physical devices or GUI rendering."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="scopecat-first-data-") as directory:
        print(
            BENCHMARK_RESULT_PREFIX
            + json.dumps(
                measure(Path(directory), repetitions=cast("int", args.repetitions))
            )
        )


if __name__ == "__main__":
    main()
