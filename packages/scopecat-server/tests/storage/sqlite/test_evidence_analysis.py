from pathlib import Path

import pytest
from scopecat.analysis.repository import AnalysisPublication
from scopecat.kernel.content_identity import (
    model_wire_content_hash,
    sha256_content_hash,
)
from scopecat.records.analysis import (
    AnalysisArtifactRecordOutput,
    AnalysisArtifactReference,
    AnalysisRecord,
    ProjectAnalysisSubject,
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
from scopecat.runs.refs import artifact_content_ref, record_content_ref
from scopecat_testkit.authoring import load_config

from scopecat_server.storage.sqlite.analysis_repository import SQLiteAnalysisRepository
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.evidence_analysis import capture_analysis_evidence
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


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
    store.close()
