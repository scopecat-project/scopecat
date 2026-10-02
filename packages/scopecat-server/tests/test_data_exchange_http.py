"""Portable data uses the application without requesting a device backend."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scopecat.data_exchange import write_scientific_exchange
from scopecat.data_exchange.models import RunEvidence, ScientificEvidence
from scopecat.records.config import config_content_hash
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
    write_scientific_exchange(source, evidence, {})
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
