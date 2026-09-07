from __future__ import annotations

from pathlib import Path

import pytest
from scopecat.kernel.errors import DataIntegrityError, NotFound
from scopecat.measurements.recording_arrow import (
    decode_measurement_append,
    encode_measurement_append,
)
from scopecat.runs.access import (
    dataset_storage_ref,
)
from scopecat.runs.service import (
    load_run_request,
    read_run_artifact_bytes,
    read_run_artifact_text,
    read_run_measurement_dataset,
)
from scopecat_testkit.server.runtime import (
    list_test_runs,
    sqlite_project_services,
    sqlite_run_repository,
)
from scopecat_testkit.server.signal_testkit import execute_signal_run
from scopecat_testkit.server.workflow_fixtures import attach_binary_artifact
from scopecat_testkit.workflow_fixtures import (
    load_config,
    load_invocation,
)


def test_workflow_run_data_access_reads_runs_artifacts_and_datasets(
    tmp_path: Path,
) -> None:
    config = load_config()
    experiment = load_invocation()
    baseline = execute_signal_run(
        config=config,
        experiment=experiment,
        project_root=tmp_path,
    )
    candidate = execute_signal_run(
        config=config,
        experiment=experiment,
        project_root=tmp_path,
    )
    services = sqlite_project_services(tmp_path)
    runs = list_test_runs(services.runs)
    snapshot = services.runs.read_snapshot(candidate.run_id)
    run_config = services.runs.read_config_profile_snapshot(candidate.run_id)
    run_request = load_run_request(
        run_id=candidate.run_id, services=sqlite_project_services(tmp_path)
    )
    artifacts = services.runs.list_contents(
        candidate.run_id,
        limit=100,
        role="artifact",
    ).items
    contents = services.runs.list_contents(
        candidate.run_id,
        limit=100,
    ).items
    measurement_datasets = services.runs.list_contents(
        candidate.run_id,
        limit=100,
        role="dataset",
        kind="measurement_dataset",
    ).items
    raw_dataset = read_run_measurement_dataset(
        run_id=candidate.run_id,
        services=sqlite_project_services(tmp_path),
    )
    assert [run.run_id for run in runs] == [
        baseline.run_id,
        candidate.run_id,
    ]
    assert snapshot.run_id == candidate.run_id
    assert snapshot.outcome is not None
    assert snapshot.outcome.result == "succeeded"
    assert run_config.id == "simple-scan-profile"
    assert run_request.experiment_id == "test.workflow_scan"
    assert artifacts == ()
    assert [entry.id for entry in contents] == [
        "raw-measurements",
        "instrument-state-evidence",
        "compilation-cost",
    ]
    assert [dataset.id for dataset in measurement_datasets] == ["raw-measurements"]
    assert raw_dataset.dataset_entry.id == "raw-measurements"
    assert raw_dataset.dataset.dataset_schema.dataset_id == "raw-measurements"
    assert len(raw_dataset.dataset.records) == 3


def test_workflow_run_data_access_rejects_invalid_reads(tmp_path: Path) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    attach_binary_artifact(tmp_path, run.run_id)

    with pytest.raises(NotFound) as missing_run:
        sqlite_project_services(tmp_path).runs.read_snapshot("run_missing")
    with pytest.raises(NotFound) as missing_artifact:
        read_run_artifact_text(
            run_id=run.run_id,
            selector="missing-artifact",
            services=sqlite_project_services(tmp_path),
        )
    with pytest.raises(NotFound) as path_escape:
        read_run_artifact_text(
            run_id=run.run_id,
            selector="../legacy-manifest.json",
            services=sqlite_project_services(tmp_path),
        )
    binary = read_run_artifact_bytes(
        run_id=run.run_id,
        selector="binary-artifact",
        services=sqlite_project_services(tmp_path),
    )

    assert missing_run.value.problems[0].code == "run.not_found"
    assert missing_artifact.value.problems[0].code == "run.artifact_not_found"
    assert path_escape.value.problems[0].code == "run.artifact_not_found"
    assert binary.artifact.id == "binary-artifact"
    assert binary.content == b"\x00\x01"


def test_workflow_run_data_access_rejects_invalid_typed_storage_rows(
    tmp_path: Path,
) -> None:
    run = execute_signal_run(
        config=load_config(),
        experiment=load_invocation(),
        project_root=tmp_path,
    )
    raw_dataset = read_run_measurement_dataset(
        run_id=run.run_id,
        services=sqlite_project_services(tmp_path),
    )
    storage = sqlite_run_repository(tmp_path)

    invalid_measurement = raw_dataset.dataset.records[0].model_copy(
        update={"observables": {}, "acquisition_evidence": {}}
    )
    raw_dataset_entry = storage.read_content(
        run.run_id,
        role="dataset",
        content_id="raw-measurements",
    )
    measurement_ref = (
        f"{dataset_storage_ref(raw_dataset_entry)}/chunks/00000000000000000000.arrow"
    )
    schema = raw_dataset.dataset.dataset_schema
    append = decode_measurement_append(
        storage.read_bytes(run.run_id, measurement_ref),
        schema,
    )
    invalid_schema = schema.model_copy(
        update={
            "variables": tuple(
                variable
                for variable in schema.variables
                if variable.role != "observable"
            ),
            "primary_observables": (),
        }
    )
    storage.write_bytes(
        run.run_id,
        measurement_ref,
        encode_measurement_append(
            append.model_copy(update={"records": (invalid_measurement,)}),
            invalid_schema,
        ),
    )
    with pytest.raises(DataIntegrityError) as invalid_scalar_row:
        read_run_measurement_dataset(
            run_id=run.run_id,
            services=sqlite_project_services(tmp_path),
        )

    assert (
        invalid_scalar_row.value.problems[0].code == "run.measurement_dataset.invalid"
    )
