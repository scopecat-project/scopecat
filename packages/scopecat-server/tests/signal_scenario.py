"""Shared retained signal scenario using admission, executor lease and ingest services.

This seeds measured records through the real service lifecycle; it does not run
an acquisition worker or establish physical-device evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary, RunResourceRequirement
from scopecat.daemon.wire import (
    ExecutorStartRequest,
    MeasurementFlushCommand,
    MeasurementHeaderCommand,
    MeasurementSealCommand,
    RunCoverageAdvanceCommand,
    RunSubmission,
    TerminalRunCommitCommand,
)
from scopecat.execution.evidence import build_terminal_contents
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.measurements.recording_arrow import encode_measurement_append
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityIndex,
    MeasurementProductGridPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
    MeasurementDatasetSeal,
    measurement_dataset_content_hash,
    measurement_fragment_content_hash,
)
from scopecat.records.run_request import RunRequest

from scopecat_server import LocalDaemonRuntime

_FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "core"
    / "simple_scan"
    / "config-snapshot.json"
)


def signal_config() -> ConfigProfileSnapshot:
    return load_config_snapshot_document(_FIXTURE)


def signal_submission(
    runtime: LocalDaemonRuntime,
    submission_id: str = "submission-1",
    *,
    point_count: int = 1,
) -> RunSubmission:
    return RunSubmission(
        execution_setup=runtime.application.setup.resolve("initial").ref,
        scientific_binding=bind_scientific_evidence(
            catalog_id="test", config=signal_config(), samples=(), sample_revisions={}
        ),
        submission_id=submission_id,
        config=signal_config(),
        request=RunRequest(experiment_id="scratch"),
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=point_count,
            initial_point_count=point_count,
            point_limit=point_count,
            run_resource_requirements=(
                RunResourceRequirement(id="source-0", kind="instrument"),
            ),
        ),
    )


def complete_signal_run(
    runtime: LocalDaemonRuntime,
    *,
    submission_id: str,
    signal: float | tuple[float, ...],
    entities: tuple[EntityRef, ...] | None = None,
    submission: RunSubmission | None = None,
) -> str:
    request = submission or signal_submission(runtime, submission_id)
    binding = bind_scientific_evidence(
        catalog_id=runtime.application.project_id,
        config=request.config,
        samples=runtime.application.samples.resolve_bindings(request.request.samples),
        sample_revisions={},
    )
    request = request.model_copy(
        update={
            "scientific_binding": binding,
            "request": request.request.model_copy(
                update={"samples": binding.sample_selectors()}
            ),
        }
    )
    admission = runtime.application.submit_run(request)
    run_id = admission.run_id
    lease = runtime.application.executor.start_executor(
        run_id,
        ExecutorStartRequest(executor_id=f"executor-{submission_id}"),
    )
    header = MeasurementDatasetHeader(
        run_id=run_id,
        recording_contract_fingerprint="test.project-analysis.v1",
        dataset_schema=MeasurementDatasetSchema(
            dataset_id="raw-measurements",
            point_domain=MeasurementProductGridPointDomain(axes=[]),
            dimensions=[
                MeasurementDimension(id="point", kind="point", size=1),
                *(
                    [
                        MeasurementDimension(
                            id="entity",
                            kind="entity",
                            size=len(entities),
                            index=MeasurementEntityIndex(values=entities),
                        )
                    ]
                    if entities is not None
                    else []
                ),
            ],
            variables=[
                MeasurementVariable(
                    id="signal",
                    role="observable",
                    dtype="float64",
                    unit="ratio",
                    dims=["point"] if entities is None else ["point", "entity"],
                )
            ],
        ),
        expected_record_count=1,
        record_count_limit=1,
    )
    record = MeasurementRecord(
        run_id=run_id,
        logical_point_id="point-0",
        point_index=0,
        coordinates={},
        observables={
            "signal": MeasurementScalar.create(
                dtype="float64",
                value=cast("float", signal),
                unit="ratio",
            )
            if entities is None
            else MeasurementArray.create(
                values=np.asarray(signal, dtype=np.float64), unit="ratio"
            )
        },
    )
    append = MeasurementDatasetAppend(
        run_id=run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=(record,),
    )
    runtime.application.executor.initialize_measurements(
        run_id,
        MeasurementHeaderCommand(lease_id=lease.lease_id, header=header),
    )
    runtime.application.executor.ingest_measurements(
        run_id,
        lease_id=lease.lease_id,
        content=encode_measurement_append(append, header.dataset_schema),
    )
    runtime.application.executor.flush_measurements(
        run_id,
        MeasurementFlushCommand(lease_id=lease.lease_id),
    )
    runtime.application.executor.advance_run_coverage(
        run_id,
        RunCoverageAdvanceCommand(
            lease_id=lease.lease_id,
            start_index=0,
            point_count=1,
        ),
    )
    runtime.application.executor.seal_measurements(
        run_id,
        MeasurementSealCommand(
            lease_id=lease.lease_id,
            seal=MeasurementDatasetSeal(
                run_id=run_id,
                header_content_hash=header.content_hash,
                record_count=1,
                fragment_record_count=1,
                fragment_content_hash=measurement_fragment_content_hash(
                    header_content_hash=header.content_hash,
                    record_content_hashes=append.record_content_hashes,
                ),
            ),
        ),
    )
    outcome = RunOutcome(
        run_id=run_id,
        result="succeeded",
        certainty="known",
    )
    runtime.application.executor.commit_terminal(
        run_id,
        TerminalRunCommitCommand(
            lease_id=lease.lease_id,
            outcome=outcome,
            contents=build_terminal_contents(
                outcome=outcome,
                measurement_count=1,
                dataset_content_hash=measurement_dataset_content_hash(
                    header_content_hash=header.content_hash,
                    record_content_hashes=append.record_content_hashes,
                ),
                dataset_schema=header.dataset_schema,
                expected_record_count=1,
                instrument_state=None,
            ),
        ),
    )
    return run_id
