"""Read retained host preparation evidence through the normal run repository."""

from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.records.content import ContentEntry, ModelWrite
from scopecat.records.parameter_read import (
    HostParameterEvidence,
    HostParameterEvidenceRecord,
)
from scopecat.runs.refs import record_content_ref
from scopecat.runs.repository import RunContentPublication, RunRepository

HOST_PARAMETER_EVIDENCE_KIND = "host-parameter-evidence"


def host_parameter_evidence_publication(
    run_id: str, segment_id: str, evidence: HostParameterEvidence
) -> RunContentPublication:
    """Prepare immutable content; the caller must enforce its execution fence."""

    record = HostParameterEvidenceRecord(segment_id=segment_id, evidence=evidence)
    fingerprint = stable_content_hash(content_fingerprint(record))
    entry = ContentEntry(
        role="record",
        id=fingerprint,
        kind=HOST_PARAMETER_EVIDENCE_KIND,
        content_hash=fingerprint,
        produced_by="host.input_materialization",
        metadata={"segment_id": segment_id},
    )
    return RunContentPublication(
        run_id=run_id,
        entries=(entry,),
        models=(
            ModelWrite(
                ref=record_content_ref(record_id=entry.id, kind=entry.kind),
                value=record,
                replace=False,
            ),
        ),
    )


def read_host_parameter_evidence(
    repository: RunRepository, run_id: str, record_id: str
) -> HostParameterEvidenceRecord:
    """Read one record from a bounded content page filtered by the host kind."""

    return repository.read_model(
        run_id,
        record_content_ref(record_id=record_id, kind=HOST_PARAMETER_EVIDENCE_KIND),
        HostParameterEvidenceRecord,
    )
