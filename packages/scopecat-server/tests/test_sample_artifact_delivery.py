from __future__ import annotations

import hashlib
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient
from scopecat.project import load_project
from scopecat.records.sample import SampleArtifactRef, SampleRevisionDraft
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server import LocalDaemonRuntime
from scopecat_server.snapshots import (
    SnapshotError,
    create_snapshot,
    restore_snapshot,
    verify_snapshot,
)

_FIXTURE = Path(__file__).parents[3] / "fixtures/core/sample_artifacts"


def _daemon_client(transport: TestClient) -> DaemonClient:
    def send(request: httpx2.Request) -> httpx2.Response:
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return DaemonClient("http://testserver", transport=httpx2.MockTransport(send))


def test_import_owned_delivery_and_snapshot_restore(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    image = (_FIXTURE / "diagram.png").read_bytes()
    expected = "sha256:" + hashlib.sha256(image).hexdigest()
    with (
        LocalDaemonRuntime(root, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as http,
        LabClient(_daemon_client(http), operator="attachment-test") as lab,
    ):
        diagram = lab.samples.import_artifact(
            image,
            artifact_id="diagram",
            title="Synthetic diagram",
            media_type="image/png",
        )
        assert diagram.uri == expected
        text = lab.samples.import_artifact(
            (_FIXTURE / "notes.txt").read_bytes(),
            artifact_id="notes",
            title="Delivery notes",
            media_type="text/plain",
        )
        document = lab.samples.import_artifact(
            (_FIXTURE / "document.pdf").read_bytes(),
            artifact_id="document",
            title="Synthetic document",
            media_type="application/pdf",
        )
        sample = lab.samples.create(
            "sample-a",
            kind="chip",
            content=SampleRevisionDraft(
                display_name="Synthetic sample", artifacts=(diagram, text, document)
            ),
        )
        lab.samples.create(
            "sample-b",
            kind="chip",
            content=SampleRevisionDraft(display_name="Unrelated sample"),
        )
        delivery = lab.samples.artifacts(sample.id, 1)
        url = delivery.items[0].url
        assert url and delivery.items[0].status == "stored"
        response = http.get(url)
        assert response.status_code == 200 and response.content == image
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]
        assert http.get(url.replace("sample-a", "sample-b")).status_code == 404
        assert http.get(url.replace("/1/", "/2/")).status_code == 404
        assert lab.samples.artifact_content("sample-a", 1, "notes").startswith(
            b"Synthetic"
        )
        pdf = http.get(
            "/api/v1/samples/sample-a/revisions/1/artifacts/document/content"
        )
        assert pdf.content == (_FIXTURE / "document.pdf").read_bytes()
        assert pdf.headers["content-type"] == "application/pdf"
        assert (
            pdf.headers["content-disposition"]
            == 'attachment; filename="sample-attachment.pdf"'
        )
        sample.revise(SampleRevisionDraft(display_name="Synthetic sample updated"))
        assert lab.samples.artifact_content("sample-a", 1, "diagram") == image
    snapshot = tmp_path / "snapshot"
    create_snapshot(load_project(root / "scopecat.toml"), snapshot)
    verify_snapshot(snapshot)
    restored = tmp_path / "restored"
    restore_snapshot(snapshot, restored)
    with LocalDaemonRuntime(restored) as runtime, TestClient(runtime.app()) as http:
        response = http.get(url)
        assert response.status_code == 200 and response.content == image
        assert "sha256:" + hashlib.sha256(response.content).hexdigest() == expected
        page = http.get("/api/v1/samples/sample-a/revisions/1/artifacts").json()
        assert page["items"][0]["artifact"]["uri"] == expected


def test_reference_and_content_boundaries_are_visible_over_http(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as http,
        LabClient(_daemon_client(http)) as lab,
    ):
        missing = SampleArtifactRef(
            id="missing",
            title="Missing",
            uri="sha256:" + "1" * 64,
            media_type="image/png",
        )
        refs = tuple(
            SampleArtifactRef(id=f"bad-{index}", title="Unsupported", uri=uri)
            for index, uri in enumerate(
                (
                    "../secret",
                    "file:///etc/passwd",
                    "project:diagram.svg",
                    "javascript:alert(1)",
                )
            )
        )
        lab.samples.create(
            "sample-a",
            kind="chip",
            content=SampleRevisionDraft(
                display_name="Broken delivery", artifacts=(missing, *refs)
            ),
        )
        page = lab.samples.artifacts("sample-a", 1)
        assert all(
            item.status == "unavailable" and item.url is None and item.repair
            for item in page.items
        )
        assert "missing" in page.items[0].reason
        response = http.get(
            "/api/v1/samples/sample-a/revisions/1/artifacts/missing/content"
        )
        assert response.status_code == 422 and "missing" in response.text
        response = http.post(
            "/api/v1/sample-artifacts",
            params={
                "artifact_id": "script",
                "title": "Script",
                "media_type": "text/html",
            },
            content=b"<script>window.evil=true</script>",
        )
        assert response.status_code == 422


def test_snapshot_checks_owned_objects_but_preserves_unsupported_old_references(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    with (
        LocalDaemonRuntime(root, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as http,
        LabClient(_daemon_client(http)) as lab,
    ):
        owned = lab.samples.import_artifact(
            b"retained notes",
            artifact_id="notes",
            title="Notes",
            media_type="text/plain",
        )
        old = SampleArtifactRef(
            id="old", title="Old reference", uri="file:///missing/outside/document.pdf"
        )
        lab.samples.create(
            "sample-a",
            kind="chip",
            content=SampleRevisionDraft(
                display_name="Mixed references", artifacts=(owned, old)
            ),
        )
    project = load_project(root / "scopecat.toml")
    create_snapshot(project, tmp_path / "complete")
    verify_snapshot(tmp_path / "complete")
    digest = owned.uri.removeprefix("sha256:")
    (root / ".scopecat/objects" / digest[:2] / digest[2:]).unlink()
    with pytest.raises(SnapshotError, match="cannot create snapshot"):
        create_snapshot(project, tmp_path / "incomplete")
    assert not (tmp_path / "incomplete").exists()
