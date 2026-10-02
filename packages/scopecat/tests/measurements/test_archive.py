from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zipfile import ZipFile

import pytest
from scopecat_testkit.workflow_fixtures import load_config

from scopecat.api.analysis import analysis_function
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.data_exchange import (
    PayloadReference,
    PayloadSource,
    ScientificExchange,
    write_scientific_exchange,
)
from scopecat.data_exchange.models import RunEvidence, ScientificEvidence
from scopecat.kernel.content_identity import sha256_content_hash
from scopecat.measurements.archive import (
    MeasurementSnapshot,
    RecordSelection,
    write_measurement_snapshot,
)
from scopecat.measurements.dataset import Dataset
from scopecat.measurements.imports import import_measurement_snapshot
from scopecat.records.config import config_content_hash
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainColumn,
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
from scopecat.runs.refs import content_entry_ref


def exchange_evidence() -> ScientificEvidence:
    config = load_config()
    return ScientificEvidence(
        source_project_id="source-project",
        roots=("synthetic",),
        runs=(
            RunEvidence(
                source_project_id="source-project",
                snapshot=RunSnapshot(
                    run_id="synthetic",
                    scientific_binding=bind_scientific_evidence(
                        catalog_id="source-project",
                        config=config,
                        samples=(),
                        sample_revisions={},
                    ),
                    config_content_hash=config_content_hash(config),
                ),
                request=RunRequest(experiment_id="retained"),
                configuration=config,
                contents=(),
            ),
        ),
    )


@pytest.mark.parametrize(
    "missing",
    ["subject", "configuration", "measurement", "interpretation", "publication"],
)
def test_exchange_rejects_missing_analysis_dependencies(tmp_path: Path, missing: str):
    from datetime import UTC, datetime

    from scopecat.data_exchange.models import AnalysisEvidence
    from scopecat.kernel.content_identity import model_wire_content_hash
    from scopecat.records.analysis import (
        AnalysisInterpretationReference,
        AnalysisPublishedOutputReference,
        AnalysisRecord,
        AnalysisRecordInput,
        ConfigurationAnalysisRecordInput,
        InterpretationAnalysisRecordInput,
        MeasurementAnalysisRecordInput,
        PublishedAnalysisRecordInput,
        RunAnalysisSubject,
    )

    evidence = exchange_evidence()
    inputs: list[AnalysisRecordInput] = []
    if missing == "configuration":
        inputs.append(
            ConfigurationAnalysisRecordInput(
                run_id="absent",
                id="input",
                target="retained",
                content_hash="sha256:" + "a" * 64,
                codec="test",
                role="source",
            )
        )
    elif missing == "measurement":
        inputs.append(
            MeasurementAnalysisRecordInput(
                run_id="absent",
                id="input",
                target="retained",
                content_hash="sha256:" + "a" * 64,
                codec="test",
                role="source",
            )
        )
    elif missing == "interpretation":
        inputs.append(
            InterpretationAnalysisRecordInput(
                source=AnalysisInterpretationReference(
                    procedure_run_id="absent",
                    step_key="decision",
                    request_hash="sha256:" + "b" * 64,
                    response_hash="sha256:" + "a" * 64,
                ),
                id="input",
                target="retained",
                content_hash="sha256:" + "a" * 64,
                codec="test",
                role="source",
            )
        )
    elif missing == "publication":
        inputs.append(
            PublishedAnalysisRecordInput(
                kind="analysis_fact",
                source=AnalysisPublishedOutputReference(
                    subject=RunAnalysisSubject(run_id="synthetic"),
                    analysis_record_id="absent",
                    output_id="value",
                ),
                id="input",
                target="retained",
                content_hash="sha256:" + "a" * 64,
                codec="test",
                role="source",
            )
        )
    record = AnalysisRecord(
        subject=RunAnalysisSubject(
            run_id="absent" if missing == "subject" else "synthetic"
        ),
        title="Dependent",
        revision=1,
        publication_hash="publication",
        inputs=inputs,
        outputs=[],
    )
    entry = ContentEntry(
        role="record",
        id="dependent",
        kind="analysis",
        content_hash=model_wire_content_hash(record),
    )
    analysis = AnalysisEvidence(
        entry=entry, record=record, published_at=datetime.now(UTC), contents=(entry,)
    )
    destination = tmp_path / "incomplete.scopecat"
    with pytest.raises(ValueError, match="missing from exchange"):
        write_scientific_exchange(
            destination, evidence.model_copy(update={"analyses": (analysis,)}), {}
        )
    assert not destination.exists()


