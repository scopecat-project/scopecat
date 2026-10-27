# pyright: reportPrivateUsage=false
# This component benchmark deliberately instruments the production execution ports.
"""Measure synthetic safe-prefix publication costs through the production ports.

The controller runs this fixed harness with the target checkout's interpreter.
No execution module substitution or persistence-policy override is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from unittest.mock import patch

from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header

_HARNESS_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *arguments: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required")
    return subprocess.run(  # noqa: S603
        [git, "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _identity(repo: Path) -> dict[str, object]:
    return {
        "sha": _git(repo, "rev-parse", "HEAD"),
        "dirty": bool(_git(repo, "status", "--porcelain")),
        "uv_lock_sha256": hashlib.sha256((repo / "uv.lock").read_bytes()).hexdigest(),
    }


def _options(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-repo", type=Path, required=True)
    parser.add_argument("--target-python", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        required=True,
        help="New directory only; retained on failure, removed on success.",
    )
    parser.add_argument("--points", type=int, default=1024)
    parser.add_argument("--label", default="sample")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if not 1 <= cast("int", options.points) <= 4096:
        parser.error("--points must be between 1 and 4096")
    return options


def _check_target(repo: Path) -> None:
    import scopecat.daemon.execution as execution
    import scopecat_server.runtime as runtime  # noqa: TID251

    if sys.version_info[:3] != (3, 15, 1):
        raise RuntimeError("Python 3.15.1 is required")
    for module, package in ((execution, "scopecat"), (runtime, "scopecat-server")):
        source = Path(cast("str", module.__file__)).resolve()
        if not source.is_relative_to(repo / "packages" / package / "src"):
            raise RuntimeError(f"{package} must be installed from the target checkout")


def _measure(root: Path, points: int) -> dict[str, object]:
    from scopecat.config.documents import load_config_snapshot_document
    from scopecat.config.scientific_binding import bind_scientific_evidence
    from scopecat.control.models import RunPlanSummary, RunResourceRequirement
    from scopecat.daemon.client import DaemonClient
    from scopecat.daemon.execution import (
        _DaemonMeasurementRepository,
        _DaemonRunCoverage,
        _DaemonRunRecoveryGroups,
        _LeaseAuthority,
    )
    from scopecat.daemon.wire import MeasurementIngestReceipt, RunSubmission
    from scopecat.measurements.recording_arrow import encode_measurement_append
    from scopecat.records.measurement import (
        MeasurementDatasetSchema,
        MeasurementDimension,
        MeasurementPointDomainAxis,
        MeasurementPointDomainValuesSource,
        MeasurementProductGridPointDomain,
        MeasurementRecord,
        MeasurementScalar,
        MeasurementVariable,
    )
    from scopecat.records.measurement_recording import (
        MeasurementDatasetAppend,
        MeasurementDatasetBatch,
        MeasurementDatasetHeader,
    )
    from scopecat.records.run_request import RunRequest
    from scopecat_server.runtime import LocalDaemonRuntime  # noqa: TID251
    from scopecat_server.storage.sqlite.connection import SQLiteDatabase  # noqa: TID251
    from scopecat_testkit.server.instruments import signal_endpoint  # noqa: TID251

    config = load_config_snapshot_document(
        _HARNESS_ROOT / "fixtures/core/simple_scan/config-snapshot.json"
    )
    transaction_count = 0
    original_write = SQLiteDatabase.write_transaction

    @contextmanager
    def count_writes(database: SQLiteDatabase) -> Generator[sqlite3.Connection]:
        nonlocal transaction_count
        with original_write(database) as connection:
            yield connection
        transaction_count += 1  # Count only successful commits, including heartbeat.

    with (
        patch.object(SQLiteDatabase, "write_transaction", count_writes),
        LocalDaemonRuntime(
            root, bootstrap_config=config, instrument_endpoint=signal_endpoint()
        ) as runtime,
    ):
        executor = runtime.application.executor
        submission = RunSubmission(
            execution_setup=runtime.application.setup.resolve("initial").ref,
            scientific_binding=bind_scientific_evidence(
                catalog_id="benchmark",
                config=config,
                samples=(),
                sample_revisions={},
            ),
            submission_id="safe-prefix",
            config=config,
            request=RunRequest(experiment_id="scratch"),
            plan=RunPlanSummary(
                experiment_id="scratch",
                experiment_kind="scratch",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=points,
                initial_point_count=points,
                point_limit=points,
                run_resource_requirements=(
                    RunResourceRequirement(id="source-0", kind="instrument"),
                ),
            ),
        )
        run_id = runtime.application.submit_run(submission).run_id

        class Client:
            def __getattr__(self, name: str) -> object:
                return cast("object", getattr(executor, name))

            def ingest_measurements(
                self,
                run_id: str,
                *,
                lease_id: str,
                append: MeasurementDatasetAppend,
                dataset_schema: MeasurementDatasetSchema,
            ) -> MeasurementIngestReceipt:
                return executor.ingest_measurements(
                    run_id,
                    lease_id=lease_id,
                    content=encode_measurement_append(append, dataset_schema),
                )

        authority = _LeaseAuthority(
            client=cast("DaemonClient", cast("object", Client())),
            run_id=run_id,
            executor_id="benchmark",
            lease_supervisor=None,
        )
        authority.start()
        measurements = _DaemonMeasurementRepository(authority)
        coverage = _DaemonRunCoverage(
            authority, measurements, _DaemonRunRecoveryGroups(authority)
        )
        schema = MeasurementDatasetSchema(
            dataset_id="raw-measurements",
            point_domain=MeasurementProductGridPointDomain(
                axes=[
                    MeasurementPointDomainAxis(
                        id="point",
                        size=points,
                        source=MeasurementPointDomainValuesSource(
                            values=[
                                MeasurementScalar.create(dtype="int64", value=i)
                                for i in range(points)
                            ]
                        ),
                    )
                ]
            ),
            dimensions=[MeasurementDimension(id="point", kind="point", size=points)],
            variables=[
                MeasurementVariable(
                    id="signal", role="observable", dtype="float64", dims=["point"]
                )
            ],
        )
        measurements.initialize(
            MeasurementDatasetHeader(
                run_id=run_id,
                recording_contract_fingerprint="benchmark.safe-prefix.v1",
                dataset_schema=schema,
                expected_record_count=points,
                record_count_limit=points,
            )
        )
        assert measurements._header_content_hash is not None
        header_hash = measurements._header_content_hash
        transactions_before = transaction_count
        stages: list[dict[str, object]] = []
        started = time.monotonic()
        for index in range(points):
            if time.monotonic() - started > 600:
                raise TimeoutError("600-second cooperative workload budget exceeded")
            measurements.ingest(
                MeasurementDatasetBatch(
                    run_id=run_id,
                    header_content_hash=header_hash,
                    records=(
                        MeasurementRecord(
                            run_id=run_id,
                            logical_point_id=f"point-{index}",
                            point_index=index,
                            coordinates={},
                            observables={
                                "signal": MeasurementScalar.create(
                                    dtype="float64", value=index
                                )
                            },
                        ),
                    ),
                )
            )
            coverage.advance(start_index=index, point_count=1)
            if (index + 1) % 64 == 0:
                measurements.transfer_pending()
            if (index + 1) % 128 == 0 or index == points - 1:
                authority.heartbeat()
                live = runtime.application.runs.measurement_live_preview(
                    run_id, after_record_count=None
                )
                preview = runtime.application.runs.measurement_preview(
                    run_id, limit=4096
                )
                stages.append(
                    {
                        "produced": index + 1,
                        "seconds": time.monotonic() - started,
                        "received": live.received_record_count,
                        "durable": live.durable_record_count,
                        "preview_records": len(preview.items),
                    }
                )
        before_tail = time.monotonic()
        coverage.flush()
        finished = time.monotonic()
        writes = transaction_count - transactions_before
        live = runtime.application.runs.measurement_live_preview(
            run_id, after_record_count=None
        )
        completed = executor.run_coverage(run_id).completed_point_count
        if live.durable_record_count != points or completed != points:
            raise RuntimeError(
                "tail flush did not persist the complete contiguous prefix"
            )
        with sqlite3.connect(runtime.state_dir / "control.sqlite3") as database:
            events = dict(
                cast(
                    "list[tuple[str, int]]",
                    database.execute(
                        "select kind,count(*) from durable_events group by kind"
                    ).fetchall(),
                )
            )
            refs = cast(
                "list[tuple[str]]",
                database.execute(
                    "select ref from execution_measurement_appends"
                ).fetchall(),
            )
            encoded_bytes = sum(
                len(executor._runs.read_bytes(run_id, ref[0])) for ref in refs
            )
        database.close()
        footprint = sum(
            path.stat().st_size for path in root.rglob("*") if path.is_file()
        )
        if footprint > 250 * 1024 * 1024:
            raise RuntimeError("project exceeded the post-run 250 MiB budget")
        return {
            "points": points,
            "seconds": finished - started,
            "points_per_second": points / (finished - started),
            "tail_flush_seconds": finished - before_tail,
            "durable_records": live.durable_record_count,
            "completed_prefix": completed,
            "write_transactions": writes,
            "measurement_transactions": events.get("measurements_appended", 0),
            "coverage_events": events.get("run_coverage_advanced", 0),
            "encoded_append_bytes": encoded_bytes,
            "observed_project_bytes": footprint,
            "stages": stages,
        }


def main(argv: Sequence[str] | None = None) -> int:
    args = _options(argv)
    repo = cast("Path", args.target_repo).resolve()
    python = cast("Path", args.target_python).absolute()
    root = cast("Path", args.data_root).resolve()
    points = cast("int", args.points)
    label = cast("str", args.label)
    if cast("bool", args.worker):
        _check_target(repo)
        record = benchmark_record_header(
            case_id="safe-prefix", case_version=1, kind="component"
        )
        record.update(
            {
                "benchmark": _identity(_HARNESS_ROOT),
                "target": _identity(repo),
                "label": label,
                "python": platform.python_version(),
                "platform": platform.system(),
                **_measure(root / "project", points),
            }
        )
        print(BENCHMARK_RESULT_PREFIX + json.dumps(record, sort_keys=True), flush=True)
        return 0

    # Exclusive creation rejects existing data and makes the cleanup boundary explicit.
    if not python.is_file():
        raise ValueError(
            "--target-python must be an existing target environment interpreter"
        )
    _identity(repo)
    root.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_HARNESS_ROOT)
    environment["SCOPECAT_LOCATOR_HOME"] = str(root / "locator")
    environment["MPLBACKEND"] = "Agg"
    environment.pop("SCOPECAT_APPLICATION_ROOT", None)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    command = [
        str(python),
        "-m",
        "benchmarks",
        "run",
        "safe-prefix",
        "--worker",
        "--target-repo",
        str(repo),
        "--target-python",
        str(python),
        "--data-root",
        str(root),
        "--points",
        str(points),
        "--label",
        label,
    ]
    # No timeout claims: a hard process-tree deadline belongs to the calling runner.
    result = subprocess.run(command, cwd=_HARNESS_ROOT, env=environment, check=False)  # noqa: S603
    if result.returncode == 0:
        shutil.rmtree(root)
    else:
        print("safe-prefix failed; synthetic data directory retained", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
