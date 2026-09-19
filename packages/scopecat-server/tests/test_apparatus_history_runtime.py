"""Notebook history keeps descriptions and copied evidence without live-state claims."""

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.records.apparatus_history import MAX_APPARATUS_ATTACHMENT_BYTES
from scopecat_testkit.config_registry import load_config

from scopecat_server import LocalDaemonRuntime


def _transport(transport: TestClient) -> httpx2.MockTransport:
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

    return httpx2.MockTransport(send)


def _client(transport: TestClient) -> DaemonClient:
    return DaemonClient("http://testserver", transport=_transport(transport))


def test_notebook_records_room_temperature_history_and_owned_document(
    tmp_path: Path,
) -> None:
    root = tmp_path / "daemon"
    document = tmp_path / "室温测量.txt"
    document.write_bytes(b"room temperature only")
    with (
        LocalDaemonRuntime(root, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        lab = LabClient(_client(transport), operator="Alice")
        line = lab.apparatus.create(
            "line-3", name="Input 3", kind="line", aliases=("L3",)
        )
        attachment = lab.apparatus.import_file(document)
        observation = lab.apparatus.observe(
            line.ref,
            title="Room temperature transmission",
            conditions={"temperature": "room temperature", "connection": "unconfirmed"},
            note="Reference only; not a low-temperature calibration.",
            attachments=(attachment,),
            observation_id="measurement-1",
        )
        document.unlink()
        renamed = lab.apparatus.revise(line.ref, name="Fridge 2 input 3")
        assert renamed.ref.revision == 2
        assert lab.apparatus.resolve(line.ref) == line
        assert lab.apparatus.get("line-3") == renamed
        assert lab.apparatus.search("L3").items == (renamed,)
        assert lab.apparatus.history("line-3").items == (observation,)
        assert lab.apparatus.observation(observation.id).draft.subject == line.ref
        assert observation.draft.actor == "Alice"
        assert observation.draft.observed_at is None
        with pytest.raises(DaemonConflictError):
            lab.apparatus.revise(line.ref, name="stale revision")
        assert (
            lab.apparatus.read_attachment(observation.id, attachment.content_hash)
            == b"room temperature only"
        )
        response = transport.get(
            f"/api/v1/apparatus-observations/{observation.id}/attachments/{attachment.content_hash}"
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"].startswith(
            "attachment; filename*=UTF-8''"
        )
        other = lab.apparatus.observe(line.ref, title="No attachment")
        assert (
            transport.get(
                f"/api/v1/apparatus-observations/{other.id}/attachments/{attachment.content_hash}"
            ).status_code
            == 404
        )
    with (
        LocalDaemonRuntime(root) as restarted,
        TestClient(restarted.app()) as transport,
    ):
        lab = LabClient(_client(transport))
        assert lab.apparatus.history("line-3").items[-1] == observation
        assert (
            lab.apparatus.read_attachment(observation.id, attachment.content_hash)
            == b"room temperature only"
        )


def test_attachment_http_bound_and_safe_download_filename(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        client = _client(transport)
        lab = LabClient(client)
        session = AuthorProject("http://testserver", transport=_transport(transport))
        session.use(operator="Notebook author")
        line = session.apparatus.create("line", name="Line", kind="line")
        assert line.actor == "Notebook author"
        assert session.apparatus.get("line") == line
        # Streaming boundary is tested with a small substituted cap.
        from scopecat_server.http import transport as transport_module

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(transport_module, "MAX_APPARATUS_ATTACHMENT_BYTES", 16)
            assert (
                transport.post(
                    "/api/v1/apparatus-attachments?filename=x", content=b"x" * 17
                ).status_code
                == 413
            )
            assert (
                transport.post(
                    "/api/v1/apparatus-attachments?filename=x", content=b""
                ).status_code
                == 422
            )
        attachment = client.import_apparatus_attachment(
            b"safe", filename='../x"\r\nX-Evil: yes'
        )
        observation = lab.apparatus.observe(
            line.ref, title="Document", attachments=(attachment,)
        )
        response = transport.get(
            f"/api/v1/apparatus-observations/{observation.id}/attachments/{attachment.content_hash}"
        )
        assert response.status_code == 200
        assert "x-evil" not in response.headers
        assert "../" not in response.headers["content-disposition"]
        assert MAX_APPARATUS_ATTACHMENT_BYTES == 64 * 1024 * 1024