@pytest.mark.parametrize("role", ["record", "artifact", "dataset"])
def test_exchange_requires_indexed_payload_and_its_scientific_identity(
    tmp_path: Path, role: Literal["record", "artifact", "dataset"]
):
    from scopecat.kernel.content_identity import (
        content_fingerprint,
        stable_content_hash,
    )

    raw = b'{"value": 1}'
    payload = tmp_path / "payload"
    payload.write_bytes(raw)
    digest = sha256_content_hash(raw)
    semantic_hash = (
        stable_content_hash(content_fingerprint({"value": 1}))
        if role == "record"
        else digest
    )
    entry = ContentEntry(
        role=role, id="result", kind="result", content_hash=semantic_hash
    )
    evidence = exchange_evidence()
    evidence = evidence.model_copy(
        update={"runs": (evidence.runs[0].model_copy(update={"contents": (entry,)}),)}
    )
    destination = tmp_path / "exchange.scopecat"
    with pytest.raises(ValueError, match="missing retained content"):
        write_scientific_exchange(destination, evidence, {})
    assert not destination.exists()
    reference = PayloadReference(
        owner_kind="run",
        owner_id="synthetic",
        ref=content_entry_ref(entry),
        digest=digest,
        size=len(raw),
    )
    sources = (PayloadSource(reference, payload),)
    wrong = entry.model_copy(update={"content_hash": "sha256:" + "0" * 64})
    conflicting = evidence.model_copy(
        update={"runs": (evidence.runs[0].model_copy(update={"contents": (wrong,)}),)}
    )
    with pytest.raises(ValueError, match="content identity differs"):
        write_scientific_exchange(destination, conflicting, {}, sources)
    assert not destination.exists()
    write_scientific_exchange(destination, evidence, {}, sources)
    with ScientificExchange(destination) as opened:
        opened.verify()
    assert not list(tmp_path.glob(".exchange-*"))


@dataclass(frozen=True)
class PointCount:
    count: int


@analysis_function
def count_capture_points(data: Dataset, *, offset: int = 0) -> PointCount:
    return PointCount(len(data) + offset)


