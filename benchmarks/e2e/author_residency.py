"""Checkpoint residency of real author workers across source revision churn."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import psutil

from benchmarks.e2e.author_context import select_author_context
from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from scopecat.application.author_project import AuthorProject
from scopecat.project import load_project
from scopecat.records.author_revision import AuthorAnalysisReceipt, AuthorRevisionRef
from scopecat_server.lifecycle import start_project, stop_project  # noqa: TID251

_MODULE_ROLES = {
    "scopecat_server.launch_worker": "prepare",
    "scopecat_server.validation_worker": "prepare",
    "scopecat_server.retained_worker": "analysis",
}
_ANALYSIS = "ui_signal.analysis:estimate_peak"


def validate_workload(
    *, revisions: int, rounds: int, operations_per_revision: int
) -> None:
    if revisions < 3:
        raise ValueError("at least three revisions are required to exercise eviction")
    if rounds < 1 or operations_per_revision < 1:
        raise ValueError("rounds and operations-per-revision must be positive")


def measure(
    root: Path,
    *,
    revisions: int,
    rounds: int = 1,
    operations_per_revision: int = 1,
    run_per_visit: bool = False,
    concurrent_analysis: bool = False,
) -> dict[str, object]:
    validate_workload(
        revisions=revisions,
        rounds=rounds,
        operations_per_revision=operations_per_revision,
    )
    source = Path(__file__).resolve().parents[2] / "testing/fixtures/retained-signal"
    shutil.copytree(source / "src", root / "src")
    shutil.copy2(source / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    daemon = start_project(project)
    owner = psutil.Process(daemon.pid)
    observed: dict[tuple[int, float], psutil.Process] = {}
    samples: list[dict[str, object]] = []
    operations: list[dict[str, object]] = []

    round_index = 0
    revision_index = 0

    def timed[T](
        operation: str,
        call: Callable[[], T],
        *,
        phase: str = "initial",
        visit_phase: str = "initial",
        revision: AuthorRevisionRef | None = None,
        iteration: int = 0,
    ) -> T:
        started = time.perf_counter()
        result = call()
        operations.append(
            {
                "operation": operation,
                "seconds": time.perf_counter() - started,
                "round": round_index,
                "revision_index": revision_index,
                "iteration": iteration,
                "phase": phase,
                "visit_phase": visit_phase,
                "revision": revision.content_hash if revision else None,
                "analysis_id": result.analysis_id
                if isinstance(result, AuthorAnalysisReceipt)
                else None,
            }
        )
        return result

    def snapshot(operation: str) -> dict[str, tuple[tuple[int, float], ...]]:
        processes: list[dict[str, object]] = []
        pools: dict[str, list[tuple[int, float]]] = {"prepare": [], "analysis": []}
        vanished: list[int] = []
        for process in [owner, *owner.children(recursive=True)]:
            try:
                command = process.cmdline()
                module = next((part for part in command if part in _MODULE_ROLES), None)
                role = _MODULE_ROLES[module] if module else "other"
                revision = command[-1] if module else None
                observed[(process.pid, process.create_time())] = process
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
                    pools[role].append((process.pid, process.create_time()))
            except psutil.NoSuchProcess:
                vanished.append(process.pid)
        for role, pids in pools.items():
            if len(pids) > 2:
                raise AssertionError(f"{role} retained more than two workers: {pids}")
        samples.append(
            {
                "operation": operation,
                "operation_index": len(operations) - 1 if operations else None,
                "processes": processes,
                "vanished_during_sample": vanished,
                "pool_counts": {role: len(pids) for role, pids in pools.items()},
            }
        )
        return {role: tuple(sorted(pids)) for role, pids in pools.items()}

    try:
        snapshot("started")
        with AuthorProject(
            daemon.base_url, project_root=root, receipts=root / "receipts"
        ) as author:
            select_author_context(author)
            prepared = timed(
                "prepare_first",
                lambda: author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}),
            )
            original = prepared.preview.code_revision
            assert original is not None
            operations[-1]["revision"] = original.content_hash
            initial = snapshot("prepare_first")
            assert len(initial["prepare"]) == 1
            timed(
                "prepare_repeat",
                lambda: author.prepare("signal"),
                phase="reuse",
                revision=original,
                iteration=1,
            )
            repeated = snapshot("prepare_repeat")
            assert repeated["prepare"] == initial["prepare"]
            run = timed(
                "virtual_run", lambda: prepared.run().wait().result(), revision=original
            )
            operations[-1]["run_id"] = run.id
            snapshot("virtual_run")

            def analyze(
                revision: AuthorRevisionRef = original,
            ) -> AuthorAnalysisReceipt:
                return author.analyze(run.id, _ANALYSIS, code_revision=revision)

            timed("analysis_first", analyze, revision=original)
            operations[-1]["run_id"] = run.id
            initial = snapshot("analysis_first")
            assert len(initial["analysis"]) == 1
            timed(
                "analysis_repeat",
                analyze,
                phase="reuse",
                revision=original,
                iteration=1,
            )
            operations[-1]["run_id"] = run.id
            repeated = snapshot("analysis_repeat")
            assert repeated["analysis"] == initial["analysis"]

            def visit(revision: AuthorRevisionRef, phase: str, start: int = 0) -> None:
                nonlocal run
                previous = snapshot("visit_started")
                for iteration in range(start, operations_per_revision):
                    current_phase = phase if iteration == 0 else "reuse"
                    # A separate client owns the overlapping analysis request. The
                    # caller still owns selection, refresh and acquisition.
                    pair_started = time.perf_counter()
                    with ThreadPoolExecutor(max_workers=1) as executor:

                        def overlapping_analysis(
                            run_id: str = run.id,
                        ) -> AuthorAnalysisReceipt:
                            with AuthorProject(
                                daemon.base_url,
                                project_root=root,
                                receipts=root / "concurrent-receipts",
                            ) as reader:
                                return reader.analyze(
                                    run_id, _ANALYSIS, code_revision=revision
                                )

                        concurrent = (
                            executor.submit(overlapping_analysis)
                            if concurrent_analysis
                            else None
                        )
                        prepared = timed(
                            "prepare",
                            lambda: author.prepare(
                                "signal",
                                code_revision=revision,
                                scans={"frequency": [4.7, 4.8, 4.9]}
                                if run_per_visit
                                else None,
                            ),
                            phase=current_phase,
                            visit_phase=phase,
                            revision=revision,
                            iteration=iteration,
                        )
                        if concurrent is not None:
                            receipt = concurrent.result()
                            assert receipt.code_revision == revision
                            operations[-1]["overlap_batch_seconds"] = (
                                time.perf_counter() - pair_started
                            )
                            operations[-1]["overlapping_run_id"] = run.id
                            operations[-1]["overlapping_analysis_id"] = (
                                receipt.analysis_id
                            )
                    assert prepared.preview.code_revision == revision
                    after_prepare = snapshot("prepare")
                    if run_per_visit and iteration == start:
                        run = timed(
                            "virtual_run",
                            lambda prepared=prepared: prepared.run().wait().result(),
                            phase=current_phase,
                            visit_phase=phase,
                            revision=revision,
                            iteration=iteration,
                        )
                        operations[-1]["run_id"] = run.id
                        snapshot("virtual_run")
                    receipt = timed(
                        "analysis",
                        lambda: analyze(revision),
                        phase=current_phase,
                        visit_phase=phase,
                        revision=revision,
                        iteration=iteration,
                    )
                    assert receipt.code_revision == revision
                    operations[-1]["run_id"] = run.id
                    after_analysis = snapshot("analysis")
                    if current_phase == "reuse":
                        assert after_prepare["prepare"] == previous["prepare"]
                        assert after_analysis["analysis"] == previous["analysis"]
                    previous = after_analysis

            # Keep the historical first/repeat pair even in the default short case.
            visit(original, "initial", start=2)
            source_file = root / "src/ui_signal/ordinary.py"
            revision_hashes = [original.content_hash]
            for round_index in range(rounds):
                original_pools = snapshot("round_started")
                original_workers = {
                    role: {
                        (cast("int", process["pid"]), cast("float", process["created"]))
                        for process in cast(
                            "list[dict[str, object]]", samples[-1]["processes"]
                        )
                        if process["role"] == role
                        and process["revision"] == original.content_hash
                    }
                    for role in original_pools
                }
                for revision_index in range(1, revisions):
                    source_file.write_text(
                        source_file.read_text(encoding="utf-8")
                        + f"\n# residency round {round_index}"
                        + f" revision {revision_index}\n",
                        encoding="utf-8",
                    )
                    state = timed(
                        "refresh", author.refresh, phase="churn", visit_phase="churn"
                    )
                    current = state.active
                    assert (
                        current is not None
                        and current.content_hash not in revision_hashes
                    )
                    revision_hashes.append(current.content_hash)
                    operations[-1]["revision"] = current.content_hash
                    snapshot("refresh")
                    visit(current, "churn")
                revision_index = 0
                visit(original, "restore")
                restored = snapshot("original_restored")
                for role in original_pools:
                    assert original_workers[role]
                    assert not original_workers[role].intersection(restored[role])
    finally:
        started = time.perf_counter()
        stop_project(project)
        stop_seconds = time.perf_counter() - started
        # Observation grace only: never changes production shutdown or retries it.
        _, alive = psutil.wait_procs(list(observed.values()), timeout=5)
        survivors = [process.pid for process in alive]
        if survivors:
            raise AssertionError(
                f"Observed project processes survived stop: {survivors}"
            )
    return {
        **benchmark_record_header(
            case_id="author-residency", case_version=5, kind="e2e"
        ),
        "host": platform.platform(),
        "python": platform.python_version(),
        "revisions": revisions,
        "rounds": rounds,
        "operations_per_revision": operations_per_revision,
        "run_per_visit": run_per_visit,
        "concurrent_analysis": concurrent_analysis,
        "revision_hashes": revision_hashes,
        "run_id": run.id,
        "original_revision": original.content_hash,
        "operations": operations,
        "samples": samples,
        "stop_seconds": stop_seconds,
        "survivors": survivors,
        "scope": (
            "Virtual project; optional per-visit runs and overlapping "
            "analysis/prepare. "
            "Overlap is submitted, not proof of simultaneous worker execution. "
            "Settled checkpoints, not transient peaks or long-session leak proof. "
            "Per-process RSS includes shared pages; do not sum it as physical memory. "
            "Shutdown checks identities within a five-second observation grace."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revisions", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--operations-per-revision", type=int, default=1)
    parser.add_argument("--run-per-visit", action="store_true")
    parser.add_argument("--concurrent-analysis", action="store_true")
    args = parser.parse_args()
    revisions = cast("int", args.revisions)
    rounds = cast("int", args.rounds)
    operations_per_revision = cast("int", args.operations_per_revision)
    try:
        validate_workload(
            revisions=revisions,
            rounds=rounds,
            operations_per_revision=operations_per_revision,
        )
    except ValueError as error:
        parser.error(str(error))
    with tempfile.TemporaryDirectory(prefix="scopecat-residency-") as directory:
        print(
            BENCHMARK_RESULT_PREFIX
            + json.dumps(
                measure(
                    Path(directory),
                    revisions=revisions,
                    rounds=rounds,
                    operations_per_revision=operations_per_revision,
                    run_per_visit=cast("bool", args.run_per_visit),
                    concurrent_analysis=cast("bool", args.concurrent_analysis),
                )
            )
        )


if __name__ == "__main__":
    main()
