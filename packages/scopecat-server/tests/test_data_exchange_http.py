"""Portable data uses the application without requesting a device backend."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scopecat.data_exchange import write_scientific_exchange
from scopecat.data_exchange.models import RunEvidence, ScientificEvidence
from scopecat.measurements.archive import RecordSelection, write_measurement_snapshot
from scopecat.records.config import config_content_hash
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
)
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat_testkit.authoring import load_config

from scopecat_server.http import data_exchange
from scopecat_server.instruments.owner import InstrumentBackendOwner
from scopecat_server.runtime import LocalDaemonRuntime


def test_capture_http_without_device_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_activation(self: InstrumentBackendOwner) -> None:
        pytest.fail("data access requested device capabilities")

    monkeypatch.setattr(InstrumentBackendOwner, "get", unexpected_activation)
    config = load_config()
    digest = config_content_hash(config)
    run = RunEvidence(
        source_project_id="source",
        snapshot=RunSnapshot(
            run_id="portable",
            config_content_hash=digest,
            scientific_binding=ResolvedScientificBinding(
                subject=UnboundSubject(),
                config_content_hash=digest,
                setup_content_hash="sha256:" + "0" * 64,
            ),
        ),
        request=RunRequest(experiment_id="original"),
        configuration=config,
        contents=(),
    )
    evidence = ScientificEvidence(
        source_project_id="source", roots=("portable",), runs=(run,)
    )
    source = tmp_path / "capture.scopecat"
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(MeasurementDimension(id="point", kind="point", size=2),),
        variables=(
            MeasurementVariable(
                id="signal", role="observable", dtype="float64", dims=("point",)
            ),
        ),
    )
    header = MeasurementDatasetHeader(
        run_id="portable",
        recording_contract_fingerprint="test",
        dataset_schema=schema,
        expected_record_count=2,
        record_count_limit=2,
    )
    records = tuple(
        MeasurementRecord(
            run_id="portable",
            point_index=point,
            logical_point_id=f"point-{point}",
            coordinates={},
            observables={
                "signal": MeasurementScalar.create(dtype="float64", value=float(index))
            },
        )
        for index, point in enumerate((1, 0, 1))
    )
    recording = tmp_path / "recording.scopecat"
    write_measurement_snapshot(
        recording,
        header,
        (
            MeasurementDatasetAppend(
                run_id="portable",
                header_content_hash=header.content_hash,
                acquisition_start=0,
                records=records[:2],
            ),
            MeasurementDatasetAppend(
                run_id="portable",
                header_content_hash=header.content_hash,
                acquisition_start=2,
                records=records[2:],
            ),
        ),
        projection=(
            RecordSelection(point_index=0, acquisition_index=1),
            RecordSelection(point_index=1, acquisition_index=2),
        ),
    )
    write_scientific_exchange(source, evidence, {"portable": recording})
    content = source.read_bytes()
    conflict = tmp_path / "conflict.scopecat"
    write_scientific_exchange(
        conflict,
        evidence.model_copy(
            update={
                "runs": (
                    run.model_copy(
                        update={"request": RunRequest(experiment_id="different")}
                    ),
                )
            }
        ),
        {},
    )
    with (
        LocalDaemonRuntime(tmp_path / "application") as application,
        TestClient(application.app()) as client,
    ):
        url = "/api/v1/data/captures"
        headers = {"content-type": "application/octet-stream"}
        uploaded = client.post(url, content=content, headers=headers)
        assert uploaded.status_code == 200, uploaded.text
        receipt = uploaded.json()
        assert receipt["created"] is True
        capture_hash = receipt["capture"]["content_hash"]
        source.unlink()
        assert client.get(url).json() == [receipt["capture"]]
        assert client.get(url, params={"offset": 1}).json() == []
        assert client.get(
            f"{url}/{capture_hash}/evidence"
        ).json() == evidence.model_dump(mode="json")
        assert client.get(f"{url}/{capture_hash}/file").content == content
        recording_url = f"{url}/{capture_hash}/runs/portable/recording"
        acquired = client.get(recording_url, params={"limit": 2}).json()
        assert acquired["record_count"] == 3
        assert acquired["next_offset"] == 2
        assert [item["point_index"] for item in acquired["items"]] == [1, 0]
        tail = client.get(recording_url, params={"offset": 2}).json()
        assert tail["next_offset"] is None
        assert [item["point_index"] for item in tail["items"]] == [1]
        selected = client.get(recording_url, params={"selection": "selected"}).json()
        assert selected["record_count"] == 2
        assert selected["items"] == [
            item.model_dump(mode="json") for item in records[1:]
        ]
        assert client.get(recording_url, params={"limit": 101}).status_code == 422
        assert (
            client.get(f"{url}/{capture_hash}/runs/unknown/recording").status_code
            == 404
        )
        repeated = client.post(url, content=content, headers=headers)
        assert repeated.json()["created"] is False
        assert client.post(url, content=b"invalid", headers=headers).status_code == 422
        assert (
            client.post(url, content=conflict.read_bytes(), headers=headers).status_code
            == 409
        )
        assert client.get(url).json() == [receipt["capture"]]
        assert client.get(f"{url}/unknown/evidence").status_code == 404
        assert client.get(f"{url}/unknown/file").status_code == 404
        assert client.post(url, json={"path": str(conflict)}).status_code == 415
        monkeypatch.setattr(data_exchange, "MAX_CAPTURE_UPLOAD_BYTES", 1)
        assert client.post(url, content=content, headers=headers).status_code == 413
        assert client.get(url).json() == [receipt["capture"]]
        assert application.application.devices.list() == ()
