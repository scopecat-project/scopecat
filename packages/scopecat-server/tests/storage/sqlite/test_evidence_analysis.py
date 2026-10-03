from pathlib import Path

import pytest
from scopecat.analysis.repository import AnalysisPublication
from scopecat.kernel.content_identity import (
    model_wire_content_hash,
    sha256_content_hash,
)
from scopecat.records.analysis import (
    ANALYSIS_ARTIFACT_CODEC,
    AnalysisArtifactRecordOutput,
    AnalysisArtifactReference,
    AnalysisDatasetRecordOutput,
    AnalysisDatasetReference,
    AnalysisFact,
    AnalysisFactRecordOutput,
    AnalysisFigure,
    AnalysisFigureAxis,
    AnalysisFigureLayerView,
    AnalysisFigureProjection,
    AnalysisFigureRecordOutput,
    AnalysisFigureSeries,
    AnalysisFigureView,
    AnalysisPublishedDatasetViewSource,
    AnalysisPublishedOutputReference,
    AnalysisRecord,
    ProjectAnalysisSubject,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
)
from scopecat.records.config import config_content_hash
from scopecat.records.content import BytesWrite, ContentEntry, ModelWrite
from scopecat.records.run_request import RunRequest
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat.runs.admission import build_run_admission
from scopecat.runs.refs import (
    artifact_content_ref,
    dataset_content_ref,
    record_content_ref,
)
from scopecat_testkit.authoring import load_config

from scopecat_server.storage.sqlite.analysis_repository import SQLiteAnalysisRepository
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.evidence_analysis import (
    capture_analysis_evidence,
    capture_analysis_input_graph,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def test_figure_source_retains_dataset_without_declared_input(tmp_path: Path):
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "data.sqlite"), tmp_path / "objects"
    )
    store.bootstrap()
    runs = SQLiteRunRepository(store.sqlite, store.objects.root)
    repository = SQLiteAnalysisRepository(store.sqlite, store.objects.root)
    subject = ProjectAnalysisSubject()
    content = b"retained dataset bytes"
    dataset = ContentEntry(
        role="dataset",
        kind="analysis_dataset",
        id="data",
        content_hash=sha256_content_hash(content),
        produced_by="source",
    )
    reference = AnalysisDatasetReference(
        dataset_id=dataset.id,
        content_hash=dataset.content_hash,
        codec="test.dataset.v1",
    )
    source = AnalysisRecord(
        subject=subject,
        title="Source",
        revision=1,
        publication_hash=sha256_content_hash(b"source"),
        outputs=[
            AnalysisDatasetRecordOutput(
                kind="dataset", id="data", title="Data", content=reference
            ),
        ],
    )
    figure_source = AnalysisPublishedDatasetViewSource(
        source=AnalysisPublishedOutputReference(
            subject=subject, analysis_record_id="source", output_id="data"
        ),
        dataset=reference,
    )
    layer = AnalysisFigureLayerView(
        id="layer",
        source=figure_source,
        projection=AnalysisFigureProjection(kind="line", x="x", y="y"),
        preview=AnalysisFigure(
            kind="line",
            x_axis=AnalysisFigureAxis(label="x"),
            y_axis=AnalysisFigureAxis(label="y"),
            series=[AnalysisFigureSeries(id="curve", x=[0.0], y=[1.0])],
        ),
        total_points=1,
        truncated=False,
    )

    def publish(record_id: str, record: AnalysisRecord) -> None:
        entry = ContentEntry(
            role="record",
            kind="analysis",
            id=record_id,
            content_hash=model_wire_content_hash(record),
        )
        repository.publish(
            AnalysisPublication(
                subject=subject,
                record=entry,
                entries=(entry, dataset) if record_id == "source" else (entry,),
                analysis_key=record_id,
                revision=1,
                publication_hash=record.publication_hash,
                title=record.title,
                step_id=None,
                input_count=0,
                output_count=1,
                models=(
                    ModelWrite(
                        ref=record_content_ref(record_id=record_id, kind="analysis"),
                        value=record,
                    ),
                ),
                bytes=(
                    BytesWrite(
                        ref=dataset_content_ref(
                            dataset_id=dataset.id, kind=dataset.kind
                        ),
                        content=content,
                    ),
                )
                if record_id == "source"
                else (),
            )
        )

    publish("source", source)
    for record_id, selected in (
        ("figure", layer),
        (
            "mismatch",
            layer.model_copy(
                update={
                    "source": figure_source.model_copy(
                        update={
                            "dataset": reference.model_copy(
                                update={
                                    "content_hash": sha256_content_hash(b"different")
                                }
                            ),
                        }
                    )
                }
            ),
        ),
    ):
        record = AnalysisRecord(
            subject=subject,
            title="Figure",
            revision=1,
            publication_hash=sha256_content_hash(record_id.encode()),
            outputs=[
                AnalysisFigureRecordOutput(
                    kind="figure",
                    id="figure",
                    title="Figure",
                    content=AnalysisFigureView(
                        layers=(selected,), total_points=1, truncated=False
                    ),
                )
            ],
        )
        publish(record_id, record)
    with store.sqlite.read_transaction() as connection:
        graph = capture_analysis_input_graph(connection, runs, ((subject, "figure"),))
        assert [item.evidence.entry.id for item in graph] == ["figure", "source"]
        assert any(item.path.read_bytes() == content for item in graph[1].payloads)
        with pytest.raises(ValueError, match="differs from its exact output"):
            capture_analysis_input_graph(connection, runs, ((subject, "mismatch"),))
    store.close()


