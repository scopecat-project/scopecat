# ruff: noqa: TID251
# Integration benchmark deliberately calls server and testkit production ports.
"""Versioned, synthetic publication comparison; this is NOT safe-prefix v1.

Inspired by benchmarks/component/safe_prefix.py: real production ports, existing
durability policy, isolated directory, setup excluded, explicit ABBA ordering.
Differences: actual available interpreter (not the v1-required Python 3.15.1),
two-channel waveforms, sparse groups, forced per-group flush, and attempt/snapshot
auditing on the experimental side. No hardware, HTTP, GUI, or provider latency.
Disk growth is retained footprint, NOT physical device write amplification.
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
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

BASELINE_SHA = "f2e514a1a1075b9de0c1bf5de697bba455a72aae"
ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required")
    return subprocess.check_output(  # noqa: S603
        [git, "-C", str(repo), *args], text=True
    ).strip()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _footprint(root: Path) -> dict[str, int]:
    files = [p for p in root.rglob("*") if p.is_file()]
    return {
        "all_files_bytes": sum(p.stat().st_size for p in files),
        "sqlite_and_sidecars_bytes": sum(
            p.stat().st_size
            for p in files
            if any(s in p.name for s in (".sqlite", ".db"))
        ),
    }


def _pragmas(connection: sqlite3.Connection) -> dict[str, object]:
    return {
        name: connection.execute(f"PRAGMA {name}").fetchone()[0]
        for name in (
            "journal_mode",
            "synchronous",
            "foreign_keys",
            "wal_autocheckpoint",
            "page_size",
        )
    }


def _payload(points: int, samples: int) -> list[dict]:
    # Availability is scientific data; the same mask and masked values are read
    # back and checked on both sides, outside the timed publication section.
    return [
        {
            "point": point,
            "bias": float(point // (points // 2)),
            "frequency": float(point % (points // 2) + 1),
            "channels": [
                [
                    float(point + channel / 10 + sample / 1000)
                    for sample in range(samples)
                ]
                for channel in range(2)
            ],
            "availability": [
                [
                    not (point % 7 == 0 and sample == samples - 1)
                    for sample in range(samples)
                ]
                for _ in range(2)
            ],
        }
        for point in range(points)
    ]


def _scientific_values(payload: list[dict]) -> list:
    return [
        [
            r["point"],
            r["bias"],
            r["frequency"],
            [
                [
                    value if valid else None
                    for value, valid in zip(values, mask, strict=True)
                ]
                for values, mask in zip(r["channels"], r["availability"], strict=True)
            ],
            r["availability"],
        ]
        for r in payload
    ]


def _groups(points: int) -> list[tuple[int, int]]:
    # First group omits point 0. Logical order is independent from arrival order.
    return [(i, i + points // 2) for i in reversed(range(points // 2))]


def _finish_metrics(
    root: Path,
    before: dict,
    started: float,
    first: float,
    peak: int,
    writes: int,
    payload: list[dict],
    settings: dict,
    **extra: object,
) -> dict:
    elapsed = time.perf_counter() - started
    after = _footprint(root)
    logical_bytes = len(
        json.dumps(_scientific_values(payload), separators=(",", ":")).encode()
    )
    return {
        "publication_seconds": elapsed,
        "first_sparse_readable_seconds": first,
        "tracemalloc_peak_bytes": peak,
        "warmup": "one untimed encode/decode/validation, no persisted writes",
        "successful_write_transactions": writes,
        "logical_scientific_json_bytes": logical_bytes,
        "footprint_before": before,
        "footprint_after": after,
        "retained_growth_over_logical_bytes": (
            after["all_files_bytes"] - before["all_files_bytes"]
        )
        / logical_bytes,
        "sqlite_pragmas": settings,
        "scientific_values_sha256": _hash(_scientific_values(payload)),
        **extra,
    }


def _baseline(root: Path, payload: list[dict], samples: int, repo: Path) -> dict:
    import scopecat.daemon.execution as execution
    import scopecat_server.runtime as runtime_module
    from scopecat.config.documents import load_config_snapshot_document
    from scopecat.config.scientific_binding import bind_scientific_evidence
    from scopecat.control.models import RunPlanSummary, RunResourceRequirement
    from scopecat.daemon.wire import (
        ExecutorStartRequest,
        MeasurementFlushCommand,
        MeasurementHeaderCommand,
        RunRecoveryGroupCommitCommand,
        RunSubmission,
    )
    from scopecat.measurements.recording_arrow import (
        decode_measurement_append,
        encode_measurement_append,
    )
    from scopecat.records.execution import RecoveryGroupCompletion
    from scopecat.records.measurement import (
        MeasurementArray,
        MeasurementArrayAvailability,
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
        MeasurementDatasetHeader,
        measurement_record_content_hash,
    )
    from scopecat.records.run_request import RunRequest
    from scopecat_server.runtime import LocalDaemonRuntime
    from scopecat_server.storage.sqlite.connection import SQLiteDatabase
    from scopecat_server.storage.sqlite.execution import (
        SQLiteMeasurementDatasetRepository,
    )
    from scopecat_testkit.server.instruments import signal_endpoint

    sources = {
        "core": str(Path(execution.__file__).resolve()),
        "server": str(Path(runtime_module.__file__).resolve()),
    }
    if any(
        not Path(source).is_relative_to(repo / "packages")
        for source in sources.values()
    ):
        raise RuntimeError(f"baseline imports must resolve into {repo}: {sources}")
    if _git(repo, "rev-parse", "HEAD") != BASELINE_SHA:
        raise RuntimeError("baseline checkout does not match the fixed SHA")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("baseline has tracked edits; refusing misleading comparison")
    count = len(payload)
    config = load_config_snapshot_document(
        repo / "fixtures/core/simple_scan/config-snapshot.json"
    )
    writes = 0
    original = SQLiteDatabase.write_transaction

    @contextmanager
    def counted(database):
        nonlocal writes
        with original(database) as connection:
            yield connection
        writes += 1

    with (
        patch.object(SQLiteDatabase, "write_transaction", counted),
        LocalDaemonRuntime(
            root / "project",
            bootstrap_config=config,
            instrument_endpoint=signal_endpoint(),
        ) as runtime,
    ):
        executor = runtime.application.executor
        run = runtime.application.submit_run(
            RunSubmission(
                execution_setup=runtime.application.setup.resolve("initial").ref,
                scientific_binding=bind_scientific_evidence(
                    catalog_id="result-ledger-component",
                    config=config,
                    samples=(),
                    sample_revisions={},
                ),
                submission_id="result-ledger-component",
                config=config,
                request=RunRequest(experiment_id="scratch"),
                plan=RunPlanSummary(
                    experiment_id="scratch",
                    experiment_kind="scratch",
                    point_plan_fingerprint="a" * 64,
                    measurement_contract_fingerprint="b" * 64,
                    point_count=count,
                    initial_point_count=count,
                    point_limit=count,
                    run_resource_requirements=(
                        RunResourceRequirement(id="source-0", kind="instrument"),
                    ),
                ),
            )
        ).run_id
        lease = executor.start_executor(
            run, ExecutorStartRequest(executor_id="component-benchmark")
        )
        schema = MeasurementDatasetSchema(
            dataset_id="raw-measurements",
            point_domain=MeasurementProductGridPointDomain(
                axes=[
                    MeasurementPointDomainAxis(
                        id=name,
                        size=len(values),
                        source=MeasurementPointDomainValuesSource(
                            values=[
                                MeasurementScalar.create(
                                    dtype="float64", value=value, unit=unit
                                )
                                for value in values
                            ]
                        ),
                    )
                    for name, values, unit in (
                        ("bias", [0.0, 1.0], "V"),
                        ("frequency", [float(i + 1) for i in range(count // 2)], "Hz"),
                    )
                ]
            ),
            dimensions=[
                MeasurementDimension(id="point", kind="point", size=count),
                MeasurementDimension(id="sample", kind="time", size=samples),
            ],
            variables=[
                MeasurementVariable(
                    id=name,
                    role="coordinate",
                    dtype="float64",
                    unit=unit,
                    dims=["point"],
                )
                for name, unit in (("bias", "V"), ("frequency", "Hz"))
            ]
            + [
                MeasurementVariable(
                    id=f"channel-{channel}",
                    role="observable",
                    dtype="float64",
                    unit="V",
                    dims=["point", "sample"],
                )
                for channel in range(2)
            ]
            + [
                MeasurementVariable(
                    id="time",
                    role="coordinate",
                    dtype="float64",
                    unit="s",
                    dims=["point", "sample"],
                )
            ],
        )
        header = MeasurementDatasetHeader(
            run_id=run,
            recording_contract_fingerprint="result-ledger-component.v1",
            dataset_schema=schema,
            expected_record_count=count,
            record_count_limit=count,
        )
        executor.initialize_measurements(
            run, MeasurementHeaderCommand(lease_id=lease.lease_id, header=header)
        )
        records = [
            MeasurementRecord(
                run_id=run,
                logical_point_id=f"p-{r['point']}",
                point_index=r["point"],
                coordinates={
                    "bias": MeasurementScalar.create(
                        dtype="float64", value=r["bias"], unit="V"
                    ),
                    "frequency": MeasurementScalar.create(
                        dtype="float64", value=r["frequency"], unit="Hz"
                    ),
                    "time": MeasurementArray.create(
                        dtype="float64",
                        values=[i * 1e-9 for i in range(samples)],
                        unit="s",
                    ),
                },
                observables={
                    f"channel-{channel}": MeasurementArray.create(
                        dtype="float64",
                        values=r["channels"][channel],
                        unit="V",
                        availability=None
                        if all(r["availability"][channel])
                        else MeasurementArrayAvailability.create(
                            valid=r["availability"][channel], reason="missing"
                        ),
                        metadata={
                            "provider": "synthetic",
                            "command_id": f"command-{r['point'] % (count // 2)}",
                        },
                    )
                    for channel in range(2)
                },
            )
            for r in payload
        ]
        repository = SQLiteMeasurementDatasetRepository(executor._runs, run_id=run)
        warmup_append = MeasurementDatasetAppend(
            run_id=run,
            header_content_hash=header.content_hash,
            acquisition_start=0,
            records=tuple(records[p] for p in _groups(count)[0]),
        )
        decode_measurement_append(
            encode_measurement_append(warmup_append, schema), schema
        )
        with executor._runs.sqlite.read_connection() as connection:
            settings = _pragmas(connection)
        before, prior_writes = _footprint(root), writes
        first = None
        tracemalloc.start()
        started = time.perf_counter()
        for index, group in enumerate(_groups(count)):
            selected = tuple(records[p] for p in group)
            append = MeasurementDatasetAppend(
                run_id=run,
                header_content_hash=header.content_hash,
                acquisition_start=index * 2,
                records=selected,
            )
            executor.ingest_measurements(
                run,
                lease_id=lease.lease_id,
                content=encode_measurement_append(append, schema),
            )
            executor.flush_measurements(
                run, MeasurementFlushCommand(lease_id=lease.lease_id)
            )
            executor.commit_recovery_groups(
                run,
                RunRecoveryGroupCommitCommand(
                    lease_id=lease.lease_id,
                    groups=(
                        RecoveryGroupCompletion(
                            schedule_fingerprint="component-schedule",
                            group_id=f"g-{group[0]}",
                            point_indices=group,
                            output_kind="measurement",
                            record_content_hashes=tuple(
                                measurement_record_content_hash(r) for r in selected
                            ),
                        ),
                    ),
                ),
            )
            if first is None:
                assert len(repository.measurements()) == 2
                assert executor.run_coverage(run).completed_point_count == 0
                first = time.perf_counter() - started
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        timed_writes = writes - prior_writes
        result = _finish_metrics(
            root,
            before,
            started,
            first,
            peak,
            timed_writes,
            payload,
            settings,
            module_paths=sources,
            completed_prefix=executor.run_coverage(run).completed_point_count,
        )
        result["publication_seconds"] = elapsed
        actual = repository.measurements()
        assert len(actual) == count
        observed = []
        for record in actual:
            assert record.coordinates["bias"].unit == "V"
            assert record.coordinates["frequency"].unit == "Hz"
            assert record.coordinates["time"].unit == "s"
            assert record.coordinates["time"].values.tolist() == [
                i * 1e-9 for i in range(samples)
            ]
            masks = [
                (
                    [True] * samples
                    if record.observables[f"channel-{c}"].availability is None
                    else record.observables[f"channel-{c}"].availability.valid.tolist()
                )
                for c in range(2)
            ]
            values = [
                [
                    float(v) if valid else None
                    for v, valid in zip(
                        record.observables[f"channel-{c}"].values.tolist(),
                        masks[c],
                        strict=True,
                    )
                ]
                for c in range(2)
            ]
            assert all(record.observables[f"channel-{c}"].unit == "V" for c in range(2))
            observed.append(
                [
                    record.point_index,
                    record.coordinates["bias"].value,
                    record.coordinates["frequency"].value,
                    values,
                    masks,
                ]
            )
        assert _hash(observed) == result["scientific_values_sha256"]
        result["verified_roundtrip"] = True
        return result


def _experiment(root: Path, payload: list[dict], samples: int) -> dict:
    from .core import Store, _validate_outputs
    from .core import _hash as core_hash

    count = len(payload)
    groups = _groups(count)
    with Store(root / "ledger.sqlite3") as store:
        store.initialize(
            "component",
            points=[
                {
                    "id": f"p-{r['point']}",
                    "coordinates": {
                        "bias": {"value": r["bias"], "unit": "V"},
                        "frequency": {"value": r["frequency"], "unit": "Hz"},
                    },
                }
                for r in payload
            ],
            units=[
                {
                    "id": f"u-{g[0]}",
                    "point_ids": [f"p-{p}" for p in g],
                    "required_outputs": ["waveform"],
                    "output_contracts": {
                        "waveform": {
                            "dtype": "float64",
                            "unit": "V",
                            "dims": ["point", "channel", "sample"],
                            "shape": [None, 2, samples],
                            "coordinates": {
                                "channel": {
                                    "values": ["channel-0", "channel-1"],
                                    "unit": "",
                                },
                                "sample": {
                                    "values": [i * 1e-9 for i in range(samples)],
                                    "unit": "s",
                                },
                            },
                        }
                    },
                }
                for g in groups
            ],
            groups=[
                {"id": f"g-{g[0]}", "unit_ids": [f"u-{g[0]}"], "closed": True}
                for g in groups
            ],
        )
        fence = store.acquire("component-benchmark")
        # A trace observes actual successful COMMITs without changing store policy.
        connection = next(
            value
            for value in vars(store).values()
            if isinstance(value, sqlite3.Connection)
        )
        writes = 0
        write_transaction = False

        def trace(sql):
            nonlocal writes, write_transaction
            statement = sql.strip().upper()
            if statement.startswith("BEGIN"):
                write_transaction = statement == "BEGIN IMMEDIATE"
            elif statement == "COMMIT" and write_transaction:
                writes += 1
                write_transaction = False
            elif statement == "ROLLBACK":
                write_transaction = False

        connection.set_trace_callback(trace)
        settings, before = _pragmas(connection), _footprint(root)
        outputs = []
        for group in groups:
            masks = [
                value
                for p in group
                for channel in payload[p]["availability"]
                for value in channel
            ]
            values = [
                value
                for p in group
                for channel in payload[p]["channels"]
                for value in channel
            ]
            outputs.append(
                [
                    {
                        "id": "waveform",
                        "point_ids": [f"p-{p}" for p in group],
                        "dims": ["point", "channel", "sample"],
                        "shape": [2, 2, samples],
                        "dtype": "float64",
                        "unit": "V",
                        "values": [
                            v if valid else None
                            for v, valid in zip(values, masks, strict=True)
                        ],
                        "availability": masks,
                        "coordinates": {
                            "channel": {
                                "values": ["channel-0", "channel-1"],
                                "unit": "",
                            },
                            "sample": {
                                "values": [i * 1e-9 for i in range(samples)],
                                "unit": "s",
                            },
                        },
                        "provenance": {
                            "provider": "synthetic",
                            "command_id": f"command-{group[0]}",
                            "unavailable_reason": "missing",
                        },
                    }
                ]
            )
        warmup_unit = json.loads(
            connection.execute(
                "SELECT body FROM units WHERE id=?", (f"u-{groups[0][0]}",)
            ).fetchone()[0]
        )
        _validate_outputs(warmup_unit, outputs[0])
        warmup_payload = {
            "attempt_id": "warmup",
            "unit_id": warmup_unit["id"],
            "outputs": outputs[0],
            "provenance": {"provider": "synthetic"},
        }
        assert core_hash(json.loads(json.dumps(warmup_payload))) == core_hash(
            warmup_payload
        )
        first = None
        snapshots = []
        tracemalloc.start()
        started = time.perf_counter()
        for group, output in zip(groups, outputs, strict=True):
            attempt = store.begin_attempt(fence, f"u-{group[0]}")
            bundle = store.save_result(
                fence, attempt, output, {"provider": "synthetic"}
            )
            store.select_result(fence, f"u-{group[0]}", bundle)
            snapshots.append(store.publish_group(fence, f"g-{group[0]}"))
            if first is None:
                assert store.snapshot(snapshots[-1])
                first = time.perf_counter() - started
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result = _finish_metrics(
            root, before, started, first, peak, writes, payload, settings
        )
        result["publication_seconds"] = elapsed
        connection.set_trace_callback(None)
        # Validation uses snapshots (the actual precise analysis input), not the
        # original Python payload. Core snapshot structure is public API.
        observed = {}
        for snapshot_id in snapshots:
            snapshot = store.snapshot(snapshot_id)
            persisted_points = {point["id"]: point for point in snapshot["points"]}
            for bundle in snapshot["bundles"]:
                for block in bundle["outputs"]:
                    assert block["unit"] == "V" and block["shape"] == [2, 2, samples]
                    assert block["dtype"] == "float64"
                    assert block["coordinates"]["sample"] == {
                        "values": [i * 1e-9 for i in range(samples)],
                        "unit": "s",
                    }
                    assert block["coordinates"]["channel"]["values"] == [
                        "channel-0",
                        "channel-1",
                    ]
                    for offset, point_id in enumerate(block["point_ids"]):
                        point = int(point_id.removeprefix("p-"))
                        coordinates = persisted_points[point_id]["coordinates"]
                        assert coordinates["bias"]["unit"] == "V"
                        assert coordinates["frequency"]["unit"] == "Hz"
                        start = offset * 2 * samples
                        channels = [
                            block["values"][
                                start + c * samples : start + (c + 1) * samples
                            ]
                            for c in range(2)
                        ]
                        masks = [
                            block["availability"][
                                start + c * samples : start + (c + 1) * samples
                            ]
                            for c in range(2)
                        ]
                        observed[point] = [
                            point,
                            coordinates["bias"]["value"],
                            coordinates["frequency"]["value"],
                            channels,
                            masks,
                        ]
        assert (
            _hash([observed[p] for p in sorted(observed)])
            == result["scientific_values_sha256"]
        )
        result["verified_roundtrip"] = True
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--baseline-repo", type=Path, default=Path("/workspace/scopecat")
    )
    parser.add_argument("--points", type=int, nargs="+", default=[16, 64, 256])
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument(
        "--repeats", type=int, default=1, help="Number of explicit ABBA cycles"
    )
    parser.add_argument(
        "--worker", choices=["baseline", "experiment"], help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    if (
        any(n < 4 or n > 1024 or n % 2 for n in args.points)
        or not 2 <= args.samples <= 256
        or not 1 <= args.repeats <= 4
    ):
        parser.error("even points 4..1024, samples 2..256, repeats 1..4 required")
    root, repo = args.directory.resolve(), args.baseline_repo.resolve()
    root.mkdir(parents=True, exist_ok=False)
    if args.worker:
        payload = _payload(args.points[0], args.samples)
        result = (
            _baseline(root, payload, args.samples, repo)
            if args.worker == "baseline"
            else _experiment(root, payload, args.samples)
        )
        print(
            json.dumps(
                {
                    "side": args.worker,
                    "points": args.points[0],
                    "samples": args.samples,
                    **result,
                },
                sort_keys=True,
            )
        )
        return 0
    results = []
    for points in args.points:
        for repeat in range(args.repeats):
            for index, side in enumerate(
                ("baseline", "experiment", "experiment", "baseline")
            ):
                name = f"n{points}-r{repeat}-{index}-{side}"
                command = [
                    sys.executable,
                    "-m",
                    "experiments.result_ledger.benchmark",
                    "--worker",
                    side,
                    "--directory",
                    str(root / name),
                    "--baseline-repo",
                    str(repo),
                    "--points",
                    str(points),
                    "--samples",
                    str(args.samples),
                ]
                environment = os.environ.copy()
                environment.update(
                    {
                        "SCOPECAT_LOCATOR_HOME": str(root / f"{name}-locator"),
                        "MPLBACKEND": "Agg",
                    }
                )
                environment.pop("SCOPECAT_APPLICATION_ROOT", None)
                environment.pop("SCOPECAT_DAEMON_URL", None)
                child = subprocess.run(  # noqa: S603
                    command, cwd=ROOT, env=environment, capture_output=True, text=True
                )
                (root / f"{name}.stderr").write_text(child.stderr)
                if child.returncode:
                    raise RuntimeError(
                        f"benchmark worker failed; retained data/log: {root / name}\n"
                        f"{child.stderr}"
                    )
                record = json.loads(child.stdout.strip().splitlines()[-1])
                record.update(
                    {"command": command, "abba_position": index, "repeat": repeat}
                )
                results.append(record)
                (root / "samples.json").write_text(json.dumps(results, indent=2))
    summary = {
        "protocol": "result-ledger-publication-component",
        "version": 1,
        "baseline_sha": BASELINE_SHA,
        "experiment_sha": _git(ROOT, "rev-parse", "HEAD"),
        "experiment_dirty": bool(_git(ROOT, "status", "--porcelain")),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "uv_lock_sha256": hashlib.sha256((repo / "uv.lock").read_bytes()).hexdigest(),
        "limits": [
            "Not safe-prefix v1 (interpreter/workload/protocol differ)",
            (
                "Baseline includes daemon/schema/Arrow; "
                "experiment is standalone JSON SQLite"
            ),
            "Forced publication per 2-point group; not batching throughput",
            (
                "Experiment audits attempts/selections/snapshots; "
                "baseline only publication recovery proof"
            ),
            "First readable means storage query, not GUI",
            "Tracemalloc tracks timed Python allocations, not native allocations/RSS",
            (
                "Footprint and committed transaction count are proxies, "
                "not physical write amplification"
            ),
            "One untimed encode/decode/validation warms lazy imports; no writes",
            "No adaptive recovery or fault injection measured here; see demo/tests",
        ],
        "samples": results,
    }
    (root / "results.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