def test_capture_uses_ordinary_analysis_and_retains_execution_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scopecat as sc

    def count_points(data: Dataset) -> int:
        return len(data)

    @dataclass
    class FitPoint:
        x: float
        y: float

    header, appends, _records = recording()
    recording_file = tmp_path / "recording.scopecat"
    write_measurement_snapshot(
        recording_file,
        header,
        appends,
        projection=(
            RecordSelection(point_index=0, acquisition_index=1),
            RecordSelection(point_index=1, acquisition_index=3),
            RecordSelection(point_index=2, acquisition_index=0),
            RecordSelection(point_index=3, acquisition_index=2),
        ),
    )
    with MeasurementSnapshot(recording_file) as snapshot:
        dataset_entry = snapshot.dataset().entry
    evidence = exchange_evidence()
    evidence = evidence.model_copy(
        update={
            "runs": (
                evidence.runs[0].model_copy(update={"contents": (dataset_entry,)}),
            )
        }
    )
    source = tmp_path / "source.scopecat"
    output = tmp_path / "analyzed.scopecat"
    write_scientific_exchange(source, evidence, {"synthetic": recording_file})
    with sc.open_capture(source, output=output) as captured:
        assert captured.run_ids == ("synthetic",)
        ctx = captured.analysis("synthetic", title="Local analysis", key="fit")
        data = ctx.measurements()
        count = ctx.trace(fn=count_points, data=data)
        result = (
            ctx.result()
            .fact("count", count)
            .artifact("report", text="four observations")
            .dataset("fit", [FitPoint(0.0, 2.0), FitPoint(1.0, 3.0)])
            .table(dataset="fit")
            .figure(dataset="fit", kind="line", x="x", y="y")
        )
        saved = result.save()
        assert result.save().id == saved.id
        assert saved.fact("count").value == 4
        assert saved.artifact("report").text() == "four observations"
        assert len(saved.dataset("fit")) == 2
        assert len(saved.executions) == 1
        assert (
            saved.executions[0].input_bindings[0].content_hash
            == dataset_entry.content_hash
        )
        assert output.exists()
        with ScientificExchange(output) as durable:
            assert durable.evidence.analyses[0].published_at == saved.published_at
        counted = captured.analyze(
            "synthetic", count_capture_points(offset=2), key="count"
        )
        assert counted.result_as(PointCount).value == PointCount(6)
        assert (
            captured.analyze(
                "synthetic", count_capture_points(offset=2), key="count"
            ).id
            == counted.id
        )

        def unavailable_destination(_self: Path, _target: str | Path) -> Path:
            raise OSError("destination unavailable")

        with monkeypatch.context() as failure:
            failure.setattr(Path, "replace", unavailable_destination)
            with pytest.raises(OSError, match="destination unavailable"):
                captured.analyze(
                    "synthetic", count_capture_points(offset=999), key="count"
                )
        assert captured.published_analysis("count").result_as(
            PointCount
        ).value == PointCount(6)
        revised = captured.analyze(
            "synthetic", count_capture_points(offset=3), key="count"
        )
        assert revised.revision == 2
    with sc.open_capture(output) as reopened:
        saved = reopened.published_analysis("fit")
        assert saved.fact("count").value == 4
        assert saved.artifact("report").text() == "four observations"
        assert len(saved.dataset("fit")) == 2
        assert len(saved.executions) == 1
        assert saved.inputs[0].content_hash == dataset_entry.content_hash
        counted = reopened.published_analysis("count")
        assert counted.result_as(PointCount).value == PointCount(7)
        assert (
            counted.executions[0].metadata["local_implementation"]
            == count_capture_points(offset=3).implementation_fingerprint
        )
        assert counted.executions[0].metadata["python"] == sys.version
    with ScientificExchange(source) as original, ScientificExchange(output) as analyzed:
        assert original.evidence.runs == analyzed.evidence.runs
        assert len(analyzed.evidence.analyses) == 3
    failed = tmp_path / "failed.scopecat"
    with (
        pytest.raises(RuntimeError, match="analysis interrupted"),
        sc.open_capture(source, output=failed) as captured,
    ):
        ctx = captured.analysis("synthetic")
        ctx.measurements()
        ctx.result().fact("count", 4).save()
        raise RuntimeError("analysis interrupted")
    with ScientificExchange(failed) as retained:
        assert len(retained.evidence.analyses) == 1
    standalone = subprocess.run(  # noqa: S603 - fixed script and test-owned file
        [
            sys.executable,
            "-c",
            """
import sys
import scopecat as sc
with sc.open_capture(sys.argv[1]) as capture:
    assert capture.published_analysis("count").fact("result").value == {"count": 7}
    report = capture.published_analysis("fit").artifact("report").text()
    assert report == "four observations"
for name in sys.modules:
    assert not name.startswith(("scopecat_server", "lab_tools", "lab_adapter"))
""",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert standalone.returncode == 0, standalone.stderr


def test_external_publication_preserves_source_evidence_and_recordings(tmp_path: Path):
    from dataclasses import replace

    from scopecat.analysis.repository import AnalysisPublication
    from scopecat.kernel.content_identity import model_wire_content_hash
    from scopecat.records.analysis import (
        CONFIGURATION_ANALYSIS_INPUT_CODEC,
        AnalysisArtifactRecordOutput,
        AnalysisArtifactReference,
        AnalysisRecord,
        ConfigurationAnalysisRecordInput,
        ProjectAnalysisSubject,
    )
    from scopecat.records.content import BytesWrite, ModelWrite
    from scopecat.runs.refs import CONFIG_PROFILE_SNAPSHOT_REF

    header, appends, records = recording()
    recording_file = tmp_path / "recording.scopecat"
    write_measurement_snapshot(recording_file, header, appends)
    evidence = exchange_evidence()
    source = tmp_path / "source.scopecat"
    write_scientific_exchange(source, evidence, {header.run_id: recording_file})
    original_bytes = source.read_bytes()
    report = b"external fit report"
    artifact = ContentEntry(
        role="artifact",
        id="report",
        kind="analysis_artifact",
        filename="report.txt",
        media_type="text/plain",
        content_hash=sha256_content_hash(report),
    )
    record = AnalysisRecord(
        subject=ProjectAnalysisSubject(),
        title="External fit",
        key="fit",
        revision=1,
        publication_hash="external-fit",
        inputs=[
            ConfigurationAnalysisRecordInput(
                id="config",
                run_id="synthetic",
                target=CONFIG_PROFILE_SNAPSHOT_REF,
                content_hash=evidence.runs[0].snapshot.config_content_hash,
                codec=CONFIGURATION_ANALYSIS_INPUT_CODEC,
                role="configuration",
            )
        ],
        outputs=[
            AnalysisArtifactRecordOutput(
                kind="artifact",
                id="report",
                title="Report",
                content=AnalysisArtifactReference(
                    artifact_id=artifact.id,
                    content_hash=artifact.content_hash,
                    filename="report.txt",
                    media_type="text/plain",
                ),
            )
        ],
    )
    entry = ContentEntry(
        role="record",
        id="analysis-fit-r1",
        kind="analysis",
        content_hash=model_wire_content_hash(record),
    )
    publication = AnalysisPublication(
        subject=record.subject,
        record=entry,
        entries=(entry, artifact),
        analysis_key="fit",
        revision=1,
        publication_hash=record.publication_hash,
        title=record.title,
        step_id=None,
        input_count=1,
        output_count=1,
        models=(ModelWrite(content_entry_ref(entry), record),),
        bytes=(BytesWrite(content_entry_ref(artifact), report),),
    )
    destination = tmp_path / "analyzed.scopecat"
    with ScientificExchange(source) as original:
        original.write_analyses(destination, (publication,))
        with ScientificExchange(destination) as analyzed:
            analyzed.verify()
            assert analyzed.evidence.runs == original.evidence.runs
            assert analyzed.evidence.analyses[0].record == record
            assert (
                analyzed.recording("synthetic").content_hash
                == original.recording("synthetic").content_hash
            )
            assert tuple(analyzed.recording("synthetic").records()) == records
            attachment = next(
                item
                for item in analyzed.payloads
                if item.ref == content_entry_ref(artifact)
            )
            extracted = tmp_path / "report.txt"
            analyzed.copy_payload(attachment, extracted)
            assert extracted.read_bytes() == report
        with pytest.raises(FileExistsError):
            original.write_analyses(destination, (publication,))
        incomplete = tmp_path / "incomplete.scopecat"
        with pytest.raises(ValueError, match="missing retained content"):
            original.write_analyses(incomplete, (replace(publication, bytes=()),))
        assert not incomplete.exists()
    assert source.read_bytes() == original_bytes
    assert not list(tmp_path.glob(".exchange-*"))


def test_exchange_reads_partition_after_borrowed_reader_closes(tmp_path: Path):
    header, appends, records = recording()
    source = tmp_path / "recording.scopecat"
    write_measurement_snapshot(source, header, appends)
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"retained analysis")
    reference = PayloadReference(
        owner_kind="run",
        owner_id=header.run_id,
        ref="artifacts/report/report",
        digest=sha256_content_hash(artifact.read_bytes()),
        size=artifact.stat().st_size,
    )
    destination = tmp_path / "exchange.scopecat"
    evidence = exchange_evidence()
    write_scientific_exchange(
        destination,
        evidence,
        {header.run_id: source},
        (PayloadSource(reference, artifact),),
    )
    source.unlink()
    artifact.unlink()
    with ScientificExchange(destination) as exchange:
        assert exchange.evidence == evidence
        assert exchange.payloads == (reference,)
        with exchange.recording(header.run_id) as snapshot:
            assert tuple(snapshot.records(offset=1, limit=2)) == records[1:3]
        exchange.verify()
        assert tuple(exchange.recording(header.run_id).records()) == records
        saved = tmp_path / "selected report.txt"
        exchange.copy_payload(reference, saved)
        assert saved.read_bytes() == b"retained analysis"
        with pytest.raises(FileExistsError):
            exchange.copy_payload(reference, saved)
        assert saved.read_bytes() == b"retained analysis"
        with pytest.raises(KeyError, match="does not belong"):
            exchange.copy_payload(reference.model_copy(update={"ref": "other"}), saved)
    damaged = tmp_path / "damaged-exchange.scopecat"
    rewrite(
        destination,
        damaged,
        {
            f"objects/{reference.digest.removeprefix('sha256:')}": bytes(
                reference.size
            ),
        },
    )
    with (
        ScientificExchange(damaged) as exchange,
        pytest.raises(ValueError, match="payload checksum"),
    ):
        exchange.verify()
    with ScientificExchange(damaged) as exchange:
        rejected = tmp_path / "rejected.txt"
        with pytest.raises(ValueError, match="payload checksum"):
            exchange.copy_payload(reference, rejected)
        assert not rejected.exists()
    assert not list(tmp_path.glob(".exchange-payload-*"))


def test_exchange_corrupt_payload_fails_without_publication(tmp_path: Path):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"changed")
    reference = PayloadReference(
        owner_kind="run",
        owner_id="synthetic",
        ref="artifacts/report/report",
        digest=sha256_content_hash(b"original"),
        size=8,
    )
    destination = tmp_path / "exchange.scopecat"
    with pytest.raises(ValueError, match="payload changed or is corrupt"):
        write_scientific_exchange(
            destination, exchange_evidence(), {}, (PayloadSource(reference, artifact),)
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".exchange-*"))


