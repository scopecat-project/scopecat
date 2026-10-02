"""Portable data uses the application without requesting a device backend."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scopecat.analysis.repository import AnalysisPublication
from scopecat.data_exchange import (
    PayloadReference,
    PayloadSource,
    ScientificExchange,
    write_scientific_exchange,
)
from scopecat.data_exchange.models import (
    AnalysisEvidence,
    CaptureImportReceipt,
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
    ProjectAnalysisSubject,
    RunAnalysisSubject,
)
from scopecat.records.config import config_content_hash
from scopecat.records.content import ContentEntry, ModelWrite
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementUnavailable,
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
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat.runs.admission import build_run_admission
from scopecat.runs.refs import content_entry_ref
from scopecat_testkit.authoring import load_config

from scopecat_server.http import data_exchange
from scopecat_server.instruments.backend import InstrumentBackendUnavailable
from scopecat_server.instruments.owner import InstrumentBackendOwner
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


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


def test_external_analysis_import_preserves_existing_source_identity(tmp_path: Path):
    source = tmp_path / "source.scopecat"
    write_scientific_exchange(source, _evidence(), {})
    record = AnalysisRecord(
        subject=ProjectAnalysisSubject(),
        title="External result",
        key="external",
        revision=1,
        publication_hash="external-result",
        outputs=[],
    )
    entry = ContentEntry(
        role="record",
        id="analysis-external-r1",
        kind="analysis",
        content_hash=model_wire_content_hash(record),
    )
    publication = AnalysisPublication(
        subject=record.subject,
        record=entry,
        entries=(entry,),
        analysis_key="external",
        revision=1,
        publication_hash=record.publication_hash,
        title=record.title,
        step_id=None,
        input_count=0,
        output_count=0,
        models=(ModelWrite(content_entry_ref(entry), record),),
    )
    result = tmp_path / "result.scopecat"
    with ScientificExchange(source) as captured:
        captured.write_analyses(result, (publication,))
    with (
        LocalDaemonRuntime(tmp_path / "app") as runtime,
        TestClient(runtime.app()) as client,
    ):
        receipts: list[CaptureImportReceipt] = []
        for path in (source, result, result):
            response = client.post(
                "/api/v1/data/captures",
                content=path.read_bytes(),
                headers={"Content-Type": "application/octet-stream"},
            )
            assert response.status_code == 200, response.text
            receipts.append(CaptureImportReceipt.model_validate(response.json()))
        assert [receipt.created for receipt in receipts] == [True, True, False]
        original_hash, result_hash = (
            receipt.capture.content_hash for receipt in receipts[:2]
        )
        assert original_hash != result_hash
        original = client.get(f"/api/v1/data/captures/{original_hash}/evidence").json()
        analyzed = client.get(f"/api/v1/data/captures/{result_hash}/evidence").json()
        assert analyzed["runs"] == original["runs"]
        assert original["analyses"] == []
        assert analyzed["analyses"][0]["record"] == record.model_dump(mode="json")


def test_current_run_export_is_portable_without_device_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def unexpected_activation(self: InstrumentBackendOwner):
        pytest.fail("export requested device capabilities")

    monkeypatch.setattr(InstrumentBackendOwner, "get", unexpected_activation)
    with (
        LocalDaemonRuntime(tmp_path / "application") as runtime,
        TestClient(runtime.app()) as client,
    ):
        store = SQLiteProjectStore(
            SQLiteDatabase(runtime.state_dir / "control.sqlite3"),
            runtime.state_dir / "objects",
        )
        try:
            runs = SQLiteRunRepository(store.sqlite, store.objects.root)
            config = load_config()
            skeleton = build_run_admission(
                config=config,
                request=RunRequest(experiment_id="retained"),
                scientific_binding=ResolvedScientificBinding(
                    subject=UnboundSubject(),
                    config_content_hash=config_content_hash(config),
                    setup_content_hash=ExecutableSetupSnapshot.from_config(
                        config
                    ).execution_content_hash,
                ),
            )
            prepared = runs.prepare_run_skeleton(skeleton)
            with store.sqlite.write_transaction() as connection:
                runs.commit_run_skeleton_in_transaction(connection, prepared)
            run_id = skeleton.snapshot.run_id
            response = client.get(f"/api/v1/data/runs/{run_id}/file")
            assert response.status_code == 200, response.text
            assert "run.scopecat" in response.headers["content-disposition"]
            exported = tmp_path / "export.scopecat"
            exported.write_bytes(response.content)
            with ScientificExchange(exported) as capture:
                capture.verify()
                assert capture.evidence.roots == (run_id,)
                assert capture.evidence.runs[0].request.experiment_id == "retained"
            assert client.get("/api/v1/data/runs/missing/file").status_code == 404
        finally:
            store.close()


def test_captured_traces_keep_selection_failures_and_sampling_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_activation(self: InstrumentBackendOwner) -> None:
        pytest.fail("captured traces requested device capabilities")

    monkeypatch.setattr(InstrumentBackendOwner, "get", unexpected_activation)
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=2),
            MeasurementDimension(id="sample", kind="sample", size=8192),
        ),
        variables=(
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype="float64",
                dims=("point", "sample"),
                unit="V",
            ),
        ),
    )
    records = (
        *(
            MeasurementRecord(
                run_id="portable",
                point_index=0,
                coordinates={},
                observables={
                    "signal": MeasurementArray.create(
                        dtype="float64",
                        unit="V",
                        values=[peak if i == 64 else 0.0 for i in range(8192)],
                    )
                },
            )
            for peak in (100.0, 200.0)
        ),
        MeasurementRecord(
            run_id="portable",
            point_index=1,
            coordinates={},
            observables={
                "signal": MeasurementUnavailable.create(
                    reason="missing",
                    dtype="float64",
                    unit="V",
                    shape=(8192,),
                    metadata={},
                )
            },
        ),
    )
    header = MeasurementDatasetHeader(
        run_id="portable",
        recording_contract_fingerprint="test",
        dataset_schema=schema,
        expected_record_count=2,
        record_count_limit=2,
    )
    recording = tmp_path / "recording.scopecat"
    write_measurement_snapshot(
        recording,
        header,
        tuple(
            MeasurementDatasetAppend(
                run_id="portable",
                header_content_hash=header.content_hash,
                acquisition_start=index,
                records=(record,),
            )
            for index, record in enumerate(records)
        ),
        projection=(
            RecordSelection(point_index=0, acquisition_index=1),
            RecordSelection(point_index=1, acquisition_index=2),
        ),
    )
    source = tmp_path / "traces.scopecat"
    write_scientific_exchange(source, _evidence(), {"portable": recording})
    with (
        LocalDaemonRuntime(tmp_path / "application") as runtime,
        TestClient(runtime.app()) as client,
    ):
        imported = client.post(
            "/api/v1/data/captures",
            content=source.read_bytes(),
            headers={"content-type": "application/octet-stream"},
        )
        assert imported.status_code == 200, imported.text
        capture_hash = imported.json()["capture"]["content_hash"]
        url = f"/api/v1/data/captures/{capture_hash}/runs/portable/recording/traces"
        table = client.get(url.removesuffix("/traces"))
        assert table.status_code == 200, table.text
        value = table.json()["items"][0]["observables"]["signal"]
        assert value["kind"] == "array_summary"
        assert value["shape"] == [8192]
        assert value["available_sample_count"] == 8192
        assert "values" not in value
        assert len(table.content) < 6000
        query = {"observable_id": "signal", "max_samples": 8}
        for selection, peak in (("acquired", 100), ("selected", 200)):
            response = client.post(url, params={"selection": selection}, json=query)
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview["source_sample_count"] == 8192
            assert preview["returned_sample_count"] <= 8
            assert preview["samples_reduced"] is True
            assert max(preview["series"][0]["y"]) == peak
        failed = client.post(url, params={"offset": 2}, json=query).json()
        assert failed["series"] == []
        assert failed["failures"][0]["reasons"] == ["missing"]
        limited = client.post(
            url, params={"limit": 3}, json={**query, "max_series": 1}
        ).json()
        assert limited["selected_series_count"] == 3
        assert limited["inspected_series_count"] == 1
        assert limited["truncated_series"] is True
        assert client.post(url, json={"observable_id": "unknown"}).status_code == 409
        assert client.post(url, json={**query, "max_samples": 1}).status_code == 422


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
    analyses: list[AnalysisEvidence] = []
    payloads: list[PayloadSource] = []
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


@pytest.mark.parametrize("failed_driver", [False, True], ids=["unused", "failed"])
def test_capture_http_without_device_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_driver: bool
) -> None:
    def unexpected_activation(self: InstrumentBackendOwner) -> None:
        pytest.fail("data access requested device capabilities")

    def unavailable(*_args: object) -> None:
        raise InstrumentBackendUnavailable("vendor environment unavailable")

    monkeypatch.setattr("scopecat_server.runtime.restore_driver_source", unavailable)
    if not failed_driver:
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
        if failed_driver:
            with pytest.raises(
                InstrumentBackendUnavailable, match="vendor environment"
            ):
                application.application.instruments.driver_catalog()
        monkeypatch.setattr(InstrumentBackendOwner, "get", unexpected_activation)
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
        assert [item["point_index"] for item in selected["items"]] == [0, 1]
        assert [
            item["observables"]["signal"]["value"] for item in selected["items"]
        ] == [1, 2]
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
