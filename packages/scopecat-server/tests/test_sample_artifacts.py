from __future__ import annotations

from pathlib import Path

import pytest
from scopecat.records.sample import (
    SampleArtifactRef,
    SampleRevision,
    SampleRevisionDraft,
    sample_revision_content_hash,
)
from scopecat.records.sample_artifact import MAX_SAMPLE_ARTIFACT_BYTES

from scopecat_server.services.sample_artifacts import SampleArtifacts
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore


def revision(artifact: SampleArtifactRef) -> SampleRevision:
    content = SampleRevisionDraft(
        display_name="Synthetic sample", artifacts=(artifact,)
    )
    return SampleRevision(
        sample_id="sample-a",
        revision=1,
        actor="test",
        content=content,
        content_hash=sample_revision_content_hash(
            sample_id="sample-a", content=content
        ),
    )


@pytest.fixture
def artifacts(tmp_path: Path) -> SampleArtifacts:
    objects = ImmutableObjectStore(tmp_path / "objects")
    objects.bootstrap()
    return SampleArtifacts(objects)


@pytest.mark.parametrize(
    "uri",
    [
        "relative.pdf",
        "/etc/passwd",
        "../secret",
        "file:///etc/passwd",
        "project:attachments/diagram.png",
        "javascript:alert(1)",
        "data:text/html,test",
        "//example.com/document",
        "https://user:password@example.com/a",
        "https://example.com/\\evil",
    ],
)
def test_unsupported_references_offer_explicit_import_repair(
    artifacts: SampleArtifacts, uri: str
) -> None:
    ref = SampleArtifactRef(id="a", title="Diagram", uri=uri)
    result = artifacts.resolve(revision(ref), ref)
    assert result.status == "unavailable" and result.url is None
    assert result.repair and "lab.samples.import_artifact" in result.repair


def test_external_links_do_not_claim_snapshot_ownership(
    artifacts: SampleArtifacts,
) -> None:
    ref = SampleArtifactRef(
        id="a", title="Manual", uri="https://example.com/manual.pdf"
    )
    result = artifacts.resolve(revision(ref), ref)
    assert result.status == "external" and result.url == ref.uri
    assert "not included in snapshots" in result.reason


def test_missing_and_corrupt_content_have_different_repair_evidence(
    artifacts: SampleArtifacts,
) -> None:
    ref = artifacts.import_bytes(
        b"diagram notes", artifact_id="a", title="Notes", media_type="text/plain"
    )
    assert artifacts.resolve(revision(ref), ref).status == "stored"
    path = artifacts.objects.path_for(ref.uri)
    path.write_bytes(b"changed notes")
    assert "SHA-256" in artifacts.resolve(revision(ref), ref).reason
    path.unlink()
    assert "missing" in artifacts.resolve(revision(ref), ref).reason


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (b"<script>alert(1)</script>", "text/html"),
        (b"<svg/>", "image/svg+xml"),
        (b"not a png", "image/png"),
        (b"\xff", "text/plain"),
        (b"not json", "application/json"),
        (b'{"x": NaN}', "application/json"),
        (b"\xff", "application/json"),
        (b"x" * (MAX_SAMPLE_ARTIFACT_BYTES + 1), "text/plain"),
    ],
)
def test_import_rejects_unsupported_or_mislabelled_bytes(
    artifacts: SampleArtifacts, content: bytes, media_type: str
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"^(Unsupported attachment media type|Attachment bytes do not match"
            r"|Plain text attachments must|JSON attachments must"
            r"|Sample attachments require)"
        ),
    ):
        artifacts.import_bytes(
            content, artifact_id="a", title="Bad content", media_type=media_type
        )
    assert not tuple(artifacts.objects.root.iterdir())


def test_json_layout_is_preserved_without_text_relabelling(
    artifacts: SampleArtifacts,
) -> None:
    content = b'{"unit": "mm", "sites": [{"id": "a", "x": 1.0, "y": 2.0}]}\n'
    ref = artifacts.import_bytes(
        content, artifact_id="layout", title="Layout", media_type="application/json"
    )
    assert ref.media_type == "application/json"
    assert artifacts.content(ref) == content
    assert artifacts.resolve(revision(ref), ref).status == "stored"