def test_exchange_preserves_existing_destination(tmp_path: Path):
    destination = tmp_path / "important.scopecat"
    destination.write_bytes(b"user data")
    with pytest.raises(FileExistsError):
        write_scientific_exchange(destination, exchange_evidence(), {})
    assert destination.read_bytes() == b"user data"
    assert not list(tmp_path.glob(".exchange-*"))


def test_exchange_rejects_changed_evidence_and_unknown_members(tmp_path: Path):
    destination = tmp_path / "exchange.scopecat"
    write_scientific_exchange(destination, exchange_evidence(), {})
    changed = tmp_path / "changed.scopecat"
    with ZipFile(destination) as archive:
        evidence = archive.read("evidence.json")
    rewrite(destination, changed, {"evidence.json": b" " * len(evidence)})
    with pytest.raises(ValueError, match="evidence checksum"):
        ScientificExchange(changed)
    unknown = tmp_path / "unknown.scopecat"
    rewrite(destination, unknown, {"../unexpected": b"inert"})
    with pytest.raises(ValueError, match="unknown members"):
        ScientificExchange(unknown)


def recording():
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(
            columns=(MeasurementPointDomainColumn(id="bias"),)
        ),
        dimensions=(MeasurementDimension(id="point", kind="point", size=4),),
        variables=(
            MeasurementVariable(
                id="bias", role="coordinate", dtype="float64", unit="V", dims=("point",)
            ),
            MeasurementVariable(
                id="iq", role="observable", dtype="complex128", dims=("point",)
            ),
        ),
    )
    header = MeasurementDatasetHeader(
        run_id="synthetic",
        recording_contract_fingerprint="test-contract",
        dataset_schema=schema,
        expected_record_count=4,
        record_count_limit=4,
    )
    records = tuple(
        MeasurementRecord(
            run_id=header.run_id,
            logical_point_id=f"point-{i}",
            point_index=i,
            coordinates={
                "bias": MeasurementScalar.create(dtype="float64", unit="V", value=i)
            },
            observables={
                "iq": MeasurementScalar.create(dtype="complex128", value=complex(i, -i))
            },
        )
        for i in (2, 0, 3, 1)
    )
    appends = tuple(
        MeasurementDatasetAppend(
            run_id=header.run_id,
            header_content_hash=header.content_hash,
            acquisition_start=start,
            records=records[start : start + 2],
        )
        for start in (0, 2)
    )
    return header, appends, records


