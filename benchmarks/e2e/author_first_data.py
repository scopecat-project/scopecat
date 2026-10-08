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

from benchmarks.e2e.author_context import select_reference_context
from benchmarks.e2e.author_prepare import TimingTransport
from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from scopecat.application.author_project import AuthorProject
from scopecat.kernel.interaction_timing import TIMING_DIRECTORY_ENV, record_timing
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project  # noqa: TID251


def observe_result(
    url: str, receipt: Path, stop: Event, workspace_id: str
) -> dict[str, object]:
    # Independent normal client: never use trace files to discover a run early.
    with AuthorProject(url, workspace_id=workspace_id) as author:
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
                    daemon.base_url,
                    project_root=root,
                    receipts=root / "receipts",
                    transport=transport,
                ) as author,
                ThreadPoolExecutor(max_workers=1) as observer,
            ):
                select_reference_context(author)
                original_revision = None
                for operation in [
                    "first",
                    *(["repeat"] * repetitions),
                    "edit_input",
                    "edit_scan",
                    "after_source_edit",
                    "after_refresh",
                ]:
                    if operation == "after_source_edit":
                        path = root / "src/reference_lab_authors/authored/signal.py"
                        path.write_text(
                            path.read_text(encoding="utf-8")
                            + "\n# benchmark refresh\n",
                            encoding="utf-8",
                        )
                    interaction_start = time.monotonic_ns()
                    refresh_seconds: float | None = None
                    if operation == "after_refresh":
                        refresh_start = time.monotonic_ns()
                        author.refresh()
                        refresh_seconds = (time.monotonic_ns() - refresh_start) / 1e9
                    prepare_start = time.monotonic_ns()
                    prepared = author.prepare(
                        "signal",
                        fixed={"gain": 2.0 if operation == "edit_input" else 1.0},
                        scans={
                            "frequency": [4.6, 4.8, 5.0]
                            if operation == "edit_scan"
                            else [4.7, 4.8, 4.9]
                        },
                    )
                    revision = prepared.preview.code_revision
                    assert revision is not None
                    if original_revision is None:
                        original_revision = revision
                    if (revision != original_revision) != (
                        operation == "after_refresh"
                    ):
                        raise AssertionError(
                            "Only explicit refresh may adopt edited source"
                        )
                    submit_start = time.monotonic_ns()
                    job = prepared.run()
                    acknowledged = time.monotonic_ns()
                    stop = Event()
                    future = observer.submit(
                        observe_result,
                        daemon.base_url,
                        job.receipt,
                        stop,
                        author.workspace_id,
                    )
                    try:
                        job.wait()
                        waited = time.monotonic_ns()
                        result = future.result(timeout=60)
                    finally:
                        stop.set()
                    analysis_start = time.monotonic_ns()
                    analysis = author.analyze(
                        str(result["run_id"]),
                        "reference_lab_authors.authored.ordinary_analysis:estimate_peak",
                        code_revision=revision,
                    )
                    analyzed = time.monotonic_ns()
                    publication = author.run(str(result["run_id"])).published_analysis(
                        analysis.analysis_id
                    )
                    if publication.id != analysis.analysis_id:
                        raise AssertionError("Analysis read did not retain its receipt")
                    if (
                        publication.fact("author_code_revision").value
                        != revision.content_hash
                    ):
                        raise AssertionError(
                            "Published analysis changed source revision"
                        )
                    analysis_visible = time.monotonic_ns()
                    reopened = author.reopen(job.receipt).result()
                    values = reopened.measurements()["result"].require_values()
                    if reopened.id != result["run_id"] or len(values) != 3:
                        raise AssertionError("Receipt did not reopen the original data")
                    if analysis.code_revision != prepared.preview.code_revision:
                        raise AssertionError(
                            "Analysis lost the prepared source revision"
                        )
                    read = time.monotonic_ns()
                    samples.append(
                        {
                            "operation": operation,
                            "procedure_id": job.id,
                            **result,
                            "revision": prepared.preview.code_revision.content_hash
                            if prepared.preview.code_revision
                            else None,
                            "submit_start_ns": submit_start,
                            "interaction_start_ns": interaction_start,
                            "prepare_start_ns": prepare_start,
                            "acknowledged_ns": acknowledged,
                            "wait_return_ns": waited,
                            "analysis_start_ns": analysis_start,
                            "analysis_return_ns": analyzed,
                            "analysis_visible_ns": analysis_visible,
                            "reopen_read_ns": read,
                            "analysis_id": analysis.analysis_id,
                            "prepare_seconds": (submit_start - prepare_start) / 1e9,
                            "acknowledgement_seconds": (acknowledged - submit_start)
                            / 1e9,
                            "wait_return_seconds": (waited - submit_start) / 1e9,
                            "analysis_seconds": (analyzed - analysis_start) / 1e9,
                            "analysis_read_seconds": (analysis_visible - analyzed)
                            / 1e9,
                            "interaction_to_analysis_visible_seconds": (
                                analysis_visible - interaction_start
                            )
                            / 1e9,
                            "reopen_read_seconds": (read - analysis_visible) / 1e9,
                            "refresh_seconds": refresh_seconds,
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
    correlate_events(samples, events)
    return {
        **benchmark_record_header(
            case_id="author-first-data", case_version=3, kind="e2e"
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
            "analysis visibility is a subsequent ordinary receipt read; "
            "interaction begins at the API call, including explicit refresh; "
            "no physical devices, click dispatch or GUI rendering."
        ),
    }


def correlate_events(
    samples: list[dict[str, object]], events: list[dict[str, object]]
) -> None:
    """Keep raw clocks and both API/submission origins for matching identities."""
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
            "procedure_python_entry",
            "procedure_registered",
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
                "since_interaction_seconds": (
                    cast("int", event["monotonic_ns"])
                    - cast("int", sample["interaction_start_ns"])
                )
                / 1e9,
            }
            for event in selected
        ]


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
