"""Measure bounded daemon reads from a long-lived local project."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.runs.refs import SCIENTIFIC_BINDING_REF
from scopecat_server.runtime import LocalDaemonRuntime  # noqa: TID251
from scopecat_server.storage.sqlite.object_store import (  # noqa: TID251
    ImmutableObjectStore,
)
from scopecat_server.storage.sqlite.resource_objects import (  # noqa: TID251
    resource_directory,
)

_TIMESTAMP = "2026-08-18T00:00:00+00:00"
_HASH = "0" * 64


def _options() -> tuple[int, int, int, int]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10_000)
    parser.add_argument("--project-analyses", type=int, default=1_000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=5)
    options = parser.parse_args()
    run_count = cast("int", options.runs)
    analysis_count = cast("int", options.project_analyses)
    page_size = cast("int", options.page_size)
    repetitions = cast("int", options.repetitions)
    if run_count < page_size:
        raise ValueError("run count must be at least one page")
    if analysis_count < page_size:
        raise ValueError("project analysis count must be at least one page")
    if not 1 <= page_size <= 500:
        raise ValueError("page size must be between one and 500")
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    return run_count, analysis_count, page_size, repetitions


def _benchmark(
    *,
    run_count: int,
    analysis_count: int,
    page_size: int,
    repetitions: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="scopecat-history-benchmark-") as root:
        project_root = Path(root)
        with LocalDaemonRuntime(project_root) as runtime:
            database = project_root / ".scopecat" / "control.sqlite3"
            setup_started = time.perf_counter()
            _seed_project(
                database,
                run_count=run_count,
                analysis_count=analysis_count,
            )
            setup_seconds = time.perf_counter() - setup_started

            with TestClient(runtime.app()) as client:
                newest_runs = f"/api/v1/runs?limit={page_size}"
                oldest_runs = f"/api/v1/runs?limit={page_size}&before={page_size + 1}"
                exact_run = f"/api/v1/runs/{_run_id(run_count // 2)}"
                newest_analyses = f"/api/v1/analyses?limit={page_size}"
                oldest_analyses = (
                    f"/api/v1/analyses?limit={page_size}&before={page_size + 1}"
                )

                measurements = {
                    "newest_run_page": _measure_get(
                        client, newest_runs, repetitions=repetitions
                    ),
                    "oldest_run_page": _measure_get(
                        client, oldest_runs, repetitions=repetitions
                    ),
                    "exact_middle_run": _measure_get(
                        client, exact_run, repetitions=repetitions
                    ),
                    "newest_analysis_page": _measure_get(
                        client, newest_analyses, repetitions=repetitions
                    ),
                    "oldest_analysis_page": _measure_get(
                        client, oldest_analyses, repetitions=repetitions
                    ),
                }

            database_bytes = database.stat().st_size

    return {
        **benchmark_record_header(
            case_id="historical-project",
            case_version=1,
            kind="component",
        ),
        "run_count": run_count,
        "project_analysis_count": analysis_count,
        "page_size": page_size,
        "repetitions": repetitions,
        "setup_seconds": setup_seconds,
        "database_bytes": database_bytes,
        "measurements": measurements,
    }


def _measure_get(
    client: TestClient,
    path: str,
    *,
    repetitions: int,
) -> dict[str, object]:
    warm = client.get(path)
    warm.raise_for_status()
    payload = cast("dict[str, object]", warm.json())
    samples: list[float] = []
    response_bytes = len(warm.content)
    for _ in range(repetitions):
        started = time.perf_counter()
        response = client.get(path)
        samples.append(time.perf_counter() - started)
        response.raise_for_status()
        if len(response.content) != response_bytes:
            raise RuntimeError(f"historical project response changed: {path}")
    result: dict[str, object] = {
        "median_seconds": statistics.median(samples),
        "minimum_seconds": min(samples),
        "maximum_seconds": max(samples),
        "response_bytes": response_bytes,
    }
    if isinstance(items := payload.get("items"), list):
        result["returned_count"] = len(cast("list[object]", items))
        result["next_cursor"] = payload.get("next_cursor")
    else:
        control = cast("dict[str, object]", payload["control"])
        admission = cast("dict[str, object]", control["admission"])
        result["run_id"] = admission["run_id"]
    return result


def _seed_project(
    database: Path,
    *,
    run_count: int,
    analysis_count: int,
) -> None:
    objects = ImmutableObjectStore(database.parent / "objects")
    binding = (
        ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash=f"sha256:{_HASH}",
            setup_content_hash=f"sha256:{_HASH}",
        )
        .model_dump_json()
        .encode()
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for start in range(1, run_count + 1, 1_000):
            ordinals = range(start, min(start + 1_000, run_count + 1))
            rows = tuple(_run_seed(ordinal) for ordinal in ordinals)
            connection.executemany(
                """
                INSERT INTO scheduler_runs(
                    submission_id, run_id, state, updated_at, admission_json
                ) VALUES (?, ?, 'closed', ?, ?)
                """,
                (
                    (submission_id, run_id, _TIMESTAMP, admission_json)
                    for submission_id, run_id, admission_json, _ in rows
                ),
            )
            connection.executemany(
                """
                INSERT INTO runs(
                    run_id, created_at, config_content_hash, config_source_json
                ) VALUES (?, ?, ?, NULL)
                """,
                ((run_id, _TIMESTAMP, f"sha256:{_HASH}") for _, run_id, _, _ in rows),
            )
            for _, run_id, _, _ in rows:
                stored = ImmutableObjectStore(
                    resource_directory(objects, "run", run_id)
                ).put(binding)
                connection.execute(
                    "INSERT INTO run_repository_refs(run_id, ref, digest) "
                    "VALUES (?, ?, ?)",
                    (run_id, SCIENTIFIC_BINDING_REF, stored.digest),
                )
            connection.executemany(
                """
                INSERT INTO run_outcomes(
                    run_id, result, certainty, finished_at, outcome_json
                ) VALUES (?, 'succeeded', 'known', ?, ?)
                """,
                (
                    (run_id, _TIMESTAMP, outcome_json)
                    for _, run_id, _, outcome_json in rows
                ),
            )
            connection.executemany(
                """
                INSERT INTO execution_coverage(run_id, completed_point_count)
                VALUES (?, 1)
                """,
                ((run_id,) for _, run_id, _, _ in rows),
            )
            connection.executemany(
                """
                INSERT INTO execution_point_plans(
                    run_id, initialize_operation_id, initial_point_count,
                    accepted_point_count, point_limit, plan_closed,
                    stop_operation_id, stop_reason
                ) VALUES (?, 'admission', 1, 1, 1, 1, 'admission.static',
                          'static point plan')
                """,
                ((run_id,) for _, run_id, _, _ in rows),
            )

        connection.executemany(
            """
            INSERT INTO analysis_publications(
                subject_kind, run_id, subject_json, record_id, record_entry_json,
                analysis_key, revision, publication_hash, published_at, title,
                step_id, input_count, output_count
            ) VALUES (
                'project', NULL, '{"kind":"project"}', ?, ?, ?, 1, ?, ?, ?,
                NULL, 2, 1
            )
            """,
            (_analysis_seed(ordinal) for ordinal in range(1, analysis_count + 1)),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _run_seed(ordinal: int) -> tuple[str, str, str, str]:
    run_id = _run_id(ordinal)
    submission_id = f"submission-{ordinal:08d}"
    admission: dict[str, object] = {
        "submission_id": submission_id,
        "submission_content_hash": _HASH,
        "run_id": run_id,
        "plan": {
            "experiment_id": "historical-scan",
            "experiment_kind": "benchmark",
            "point_plan_fingerprint": _HASH,
            "measurement_contract_fingerprint": _HASH,
            "point_count": 1,
            "initial_point_count": 1,
            "point_limit": 1,
        },
        "display_name": f"Historical run {ordinal}",
        "resource_claims": [],
        "admitted_at": _TIMESTAMP,
    }
    outcome: dict[str, object] = {
        "run_id": run_id,
        "result": "succeeded",
        "certainty": "known",
        "finished_at": _TIMESTAMP,
        "problems": [],
    }
    return (
        submission_id,
        run_id,
        json.dumps(admission, separators=(",", ":")),
        json.dumps(outcome, separators=(",", ":")),
    )


def _analysis_seed(ordinal: int) -> tuple[str, str, str, str, str, str]:
    record_id = f"analysis-history-{ordinal:08d}-r1"
    entry: dict[str, object] = {
        "role": "record",
        "id": record_id,
        "kind": "analysis",
        "title": f"Historical comparison {ordinal}",
        "media_type": "application/json",
        "content_hash": f"sha256:{ordinal:064x}",
        "metadata": {},
    }
    return (
        record_id,
        json.dumps(entry, separators=(",", ":")),
        f"historical-comparison-{ordinal:08d}",
        f"sha256:{ordinal:064x}",
        _TIMESTAMP,
        f"Historical comparison {ordinal}",
    )


def _run_id(ordinal: int) -> str:
    return f"00000000-0000-7000-8000-{ordinal:012x}"


def main() -> None:
    run_count, analysis_count, page_size, repetitions = _options()
    print(
        BENCHMARK_RESULT_PREFIX
        + json.dumps(
            _benchmark(
                run_count=run_count,
                analysis_count=analysis_count,
                page_size=page_size,
                repetitions=repetitions,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