def test_snapshot_relocates_and_preserves_acquisition_order(tmp_path: Path):
    header, appends, records = recording()
    original = tmp_path / "measurement.scopecat"
    write_measurement_snapshot(original, header, iter(appends))
    moved = original.rename(tmp_path / "独立数据 snapshot.scopecat")
    with MeasurementSnapshot(moved) as snapshot:
        assert snapshot.header == header
        assert snapshot.record_count == 4
        assert tuple(snapshot.records(offset=1, limit=2)) == records[1:3]
        assert tuple(snapshot.records(offset=4)) == ()
        assert tuple(snapshot.records(limit=0)) == ()


def test_failed_export_leaves_existing_destination_untouched(tmp_path: Path):
    header, appends, _ = recording()
    target = tmp_path / "important.scopecat"
    target.write_bytes(b"existing user data")
    with pytest.raises(FileExistsError):
        write_measurement_snapshot(target, header, appends)
    assert target.read_bytes() == b"existing user data"
    target = tmp_path / "failed.scopecat"
    with pytest.raises(ValueError, match="identity/order"):
        write_measurement_snapshot(target, header, reversed(appends))
    assert not target.exists()
    assert not list(tmp_path.glob(".measurement-*"))


def rewrite(source: Path, destination: Path, changes: dict[str, bytes]):
    with ZipFile(source) as original, ZipFile(destination, "w") as rewritten:
        for name in original.namelist():
            rewritten.writestr(name, changes.pop(name, original.read(name)))
        for name, content in changes.items():
            rewritten.writestr(name, content)


