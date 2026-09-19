"""Retained analysis and comparison on copied virtual reference data."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import httpx2

from benchmarks.e2e.author_prepare import TimingTransport
from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from reference_lab.workflows.authored.ordinary_analysis import PeakResult
from scopecat.api.run import RunHandle
from scopecat.application.author_project import AuthorProject
from scopecat.kernel.quantity import Quantity
from scopecat.project import load_project
from scopecat.records.comparison import (
    ComparisonInspection,
    ComparisonRequest,
    ComparisonSelection,
)
from scopecat_server.lifecycle import (  # noqa: TID251 - deployed benchmark
    start_project,
    stop_project,
)


def measure(root: Path, *, repetitions: int) -> dict[str, object]:
    source = Path(__file__).resolve().parents[2] / "examples/reference_lab"
    for name in ("src", "config"):
        shutil.copytree(source / name, root / name)
    shutil.copy2(source / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    daemon = start_project(project)
    transport = TimingTransport()
    comparison_transport = TimingTransport()
    samples: list[dict[str, object]] = []

    def timed(name: str, call: Callable[[], object]) -> object:
        start = time.perf_counter()
        result = call()
        samples.append({"operation": name, "seconds": time.perf_counter() - start})
        return result

    try:
        with (
            AuthorProject(
                daemon.base_url,
                receipts=root / ".scopecat/benchmark-receipts",
                transport=transport,
            ) as author,
            httpx2.Client(
                base_url=daemon.base_url, timeout=60, transport=comparison_transport
            ) as http,
        ):
            start = time.perf_counter()
            signal = (
                author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]})
                .run()
                .wait()
                .result()
            )
            runs: list[RunHandle] = []
            for amplitude in (0.05, 0.08):
                runs.append(
                    author.prepare(
                        "reference_lab.frequency_amplitude",
                        scans={
                            "frequency": [
                                Quantity(value, "GHz")
                                for value in (4.6, 4.7, 4.8, 4.9, 5.0)
                            ]
                        },
                        fixed={"amplitude": Quantity(amplitude, "V")},
                    )
                    .run()
                    .wait()
                    .result(step="signal")
                )
            setup_seconds = time.perf_counter() - start
            name = "reference_lab.workflows.authored.ordinary_analysis:estimate_peak"
            for i in range(repetitions + 1):
                timed(
                    "analysis_first" if i == 0 else "analysis_repeat",
                    lambda: author.analyze_as(signal.id, name, PeakResult),
                )
            timed(
                "analysis_edit",
                lambda: author.analyze_as(
                    signal.id, name, PeakResult, arguments={"minimum_contrast": 2.0}
                ),
            )
            base = ComparisonRequest(
                action="inspect",
                model_id="signal-quadratic",
                model_version="1",
                primary_run=runs[0].id,
                secondary_run=runs[1].id,
            )

            def compare(command: ComparisonRequest) -> httpx2.Response:
                response = http.post(
                    "/api/v1/run-comparison", json=command.model_dump(mode="json")
                )
                response.raise_for_status()
                return response

            response = cast(
                "httpx2.Response",
                timed("comparison_inspect_first", lambda: compare(base)),
            )
            inspected = ComparisonInspection.model_validate_json(response.text)
            command = base.model_copy(
                update={
                    "action": "fit",
                    "code_revision": inspected.code_revision,
                    "primary": ComparisonSelection(
                        run_id=runs[0].id,
                        content_hash=inspected.primary.content_hash,
                        points=(0, 1, 2, 3, 4),
                    ),
                    "secondary": ComparisonSelection(
                        run_id=runs[1].id,
                        content_hash=inspected.secondary.content_hash,
                        points=(0, 1, 2, 3, 4),
                    ),
                }
            )
            for i in range(repetitions + 1):
                timed(
                    "comparison_fit_first" if i == 0 else "comparison_fit_repeat",
                    lambda: compare(command),
                )
            timed(
                "comparison_edit",
                lambda: compare(
                    command.model_copy(update={"parameters": {"offset_ghz": 0.01}})
                ),
            )
    finally:
        stop_project(project)
    return {
        **benchmark_record_header(
            case_id="author-analysis", case_version=1, kind="e2e"
        ),
        "host": platform.platform(),
        "python": platform.python_version(),
        "setup_seconds": setup_seconds,
        "samples": samples,
        "http_calls": transport.calls,
        "comparison_http_calls": comparison_transport.calls,
        "scope": (
            "Three virtual input runs outside timing; every analysis/fit executes "
            "and publishes normally; no physical devices"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="scopecat-analysis-benchmark-"
    ) as directory:
        print(
            BENCHMARK_RESULT_PREFIX
            + json.dumps(
                measure(Path(directory), repetitions=cast("int", args.repetitions))
            )
        )


if __name__ == "__main__":
    main()
