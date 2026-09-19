"""The management service owns operations, not arbitrary filesystem paths."""

import json
import secrets
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lab_tools import app_host
from lab_tools.host_client import HostRecord
from lab_tools.host_operations import (
    Command,
    Operation,
    Operations,
    owned_workspace,
    workspaces,
)


def test_operation_identity_busy_guard_and_interruption(tmp_path: Path) -> None:
    store = Operations(tmp_path)
    command = Command(action="open", topic="parameters")
    first, created = store.begin(command)
    assert created
    assert store.begin(command) == (first, False)
    with pytest.raises(ValueError, match="不同请求"):
        store.begin(command.model_copy(update={"topic": "compute"}))
    with pytest.raises(ValueError, match="正在进行"):
        store.begin(Command(action="open", topic="groups"))
    first.created -= 60
    store.save(first)
    reconnected = Operations(tmp_path)
    reconnected.reconcile()
    assert reconnected.get(command.id).status == "interrupted"
    assert reconnected.begin(command)[0].status == "interrupted"


def test_http_boundary_and_managed_inventory(tmp_path: Path, monkeypatch) -> None:
    key = "release-test"
    root = tmp_path / "sandboxes" / key / "parameters" / ("a" * 32)
    root.mkdir(parents=True)
    (root / "author-environment.json").write_text(json.dumps({"kind": "teaching"}))
    (root.parent / "current.json").write_text(json.dumps({"generation": root.name}))
    monkeypatch.setattr(app_host, "teaching_key", lambda _: key)
    record = HostRecord(
        instance="test",
        pid=1,
        process_time=1,
        url="http://127.0.0.1:8912",
        token=secrets.token_urlsafe(32),
        runtime=key,
        python=sys.executable,
    )
    received = []

    def launch(_home, _source, command):
        received.append(command)
        return Operation(command=command, status="starting")

    monkeypatch.setattr(app_host, "launch", launch)
    with TestClient(
        app_host.application(tmp_path, None, record, lambda: None), base_url=record.url
    ) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/state").status_code == 401
        assert (
            client.post(
                "/api/operations", json={"action": "open", "topic": "parameters"}
            ).status_code
            == 401
        )
        assert not received
        client.headers["Authorization"] = f"Bearer {record.token}"
        assert (
            client.get(
                "/api/state", headers={"Origin": "http://elsewhere.test"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/state", headers={"Host": "elsewhere.test"}).status_code
            == 403
        )
        state = client.get("/api/state").json()
        assert len(state["workspaces"]) == 1
        assert state["workspaces"][0]["deletable"] is False
        assert (
            client.post(
                "/api/operations",
                json={"action": "open", "topic": "parameters", "path": str(tmp_path)},
            ).status_code
            == 422
        )
        command = Command(action="open", topic="parameters")
        assert (
            client.post("/api/operations", json=command.model_dump()).status_code == 200
        )
        assert received == [command]
        assert client.post("/api/shutdown").status_code == 200
        assert (
            client.post("/api/operations", json=command.model_dump()).status_code == 409
        )
    with pytest.raises(ValueError, match="未找到"):
        owned_workspace(tmp_path, key, "b" * 32)
    assert workspaces(tmp_path, key)[0].current