@pytest.mark.parametrize("run_owned", [False, True])
def test_analysis_capture_keeps_exact_artifact_and_rejects_missing_ref(
    tmp_path: Path,
    run_owned: bool,
):
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "data.sqlite"), tmp_path / "objects"
    )
    store.bootstrap()
    runs = SQLiteRunRepository(store.sqlite, store.objects.root)
    if run_owned:
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
        subject = RunAnalysisSubject(run_id=skeleton.snapshot.run_id)
    else:
        subject = ProjectAnalysisSubject()
    content = b"independent external analysis\n"
    artifact = ContentEntry(
        role="artifact",
        id="analysis-one-notes",
        kind="analysis_artifact",
        content_hash=sha256_content_hash(content),
        produced_by="analysis-one",
        filename="notes.txt",
        media_type="text/plain",
    )
    record = AnalysisRecord(
        subject=subject,
        title="External result",
        key="external",
        revision=1,
        publication_hash=sha256_content_hash(b"publication"),
        outputs=[
            AnalysisArtifactRecordOutput(
                kind="artifact",
                id="notes",
                title="Notes",
                content=AnalysisArtifactReference(
                    artifact_id=artifact.id,
                    content_hash=artifact.content_hash,
                    filename="notes.txt",
                    media_type="text/plain",
                ),
            )
        ],
    )
    entry = ContentEntry(
        role="record",
        kind="analysis",
        id="analysis-one",
        content_hash=model_wire_content_hash(record),
    )
    artifact_ref = artifact_content_ref(artifact_id=artifact.id, kind=artifact.kind)
    publication = AnalysisPublication(
        subject=subject,
        record=entry,
        entries=(entry, artifact),
        analysis_key="external",
        revision=1,
        publication_hash=record.publication_hash,
        title=record.title,
        step_id=None,
        input_count=0,
        output_count=1,
        models=(
            ModelWrite(
                ref=record_content_ref(record_id=entry.id, kind=entry.kind),
                value=record,
            ),
        ),
        bytes=(BytesWrite(ref=artifact_ref, content=content),),
    )
    if isinstance(subject, RunAnalysisSubject):
        runs.publish_analysis(publication)
    else:
        SQLiteAnalysisRepository(store.sqlite, store.objects.root).publish(publication)
    with store.sqlite.read_transaction() as connection:
        captured = capture_analysis_evidence(connection, runs, subject, entry.id)
    assert captured.evidence.record == record
    payload = next(item for item in captured.payloads if item.ref == artifact_ref)
    assert payload.path.read_bytes() == content
    assert payload.digest == artifact.content_hash
    consumed = PublishedAnalysisRecordInput(
        id="upstream",
        kind="analysis_artifact",
        target=artifact.id,
        content_hash=artifact.content_hash,
        codec=ANALYSIS_ARTIFACT_CODEC,
        role="analysis",
        source=AnalysisPublishedOutputReference(
            subject=subject,
            analysis_record_id=entry.id,
            output_id="notes",
        ),
    )
    fact = AnalysisFactRecordOutput(
        kind="fact",
        id="conclusion",
        title="Conclusion",
        content=AnalysisFact(
            schema_id="test",
            schema_codec="scopecat.analysis-fact-schema.v1",
            schema_hash=sha256_content_hash(b"schema"),
            codec="test.fact.v1",
            value=2,
        ),
    )
    derived = AnalysisRecord(
        subject=ProjectAnalysisSubject(),
        title="Derived result",
        key="derived",
        revision=1,
        publication_hash=sha256_content_hash(b"derived"),
        inputs=[consumed],
        outputs=[fact],
    )

    def publish_derived(record_id: str, value: AnalysisRecord) -> None:
        root = ContentEntry(
            role="record",
            kind="analysis",
            id=record_id,
            content_hash=model_wire_content_hash(value),
        )
        SQLiteAnalysisRepository(store.sqlite, store.objects.root).publish(
            AnalysisPublication(
                subject=value.subject,
                record=root,
                entries=(root,),
                analysis_key=record_id,
                revision=1,
                publication_hash=value.publication_hash,
                title=value.title,
                step_id=None,
                input_count=1,
                output_count=len(value.outputs),
                models=(
                    ModelWrite(
                        ref=record_content_ref(record_id=record_id, kind="analysis"),
                        value=value,
                    ),
                ),
                bytes=(),
            )
        )

    publish_derived("derived", derived)
    with store.sqlite.read_transaction() as connection:
        graph = capture_analysis_input_graph(
            connection,
            runs,
            (
                (derived.subject, "derived"),
                (derived.subject, "derived"),
            ),
        )
    assert [item.evidence.entry.id for item in graph] == ["derived", entry.id]
    terminal = derived.model_copy(
        update={
            "inputs": [
                PublishedAnalysisRecordInput(
                    id="conclusion",
                    kind="analysis_fact",
                    target=fact.id,
                    content_hash=f"sha256:{model_wire_content_hash(fact.content)}",
                    codec=fact.content.codec,
                    role="analysis",
                    source=AnalysisPublishedOutputReference(
                        subject=derived.subject,
                        analysis_record_id="derived",
                        output_id=fact.id,
                    ),
                )
            ],
            "publication_hash": sha256_content_hash(b"terminal"),
        }
    )
    publish_derived("terminal", terminal)
    with store.sqlite.read_transaction() as connection:
        graph = capture_analysis_input_graph(
            connection, runs, ((terminal.subject, "terminal"),)
        )
    assert [item.evidence.entry.id for item in graph] == [
        "terminal",
        "derived",
        entry.id,
    ]
    for field, incorrect in (
        ("target", "different"),
        ("content_hash", sha256_content_hash(b"different")),
        ("codec", "different"),
        ("kind", "analysis_dataset"),
    ):
        bad = derived.model_copy(
            update={
                "inputs": [consumed.model_copy(update={field: incorrect})],
                "publication_hash": sha256_content_hash(field.encode()),
            }
        )
        publish_derived(field, bad)
        with (
            store.sqlite.read_transaction() as connection,
            pytest.raises(ValueError, match="differs from its exact output"),
        ):
            capture_analysis_input_graph(connection, runs, ((bad.subject, field),))
    with store.sqlite.write_transaction() as connection:
        if isinstance(subject, RunAnalysisSubject):
            connection.execute(
                "DELETE FROM run_repository_refs WHERE run_id=? AND ref=?",
                (subject.run_id, artifact_ref),
            )
        else:
            connection.execute(
                "DELETE FROM project_analysis_repository_refs WHERE ref=?",
                (artifact_ref,),
            )
    with (
        store.sqlite.read_transaction() as connection,
        pytest.raises(ValueError, match="missing a retained output reference"),
    ):
        capture_analysis_evidence(connection, runs, subject, entry.id)
    with (
        store.sqlite.read_transaction() as connection,
        pytest.raises(ValueError, match="missing a retained output reference"),
    ):
        capture_analysis_input_graph(connection, runs, ((derived.subject, "derived"),))
    store.close()
