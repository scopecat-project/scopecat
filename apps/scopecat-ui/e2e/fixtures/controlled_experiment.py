"""Exercise durable run states while a browser observes the daemon."""

from __future__ import annotations

import time
from pathlib import Path

import scopecat as sc
from scopecat.daemon.wire import (
    ExecutorLease,
    ExecutorStartRequest,
    MeasurementIngestReceipt,
    RunAdmission,
    RunSubmission,
)
from scopecat.records.measurement import MeasurementDatasetSchema
from scopecat.records.measurement_recording import MeasurementDatasetAppend

PROJECT_ROOT = Path(__file__).resolve().parent
CONTROL_ROOT = PROJECT_ROOT / ".scopecat"
ACCEPTED_READY = CONTROL_ROOT / "e2e-accepted-ready"
RELEASE_ACCEPTED = CONTROL_ROOT / "e2e-release-accepted"
RUNNING_READY = CONTROL_ROOT / "e2e-running-ready"
RELEASE_RUNNING = CONTROL_ROOT / "e2e-release-running"
MEASUREMENT_READY = CONTROL_ROOT / "e2e-measurement-ready"
RELEASE_MEASUREMENT = CONTROL_ROOT / "e2e-release-measurement"


def wait_for_release(path: Path) -> None:
    deadline = time.monotonic() + 45
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for {path.name}")
        time.sleep(0.02)


@sc.experiment(id="ui_e2e_live_scan")
def live_scan(experiment: sc.ExperimentContext) -> sc.CoordinateRef[int]:
    return experiment.scan("value", tuple(range(15)))


project = sc.open_project(PROJECT_ROOT)
with project.connect() as lab:
    client = lab._client  # pyright: ignore[reportPrivateUsage] - gate real transport boundaries
    original_submit = client.submit_run
    original_start = client.start_executor
    original_ingest = client.ingest_measurements
    prepared = lab.prepare(
        live_scan.build(),
        config=lab.parameters.resolve(
            lab.parameters.get("starter-initial"),
            setup=lab.setup.get("starter-bench"),
        ),
    )

    def gated_submit(submission: RunSubmission) -> RunAdmission:
        admission = original_submit(submission)
        ACCEPTED_READY.write_text(admission.run_id, encoding="utf-8")
        wait_for_release(RELEASE_ACCEPTED)
        return admission

    def gated_start(run_id: str, request: ExecutorStartRequest) -> ExecutorLease:
        lease = original_start(run_id, request)
        RUNNING_READY.write_text(run_id, encoding="utf-8")
        wait_for_release(RELEASE_RUNNING)
        return lease

    ingest_count = [0]

    def gated_ingest(
        run_id: str,
        *,
        lease_id: str,
        append: MeasurementDatasetAppend,
        dataset_schema: MeasurementDatasetSchema,
    ) -> MeasurementIngestReceipt:
        receipt = original_ingest(
            run_id,
            lease_id=lease_id,
            append=append,
            dataset_schema=dataset_schema,
        )
        ingest_count[0] += 1
        if ingest_count[0] == 1:
            MEASUREMENT_READY.write_text(run_id, encoding="utf-8")
            wait_for_release(RELEASE_MEASUREMENT)
        return receipt

    client.submit_run = gated_submit
    client.start_executor = gated_start
    client.ingest_measurements = gated_ingest
    try:
        run = prepared.run(name="Live state browser E2E")
    finally:
        client.submit_run = original_submit
        client.start_executor = original_start
        client.ingest_measurements = original_ingest
    summary = {"run_id": run.id, "status": run.status}

print(summary)
