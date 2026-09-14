"""Checkpoint residency of real author workers across source revision churn."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import tempfile
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import cast

import psutil

from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from scopecat.application.author_project import AuthorProject
from scopecat.project import load_project
from scopecat.records.author_revision import AuthorAnalysisReceipt
from scopecat_server.lifecycle import start_project, stop_project  # noqa: TID251

_MODULE_ROLES = {
    "scopecat_server.launch_worker": "prepare",
    "scopecat_server.validation_worker": "prepare",
    "scopecat_server.retained_worker": "analysis",
}
_ANALYSIS = "reference_lab.workflows.authored.ordinary_analysis:estimate_peak"


def measure(root: Path, *, revisions: int) -> dict[str, object]:
    source = Path(__file__).resolve().parents[2] / "examples/reference_lab"
    for name in ("src", "config"):
        shutil.copytree(source / name, root / name)
    shutil.copy2(source / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    daemon = start_project(project)
    owner = psutil.Process(daemon.pid)
    observed: dict[int, psutil.Process] = {}
    samples: list[dict[str, object]] = []
    operations: list[dict[str, object]] = []

    def timed[T](operation: str, call: Callable[[], T]) -> T:
        started = time.perf_counter()
        result = call()
        operations.append(
            {"operation": operation, "seconds": time.perf_counter() - started}
        )
        return result

    def snapshot(operation: str) -> dict[str, tuple[int, ...]]:
        processes: list[dict[str, object]] = []
        pools: dict[str, list[int]] = {"prepare": [], "analysis": []}
        vanished: list[int] = []
        for process in [owner, *owner.children(recursive=True)]:
            try:
                command = process.cmdline()
                module = next((part for part in command if part in _MODULE_ROLES), None)
                role = _MODULE_ROLES[module] if module else "other"
                revision = command[-1] if module else None
                observed[process.pid] = process
                processes.append(
                    {
                        "pid": process.pid,
                        "created": process.create_time(),
                        "role": role,
                        "module": module,
                        "revision": revision,
                        "rss_bytes": cast("int", process.memory_info().rss),
                    }
                )
                if module:
                    pools[role].append(process.pid)
            except psutil.NoSuchProcess:
                vanished.append(process.pid)
        for role, pids in pools.items():
            if len(pids) > 2:
                raise AssertionError(f"{role} retained more than two workers: {pids}")
        samples.append(
            {
                "operation": operation,
                "processes": processes,
                "vanished_during_sample": vanished,
                "pool_counts": {role: len(pids) for role, pids in pools.items()},
            }
        )
        return {role: tuple(sorted(pids)) for role, pids in pools.items()}

    try:
        snapshot("started")
        with AuthorProject(daemon.base_url, receipts=root / "receipts") as author:
            prepared = timed(
                "prepare_first",
                lambda: author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}),
            )
            original = prepared.preview.code_revision
            assert original is not None
            initial = snapshot("prepare_first")
            assert len(initial["prepare"]) == 1
            timed("prepare_repeat", lambda: author.prepare("signal"))
            repeated = snapshot("prepare_repeat")
            assert repeated["prepare"] == initial["prepare"]
            run = timed("virtual_run", lambda: prepared.run().wait().result())
            snapshot("virtual_run")

            def analyze() -> AuthorAnalysisReceipt:
                return author.analyze(run.id, _ANALYSIS, code_revision=original)

            timed("analysis_first", analyze)
            initial = snapshot("analysis_first")
            assert len(initial["analysis"]) == 1
            timed("analysis_repeat", analyze)
            repeated = snapshot("analysis_repeat")
            assert repeated["analysis"] == initial["analysis"]
            original_pools = repeated
            source_file = root / "src/reference_lab/workflows/authored/signal.py"
            for index in range(1, revisions):
                source_file.write_text(
                    source_file.read_text(encoding="utf-8")
                    + f"\n# residency revision {index}\n",
                    encoding="utf-8",
                )
                state = timed(f"refresh_{index}", author.refresh)
                current = state.active
                assert current is not None and current != original
                snapshot(f"refresh_{index}")
                timed(f"prepare_{index}", lambda: author.prepare("signal"))
                timed(
                    f"analysis_{index}",
                    partial(author.analyze, run.id, _ANALYSIS, code_revision=current),
                )
                snapshot(f"revision_{index}")
            restored_prepare = timed(
                "prepare_original",
                lambda: author.prepare("signal", code_revision=original),
            )
            assert restored_prepare.preview.code_revision == original
            restored_analysis = timed("analysis_original", analyze)
            assert restored_analysis.code_revision == original
            restored = snapshot("original_restored")
            for role in original_pools:
                assert not set(original_pools[role]).intersection(restored[role])
    finally:
        started = time.perf_counter()
        stop_project(project)
        stop_seconds = time.perf_counter() - started
    # Observation grace only: never changes production shutdown or retries it.
    _, alive = psutil.wait_procs(list(observed.values()), timeout=5)
    survivors = [process.pid for process in alive]
    if survivors:
        raise AssertionError(f"Observed project processes survived stop: {survivors}")
    return {
        **benchmark_record_header(
            case_id="author-residency", case_version=1, kind="e2e"
        ),
        "host": platform.platform(),
        "python": platform.python_version(),
        "revisions": revisions,
        "run_id": run.id,
        "original_revision": original.content_hash,
        "operations": operations,
        "samples": samples,
        "stop_seconds": stop_seconds,
        "survivors": survivors,
        "scope": (
            "Serial virtual project; one retained run and published analyses. "
            "Settled checkpoints, not transient peaks or long-session leak proof. "
            "Per-process RSS includes shared pages; do not sum it as physical memory. "
            "Shutdown checks identities within a five-second observation grace."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revisions", type=int, default=3)
    args = parser.parse_args()
    revisions = cast("int", args.revisions)
    if revisions < 3:
        parser.error("at least three revisions are required to exercise eviction")
    with tempfile.TemporaryDirectory(prefix="scopecat-residency-") as directory:
        print(
            BENCHMARK_RESULT_PREFIX
            + json.dumps(measure(Path(directory), revisions=revisions))
        )


if __name__ == "__main__":
    main()
