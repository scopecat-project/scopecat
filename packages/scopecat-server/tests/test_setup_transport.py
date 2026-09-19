from pathlib import Path

from fastapi.testclient import TestClient
from scopecat.daemon.wire import SetupSaveCommand
from scopecat.records.setup import ActiveSetupView, SetupRevision
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server.runtime import LocalDaemonRuntime


def test_setup_transport_saves_immutable_revision_without_selecting(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime:
        client = TestClient(runtime.app())
        original = ActiveSetupView.model_validate(
            client.get("/api/v1/setup/active").json()
        )
        command = SetupSaveCommand(
            revision_id="reviewed/new",
            setup=original.revision.setup,
            actor="operator",
            note="Save for review",
        )
        response = client.post(
            "/api/v1/setup/revisions", json=command.model_dump(mode="json")
        )
        assert response.status_code == 200
        revision = SetupRevision.model_validate(response.json())
        assert revision.id == "reviewed/new"
        assert (
            client.post(
                "/api/v1/setup/revisions", json=command.model_dump(mode="json")
            ).json()
            == response.json()
        )
        assert (
            client.get("/api/v1/setup/revisions/reviewed%2Fnew").json()
            == response.json()
        )
        assert revision.id in {
            item["id"] for item in client.get("/api/v1/setup/revisions").json()["items"]
        }
        assert (
            ActiveSetupView.model_validate(client.get("/api/v1/setup/active").json())
            == original
        )
        assert (
            client.post(
                "/api/v1/setup/revisions",
                json=command.model_copy(
                    update={"note": "different declaration"}
                ).model_dump(mode="json"),
            ).status_code
            == 409
        )