def test_range_reads_do_not_read_other_chunks_but_check_selected_content(
    tmp_path: Path,
):
    header, appends, records = recording()
    source = tmp_path / "source.scopecat"
    write_measurement_snapshot(source, header, appends)
    damaged = tmp_path / "damaged.scopecat"
    with ZipFile(source) as archive:
        content = archive.read("chunks/00000001.arrow")
    rewrite(source, damaged, {"chunks/00000001.arrow": bytes(len(content))})
    with MeasurementSnapshot(damaged) as snapshot:
        assert tuple(snapshot.records(limit=2)) == records[:2]
        with pytest.raises(ValueError, match="checksum"):
            tuple(snapshot.records(offset=2, limit=1))


def test_unknown_members_rejected_without_extraction(tmp_path: Path):
    header, appends, _ = recording()
    source = tmp_path / "source.scopecat"
    write_measurement_snapshot(source, header, appends)
    damaged = tmp_path / "damaged.scopecat"
    rewrite(source, damaged, {"../escaped": b"never extract"})
    with pytest.raises(ValueError, match="unknown members"):
        MeasurementSnapshot(damaged)
    assert not (tmp_path / "escaped").exists()


def test_reader_needs_no_server_or_original_project(tmp_path: Path):
    header, appends, records = recording()
    source = tmp_path / "independent.scopecat"
    write_measurement_snapshot(
        source,
        header,
        appends,
        projection=tuple(
            sorted(
                (
                    RecordSelection(
                        point_index=record.point_index, acquisition_index=index
                    )
                    for index, record in enumerate(records)
                ),
                key=lambda item: item.point_index,
            )
        ),
    )
    script = """
import sys
from pathlib import Path
class ForbidServer:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'scopecat_server', 'lab_tools', 'reference_lab', 'scopecat_lab_adapter'
        }:
            raise AssertionError('reading data must not import the execution server')
sys.meta_path.insert(0, ForbidServer())
from scopecat.measurements.archive import MeasurementSnapshot
with MeasurementSnapshot(Path(sys.argv[1])) as snapshot:
    assert [r.point_index for r in snapshot.records()] == [2, 0, 3, 1]
    data = snapshot.dataset().to_xarray()
    assert data['iq'].values.tolist() == [0j, 1-1j, 2-2j, 3-3j]
"""
    subprocess.run(  # noqa: S603 - fixed isolated interpreter and test script
        [sys.executable, "-I", "-c", script, str(source)],
        cwd=tmp_path,
        check=True,
        timeout=30,
    )


def test_partial_recording_keeps_planned_count(tmp_path: Path):
    header, appends, records = recording()
    source = tmp_path / "partial.scopecat"
    write_measurement_snapshot(source, header, appends[:1])
    with MeasurementSnapshot(source) as snapshot:
        assert snapshot.header.expected_record_count == 4
        assert snapshot.record_count == 2
        assert tuple(snapshot.records()) == records[:2]


def test_reacquisition_retains_physical_history_beyond_planned_count(tmp_path: Path):
    header, appends, records = recording()
    retry = MeasurementDatasetAppend(
        run_id=header.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=4,
        records=records[:2],
    )
    path = tmp_path / "retried.scopecat"
    write_measurement_snapshot(path, header, (*appends, retry))
    with MeasurementSnapshot(path) as snapshot:
        assert snapshot.record_count == 6
        assert tuple(snapshot.records()) == (*records, *records[:2])


@pytest.mark.parametrize("point_index", [-1, 4])
def test_export_rejects_point_outside_declared_domain(tmp_path: Path, point_index: int):
    header, _, records = recording()
    append = MeasurementDatasetAppend(
        run_id=header.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=0,
        records=(records[0].model_copy(update={"point_index": point_index}),),
    )
    with pytest.raises(ValueError, match="point index"):
        write_measurement_snapshot(tmp_path / "invalid.scopecat", header, (append,))


