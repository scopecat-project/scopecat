"""Two bounded synthetic GUI workloads; never attach to an existing project.

Run from the source checkout with its locked Python dependencies and built UI::

    PROBE=packages/scopecat-server/tests/measurement_reading_probe.py
    uv run --locked python "$PROBE" NEW_SESSION

For each printed URL, open the GUI, POST /probe/start, inspect the chart and
history/back journey, then POST /probe/finish. The second scene starts only after
the first finishes. /probe/status reports counts and producer stages. Ctrl-C
cancels this session's child only. Retained files belong to this synthetic session;
no desktop window is opened or closed by this command.

The validation journey is read-only chart/history navigation. Import, export,
upload, artifact and download operations are excluded. Byte sampling is an
operational stop threshold with headroom, not a filesystem quota. Stage timestamps
report producer/commit returns, not observed GUI visibility. --interaction-pacing
uses a two-second inter-batch pause for manual interaction (about one to two
minutes per scene), not an unpaced throughput comparison. This HTTP entry does
not qualify an installed or native application shell.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import socket
import tempfile
import time
from contextlib import suppress
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Literal

import psutil
import uvicorn
from fastapi import HTTPException
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary, RunResourceRequirement
from scopecat.daemon.wire import (
    ExecutorHeartbeat,
    ExecutorStartRequest,
    MeasurementFlushCommand,
    MeasurementHeaderCommand,
    RunRecoveryGroupCommitCommand,
    RunSubmission,
)
from scopecat.measurements.recording_arrow import encode_measurement_append
from scopecat.records.execution import RecoveryGroupCompletion
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointDomainAxis,
    MeasurementPointDomainValuesSource,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementVariable,
    MeasurementVariableGroup,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
    measurement_record_content_hash,
)
from scopecat.records.run_request import RunRequest
from scopecat_testkit.server.instruments import signal_endpoint

from scopecat_server import LocalDaemonRuntime

Scene = Literal["scalar", "arrays"]
SCENES: tuple[Scene, ...] = ("scalar", "arrays")
MAX_SESSION_SECONDS = 1800
MAX_SCENE_SECONDS = 900
# One GiB stop threshold leaves one GiB of headroom below the two-GiB budget.
# Inputs are fixed, and each encoded append is additionally capped at 16 MiB.
STOP_BYTES = 1024**3
MAX_APPEND_BYTES = 16 * 1024**2
ROOT = Path(__file__).resolve().parents[3]


def _scalar(value: float, unit: str = "V") -> MeasurementScalar:
    return MeasurementScalar.create(dtype="float64", value=value, unit=unit)


def workload(
    scene: Scene, run_id: str
) -> tuple[MeasurementDatasetHeader, tuple[MeasurementRecord, ...]]:
    count = 4096 if scene == "scalar" else 256
    dimensions = [MeasurementDimension(id="point", kind="point", size=count)]
    variables = [
        MeasurementVariable(
            id="bias", role="coordinate", dtype="float64", unit="V", dims=["point"]
        )
    ]
    if scene == "scalar":
        variables.append(
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="float64",
                unit="V",
                dims=["point"],
            )
        )
    else:
        dimensions.append(MeasurementDimension(id="sample", kind="time", size=128))
        variables.append(
            MeasurementVariable(
                id="time",
                role="coordinate",
                dtype="float64",
                unit="s",
                dims=["point", "sample"],
                recording_group_id="readout",
            )
        )
        variables.extend(
            MeasurementVariable(
                id=name,
                role="observable",
                dtype="float64",
                unit="V",
                dims=["point", "sample"],
                recording_group_id="readout",
            )
            for name in ("I", "Q")
        )
    schema = MeasurementDatasetSchema(
        dataset_id="synthetic-reading",
        point_domain=MeasurementProductGridPointDomain(
            axes=[
                MeasurementPointDomainAxis(
                    id="bias",
                    size=count,
                    source=MeasurementPointDomainValuesSource(
                        values=[_scalar(float(i)) for i in range(count)]
                    ),
                )
            ]
        ),
        dimensions=dimensions,
        variables=variables,
        variable_groups=[]
        if scene == "scalar"
        else [MeasurementVariableGroup(id="readout")],
        primary_coordinates=["bias"] if scene == "scalar" else ["time"],
        primary_observables=["signal"] if scene == "scalar" else ["I", "Q"],
    )
    records = tuple(
        MeasurementRecord(
            run_id=run_id,
            logical_point_id=f"point-{i}",
            point_index=i,
            coordinates={
                "bias": _scalar(float(i)),
                **(
                    {
                        "time": MeasurementArray.create(
                            dtype="float64",
                            unit="s",
                            values=tuple(float(j) for j in range(128)),
                        )
                    }
                    if scene == "arrays"
                    else {}
                ),
            },
            observables={"signal": _scalar(float(i))}
            if scene == "scalar"
            else {
                name: MeasurementArray.create(
                    dtype="float64",
                    unit="V",
                    values=tuple(i + j / 128 for j in range(128)),
                )
                for name in ("I", "Q")
            },
        )
        for i in range(count)
    )
    return MeasurementDatasetHeader(
        run_id=run_id,
        recording_contract_fingerprint="synthetic.reading.v1",
        dataset_schema=schema,
        expected_record_count=count,
        record_count_limit=count,
    ), records


def directory_bytes(root: Path) -> int:
    """Logical bytes across both scenes, without following directory symlinks."""
    total = 0
    for directory, _, names in os.walk(root, followlinks=False):
        for name in names:
            # SQLite can remove a journal during observation.
            with suppress(FileNotFoundError):
                total += (Path(directory) / name).lstat().st_size
    return total


def _write(path: Path, data: object) -> None:
    temporary = path.with_suffix(".tmp")
    _ = temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _join_producer(
    producer: Thread | None, directory: Path, state: dict[str, object]
) -> None:
    if producer is None:
        return
    producer.join(timeout=3)
    if producer.is_alive():
        state["cleanup_error"] = "producer did not stop; terminating owned child"
        _write(directory / "result.json", state)
        os._exit(2)


def _publish_ready(
    server: uvicorn.Server, stop: Event, directory: Path, ready: dict[str, str]
) -> None:
    while not stop.wait(0.05):
        if server.started:
            _write(directory / "ready.json", ready)
            print(json.dumps(ready), flush=True)
            return


def _require_byte_budget(directory: Path) -> None:
    if directory_bytes(directory) >= STOP_BYTES:
        raise RuntimeError("aggregate session byte budget reached")


def _isolate_temp(directory: Path) -> Path:
    temporary = directory / "tmp"
    temporary.mkdir(exist_ok=False)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(temporary)
    # Reset any cached selection made while importing the owned child module.
    tempfile.tempdir = None
    if Path(tempfile.gettempdir()).resolve() != temporary.resolve():
        raise RuntimeError("owned child did not select its local temp directory")
    return temporary


def _serve_scene(
    scene: Scene, directory: Path, static_dir: Path, interaction_pacing: bool
) -> None:
    _isolate_temp(directory)
    # Locator registration is also session-local on Windows and Unix.
    os.environ["SCOPECAT_LOCATOR_HOME"] = str(directory / "locator")
    project = directory / "project"
    project.mkdir(exist_ok=False)
    config = load_config_snapshot_document(
        ROOT / "fixtures/core/simple_scan/config-snapshot.json"
    )
    state: dict[str, object] = {
        "scene": scene,
        "started": False,
        "done": False,
        "completed_records": 0,
        "batches": [],
    }
    pause = 2.0 if interaction_pacing else 0.05
    state["mode"] = "interaction-paced" if interaction_pacing else "unpaced"
    state["inter_batch_pause_seconds"] = pause
    stop = Event()
    producer: Thread | None = None
    start_lock = Lock()
    with LocalDaemonRuntime(
        project, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
        count = 4096 if scene == "scalar" else 256
        run_id = runtime.application.submit_run(
            RunSubmission(
                execution_setup=runtime.application.setup.resolve("initial").ref,
                scientific_binding=bind_scientific_evidence(
                    catalog_id="synthetic",
                    config=config,
                    samples=(),
                    sample_revisions={},
                ),
                submission_id=f"synthetic-{scene}",
                config=config,
                request=RunRequest(experiment_id="synthetic-reading"),
                plan=RunPlanSummary(
                    experiment_id="synthetic-reading",
                    experiment_kind="synthetic-reading",
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
        executor = runtime.application.executor
        lease = executor.start_executor(
            run_id, ExecutorStartRequest(executor_id="synthetic-reading")
        )
        header, records = workload(scene, run_id)
        executor.initialize_measurements(
            run_id, MeasurementHeaderCommand(lease_id=lease.lease_id, header=header)
        )
        state.update(run_id=run_id, total=count)
        batches: list[dict[str, float | int]] = []
        state["batches"] = batches

        def heartbeat() -> None:
            while not stop.wait(1):
                try:
                    executor.heartbeat_executor(
                        run_id, ExecutorHeartbeat(lease_id=lease.lease_id)
                    )
                except Exception as error:
                    state["error"] = str(error)
                    stop.set()

        def produce() -> None:
            state["start_ms"] = time.time() * 1000
            try:
                size = count // 32
                for offset in range(0, count, size):
                    if stop.is_set():
                        return
                    _require_byte_budget(directory.parent)
                    batch = records[offset : offset + size]
                    timing: dict[str, float | int] = {
                        "count": offset + len(batch),
                        "produced_ms": time.time() * 1000,
                    }
                    content = encode_measurement_append(
                        MeasurementDatasetAppend(
                            run_id=run_id,
                            header_content_hash=header.content_hash,
                            acquisition_start=offset,
                            records=batch,
                        ),
                        header.dataset_schema,
                    )
                    if len(content) > MAX_APPEND_BYTES:
                        raise RuntimeError(
                            "synthetic append exceeds its fixed byte budget"
                        )
                    timing["encoded_ms"] = time.time() * 1000
                    executor.ingest_measurements(
                        run_id, lease_id=lease.lease_id, content=content
                    )
                    timing["ingested_ms"] = time.time() * 1000
                    executor.flush_measurements(
                        run_id, MeasurementFlushCommand(lease_id=lease.lease_id)
                    )
                    timing["durable_ms"] = time.time() * 1000
                    executor.commit_recovery_groups(
                        run_id,
                        RunRecoveryGroupCommitCommand(
                            lease_id=lease.lease_id,
                            groups=(
                                RecoveryGroupCompletion(
                                    schedule_fingerprint="synthetic",
                                    group_id=str(offset),
                                    point_indices=tuple(
                                        range(offset, offset + len(batch))
                                    ),
                                    output_kind="measurement",
                                    record_content_hashes=tuple(
                                        measurement_record_content_hash(record)
                                        for record in batch
                                    ),
                                ),
                            ),
                        ),
                    )
                    timing["projection_commit_return_ms"] = time.time() * 1000
                    batches.append(timing)
                    state["completed_records"] = offset + len(batch)
                    if stop.wait(pause):
                        return
                state["done"] = True
            except Exception as error:
                state["error"] = str(error)
            finally:
                state["end_ms"] = time.time() * 1000
                _write(directory / "result.json", state)

        app = runtime.app(static_dir=static_dir)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        )

        @app.get("/probe/status")
        def status() -> dict[str, object]:
            return state.copy()

        @app.post("/probe/start")
        def start() -> dict[str, object]:
            nonlocal producer
            with start_lock:
                if producer is None:
                    state["started"] = True
                    producer = Thread(target=produce, daemon=True)
                    producer.start()
            return {"started": True}

        @app.post("/probe/finish")
        def finish() -> dict[str, bool]:
            if not state["done"] or "error" in state:
                raise HTTPException(
                    409, "The fixed workload has not completed successfully."
                )
            server.should_exit = True
            return {"finished": True}

        # Probe routes precede the application's static fallback.
        app.router.routes[:0] = [
            app.router.routes.pop(-3),
            app.router.routes.pop(-2),
            app.router.routes.pop(-1),
        ]

        def cancellation() -> None:
            while not stop.wait(0.25):
                if (directory / "cancel").exists():
                    state["cancelled"] = True
                    stop.set()
                    server.should_exit = True
                    return

        heartbeat_thread = Thread(target=heartbeat, daemon=True)
        cancel_thread = Thread(target=cancellation, daemon=True)
        heartbeat_thread.start()
        cancel_thread.start()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            ready = {
                "scene": scene,
                "url": url,
                "gui": f"{url}/?run={run_id}",
                "run_id": run_id,
            }

            ready_thread = Thread(
                target=_publish_ready,
                args=(server, stop, directory, ready),
                daemon=True,
            )
            ready_thread.start()
            try:
                server.run(sockets=[listener])
            finally:
                stop.set()
                _join_producer(producer, directory, state)
                heartbeat_thread.join(timeout=2)
                cancel_thread.join(timeout=2)
                ready_thread.join(timeout=2)
                _write(directory / "result.json", state)
        if not state["done"] or "error" in state or state.get("cancelled"):
            raise RuntimeError(
                "synthetic scene did not complete; inspect its local result.json"
            )


def _wait_descendants(owned: list[psutil.Process], timeout: float) -> None:
    _, alive = psutil.wait_procs(owned, timeout=max(0, timeout))
    if alive:
        raise RuntimeError(
            "captured owned descendants have not exited: "
            f"{[child.pid for child in alive]}"
        )


def _cancel(process: BaseProcess, directory: Path, deadline: float) -> None:
    # Capture ownership before allowing the direct child to exit/reparent children.
    owned: list[psutil.Process] = []
    if process.pid is not None:
        with suppress(psutil.NoSuchProcess):
            owned = psutil.Process(process.pid).children(recursive=True)
    (directory / "cancel").touch()
    process.join(timeout=max(0, min(8, deadline - time.monotonic())))
    if process.is_alive():
        if process.pid is not None:
            with suppress(psutil.NoSuchProcess):
                for child in psutil.Process(process.pid).children(recursive=True):
                    if child not in owned:
                        owned.append(child)
        process.kill()
        process.join(timeout=2)
    # psutil Process objects retain identity checks; never discover by name or
    # target descendants of a reused pid after the owned parent has exited.
    for child in owned:
        with suppress(psutil.NoSuchProcess):
            child.kill()
    _wait_descendants(owned, timeout=2)
    if process.is_alive():
        raise RuntimeError("owned probe process did not terminate")


def run_session(
    session: Path,
    static_dir: Path,
    seconds_per_scene: int,
    interaction_pacing: bool = False,
) -> None:
    if not 15 <= seconds_per_scene <= MAX_SCENE_SECONDS:
        raise ValueError("seconds-per-scene must be between 15 and 900")
    if not (static_dir / "index.html").is_file():
        raise ValueError("build the UI first; static-dir must contain index.html")
    session.mkdir(parents=True, exist_ok=False)
    session_deadline = time.monotonic() + MAX_SESSION_SECONDS
    context = multiprocessing.get_context("spawn")
    for scene in SCENES:
        directory = session / scene
        directory.mkdir(exist_ok=False)
        deadline = min(session_deadline, time.monotonic() + seconds_per_scene)
        process = context.Process(
            target=_serve_scene, args=(scene, directory, static_dir, interaction_pacing)
        )
        process.start()
        try:
            while process.is_alive():
                if time.monotonic() >= deadline - 10:
                    raise TimeoutError("scene/session time budget reached")
                if directory_bytes(session) >= STOP_BYTES:
                    raise RuntimeError(
                        "aggregate session data reached the one-GiB stop threshold"
                    )
                process.join(timeout=0.5)
            if process.exitcode != 0:
                raise RuntimeError(
                    f"{scene} exited with {process.exitcode}; no second attempt"
                )
            if directory_bytes(session) >= STOP_BYTES:
                raise RuntimeError("aggregate data budget reached; no further scene")
        finally:
            if process.is_alive():
                _cancel(process, directory, deadline)
            process.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "session",
        type=Path,
        help="new directory for BOTH scenes; existing paths are rejected",
    )
    parser.add_argument(
        "--static-dir", type=Path, default=ROOT / "apps/scopecat-ui/dist"
    )
    parser.add_argument("--seconds-per-scene", type=int, default=600)
    parser.add_argument(
        "--interaction-pacing",
        action="store_true",
        help="2-second batch pauses for manual interaction, not throughput measurement",
    )
    args = parser.parse_args()
    run_session(
        args.session.resolve(),
        args.static_dir.resolve(),
        args.seconds_per_scene,
        args.interaction_pacing,
    )


if __name__ == "__main__":
    main()
