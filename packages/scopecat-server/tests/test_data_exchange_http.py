"""Portable data uses the application without requesting a device backend."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scopecat.data_exchange import (
    PayloadReference,
    PayloadSource,
    write_scientific_exchange,
)
from scopecat.data_exchange.models import (
    AnalysisEvidence,
    RunEvidence,
    ScientificEvidence,
)
from scopecat.kernel.content_identity import (
    model_wire_content_hash,
    sha256_content_hash,
)
from scopecat.measurements.archive import RecordSelection, write_measurement_snapshot
from scopecat.records.analysis import (
    AnalysisArtifactRecordOutput,
    AnalysisArtifactReference,
    AnalysisRecord,
    RunAnalysisSubject,
)
from scopecat.records.config import config_content_hash
from scopecat.records.content import ContentEntry
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
from scopecat.runs.refs import content_entry_ref
from scopecat_testkit.authoring import load_config

from scopecat_server.http import data_exchange
from scopecat_server.instruments.owner import InstrumentBackendOwner
from scopecat_server.runtime import LocalDaemonRuntime


def _evidence() -> ScientificEvidence:
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
    return ScientificEvidence(
        source_project_id="source", roots=("portable",), runs=(run,)
    )


def test_captured_analysis_artifacts_use_exact_record_identity(tmp_path: Path) -> None:
    evidence = _evidence()
    runs = tuple(
        evidence.runs[0].model_copy(
            update={
                "snapshot": evidence.runs[0].snapshot.model_copy(
                    update={"run_id": run_id}
                )
            }
        )
        for run_id in ("first", "second")
    )
    analyses = []
    payloads = []
    for run in runs:
        run_id = run.snapshot.run_id
        artifact = ContentEntry(
            role="artifact",
            id="attachment",
            kind="file",
            content_hash=sha256_content_hash(run_id.encode()),
            filename="result.txt",
            media_type="text/plain",
        )
        record = AnalysisRecord(
            subject=RunAnalysisSubject(run_id=run_id),
            title="Result",
            revision=1,
            publication_hash="same-declared-publication-hash",
            outputs=[
                AnalysisArtifactRecordOutput(
                    kind="artifact",
                    id="output",
                    title="Attachment",
                    content=AnalysisArtifactReference(
                        artifact_id=artifact.id,
                        content_hash=artifact.content_hash,
                        media_type="text/plain",
                        filename="result.txt",
                    ),
                )
            ],
        )
        entry = ContentEntry(
            role="record",
            id="same-analysis-id",
            kind="analysis",
            content_hash=model_wire_content_hash(record),
        )
        analyses.append(
            AnalysisEvidence(
                entry=entry,
                record=record,
                published_at=datetime.now(UTC),
                contents=(entry, artifact),
            )
        )
        for item, content in (
            (entry, record.model_dump_json().encode()),
            (artifact, run_id.encode()),
        ):
            path = tmp_path / f"{run_id}-{item.role}"
            path.write_bytes(content)
            payloads.append(
                PayloadSource(
                    PayloadReference(
                        owner_kind="run",
                        owner_id=run_id,
                        ref=content_entry_ref(item),
                        digest=sha256_content_hash(content),
                        size=len(content),
                    ),
                    path,
                )
            )
    source = tmp_path / "analyses.scopecat"
    write_scientific_exchange(
        source,
        evidence.model_copy(
            update={
                "roots": ("first", "second"),
                "runs": runs,
                "analyses": tuple(analyses),
            }
        ),
        {},
        payloads=payloads,
    )
    with (
        LocalDaemonRuntime(tmp_path / "app") as runtime,
        TestClient(runtime.app()) as client,
    ):
        receipt = client.post(
            "/api/v1/data/captures",
            content=source.read_bytes(),
            headers={"content-type": "application/octet-stream"},
        )
        assert receipt.status_code == 200, receipt.text
        capture_hash = receipt.json()["capture"]["content_hash"]
        for run_id, analysis in zip(("first", "second"), analyses, strict=True):
            url = (
                f"/api/v1/data/captures/{capture_hash}/analyses/"
                f"{analysis.entry.content_hash}/artifacts/attachment"
            )
            response = client.get(url)
            assert response.status_code == 200, response.text
            assert response.content == run_id.encode()
            assert "result.txt" in response.headers["content-disposition"]
            assert client.get(url + "-missing").status_code == 404


def test_capture_http_without_device_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_activation(self: InstrumentBackendOwner) -> None:
        pytest.fail("data access requested device capabilities")

    monkeypatch.setattr(InstrumentBackendOwner, "get", unexpected_activation)
    evidence = _evidence()
    run = evidence.runs[0]
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