def test_analysis_selection_is_explicit_and_preserves_recovery_choice(tmp_path: Path):
    header, appends, records = recording()
    retry = MeasurementDatasetAppend(
        run_id=header.run_id,
        header_content_hash=header.content_hash,
        acquisition_start=4,
        records=(records[1].model_copy(update={"metadata": {"retry": True}}),),
    )
    source = tmp_path / "selected.scopecat"
    # Point zero deliberately retains its earlier acquisition; the later physical
    # retry is retained as evidence, not silently substituted into the analysis.
    selection = (
        RecordSelection(point_index=0, acquisition_index=1),
        RecordSelection(point_index=1, acquisition_index=3),
        RecordSelection(point_index=2, acquisition_index=0),
    )
    write_measurement_snapshot(source, header, (*appends, retry), projection=selection)
    with MeasurementSnapshot(source) as snapshot:
        assert snapshot.record_count == 5
        assert snapshot.selected_record_count == 3
        assert tuple(snapshot.selected_records()) == (
            records[1],
            records[3],
            records[0],
        )
        assert tuple(snapshot.selected_records(offset=1, limit=1)) == (records[3],)
        assert tuple(snapshot.records(offset=4)) == retry.records


def test_absent_analysis_selection_is_not_inferred(tmp_path: Path):
    header, appends, _ = recording()
    source = tmp_path / "unselected.scopecat"
    write_measurement_snapshot(source, header, appends)
    with MeasurementSnapshot(source) as snapshot:
        assert snapshot.selected_record_count is None
        with pytest.raises(ValueError, match="no captured analysis selection"):
            tuple(snapshot.selected_records())


def test_selection_cannot_relabel_a_record(tmp_path: Path):
    header, appends, _ = recording()
    source = tmp_path / "wrong-point.scopecat"
    write_measurement_snapshot(
        source,
        header,
        appends,
        projection=(RecordSelection(point_index=0, acquisition_index=0),),
    )
    with (
        MeasurementSnapshot(source) as snapshot,
        pytest.raises(ValueError, match="another point"),
    ):
        tuple(snapshot.selected_records())


def test_verified_import_is_idempotent_and_conflicts_preserve_existing(tmp_path: Path):
    header, appends, _ = recording()
    source = tmp_path / "source.scopecat"
    write_measurement_snapshot(source, header, appends)
    directory = tmp_path / "library"
    first = import_measurement_snapshot(source, directory)
    assert first.created
    repacked = tmp_path / "repacked.scopecat"
    rewrite(source, repacked, {})
    repeated = import_measurement_snapshot(repacked, directory)
    assert repeated.path == first.path
    assert not repeated.created
    partial = tmp_path / "partial.scopecat"
    write_measurement_snapshot(partial, header, appends[:1])
    with pytest.raises(ValueError, match="different imported content"):
        import_measurement_snapshot(partial, directory)
    with MeasurementSnapshot(first.path) as retained:
        assert retained.record_count == 4
    assert not list(directory.glob(".import-*"))


def test_import_verifies_unselected_chunks_before_publication(tmp_path: Path):
    header, appends, _ = recording()
    source = tmp_path / "source.scopecat"
    write_measurement_snapshot(source, header, appends, projection=())
    with ZipFile(source) as archive:
        content = archive.read("chunks/00000001.arrow")
    damaged = tmp_path / "damaged.scopecat"
    rewrite(source, damaged, {"chunks/00000001.arrow": bytes(len(content))})
    directory = tmp_path / "library"
    with pytest.raises(ValueError, match="checksum"):
        import_measurement_snapshot(damaged, directory)
    assert not list(directory.iterdir())


def test_snapshot_uses_ordinary_dataset_analysis_after_close(tmp_path: Path):
    header, appends, _ = recording()
    source = tmp_path / "analysis.scopecat"
    write_measurement_snapshot(
        source,
        header,
        appends,
        projection=(
            RecordSelection(point_index=0, acquisition_index=1),
            RecordSelection(point_index=1, acquisition_index=3),
            RecordSelection(point_index=2, acquisition_index=0),
            RecordSelection(point_index=3, acquisition_index=2),
        ),
    )
    with MeasurementSnapshot(source) as snapshot:
        dataset = snapshot.dataset()
    values = dataset.to_xarray()
    assert values["iq"].values.tolist() == [0j, 1 - 1j, 2 - 2j, 3 - 3j]
