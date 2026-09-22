"""Named histories isolate editing and atomically reject stale branch saves."""

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonConflictError
from scopecat.daemon.wire import ParameterBranchCommitCommand, ParameterSaveCommand
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.scientific_selection import ParameterConfiguration
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.storage.sqlite.parameter_branches import ParameterBranchRepository


def test_branch_history_retries_conflicts_and_reopen(tmp_path: Path) -> None:
    config = load_config()
    first = ParameterBranchCommitCommand(
        name="chip/daily",
        expected_generation=0,
        actor="alice",
        source=ParameterSaveCommand(
            revision_id="first",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
            actor="alice",
        ),
    )
    endpoint = "/api/v1/parameters/branch-commits"
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        created = client.post(endpoint, json=first.model_dump(mode="json"))
        assert created.status_code == 200
        original = ParameterBranch.model_validate(created.json())
        assert original.generation == 1
        fork = first.model_copy(
            update={"name": "chip/trial", "source": original.revision}
        )
        assert (
            client.post(endpoint, json=fork.model_dump(mode="json")).status_code == 200
        )
        second = first.model_copy(
            update={
                "expected_generation": 1,
                "source": first.source.model_copy(update={"revision_id": "second"}),
            }
        )
        response = client.post(endpoint, json=second.model_dump(mode="json"))
        assert response.status_code == 200
        latest = ParameterBranch.model_validate(response.json())
        assert latest.previous == original.revision
        page = client.get("/api/v1/parameters/branches", params={"limit": 1}).json()
        assert page["items"] == [latest.model_dump(mode="json")]
        assert page["next_cursor"] == "chip/daily"
        final = client.get(
            "/api/v1/parameters/branches",
            params={"limit": 1, "after": page["next_cursor"]},
        ).json()
        assert [item["name"] for item in final["items"]] == ["chip/trial"]
        assert final["next_cursor"] is None
        assert client.get("/api/v1/parameters/branches?limit=0").status_code == 422
        assert (
            client.post(endpoint, json=first.model_dump(mode="json")).json()
            == created.json()
        )
        assert (
            client.post(endpoint, json=second.model_dump(mode="json")).json()
            == response.json()
        )
        stale = second.model_copy(
            update={
                "source": first.source.model_copy(update={"revision_id": "loser"}),
            }
        )
        assert (
            client.post(endpoint, json=stale.model_dump(mode="json")).status_code == 409
        )
        assert client.get("/api/v1/parameters/revisions/loser").status_code == 404
        assert (
            client.get("/api/v1/parameters/branches/chip%2Fdaily").json()
            == response.json()
        )
        assert (
            client.get("/api/v1/parameters/branches/chip%2Ftrial").json()["revision"]
            == original.revision.model_dump()
        )
        assert not runtime.application.setup.list()
        assert not runtime.application.config.get_config_registry().entries
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        assert (
            client.get("/api/v1/parameters/branches/chip%2Fdaily").json()
            == response.json()
        )
        history = client.get("/api/v1/parameters/branch-history/chip%2Fdaily").json()[
            "items"
        ]
        assert [item["generation"] for item in history] == [2, 1]
        assert (
            client.post(endpoint, json=second.model_dump(mode="json")).json()
            == response.json()
        )


def test_branch_failure_rolls_back_new_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config()
    command = ParameterBranchCommitCommand(
        name="daily",
        expected_generation=0,
        actor="alice",
        source=ParameterSaveCommand(
            revision_id="not-committed",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
            actor="alice",
        ),
    )

    def abort(*_args: object) -> None:
        raise RuntimeError("abort branch commit")

    with LocalDaemonRuntime(tmp_path) as runtime:
        with monkeypatch.context() as scoped:
            scoped.setattr(ParameterBranchRepository, "append", abort)
            with pytest.raises(RuntimeError, match="abort branch commit"):
                runtime.application.config.commit_parameter_branch(command)
        assert not runtime.application.config.parameter_revisions()
        assert not runtime.application.config.parameter_branch_history("daily")
        assert (
            runtime.application.config.commit_parameter_branch(command).generation == 1
        )


def test_session_checkout_save_and_concurrent_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config()
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
    ):

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

        with AuthorProject(
            "http://testserver", transport=httpx2.MockTransport(send)
        ) as session:
            original = session.parameters.save(
                name="initial",
                catalog=config.parameter_catalog,
                parameters=config.parameter_snapshot,
            )
            session.parameters.create_branch("daily", revision=original)
            lab = LabClient(session)
            setup = lab.setup.save(
                ExecutableSetupSnapshot.from_config(config), name="bench"
            )
            lab.setup.activate(setup)
            session.use(parameter_branch="daily", operator="alice")
            frozen = session.selection
            assert frozen.parameter_branch == "daily"
            other = session.parameters.checkout("daily")
            saved = session.parameter_branch.save(
                catalog=config.parameter_catalog,
                parameters=config.parameter_snapshot,
                note="new calibration",
            )
            assert saved.actor == "alice"
            assert session.selection.science.configuration == ParameterConfiguration(
                ref=saved.revision
            )
            assert frozen.science.configuration == ParameterConfiguration(
                ref=original.ref
            )
            assert session.selection.operator == "alice"
            with pytest.raises(DaemonConflictError, match="branch changed"):
                other.save(
                    catalog=config.parameter_catalog,
                    parameters=config.parameter_snapshot,
                )
            assert len(session.parameters.list()) == 2
            commit = session.commit_parameter_branch

            def lose_response(command: ParameterBranchCommitCommand) -> ParameterBranch:
                commit(command)
                raise ConnectionError("response lost")

            draft = session.parameter_branch
            with monkeypatch.context() as scoped:
                scoped.setattr(session, "commit_parameter_branch", lose_response)
                with pytest.raises(ConnectionError, match="response lost"):
                    draft.save(
                        catalog=config.parameter_catalog,
                        parameters=config.parameter_snapshot,
                    )
            retried = draft.save(
                catalog=config.parameter_catalog, parameters=config.parameter_snapshot
            )
            assert retried.generation == 3
            assert len(session.parameters.list()) == 3
            session.use(parameters=original)
            assert session.selection.parameter_branch is None
            with pytest.raises(ValueError, match="select a parameter branch"):
                _ = session.parameter_branch
            assert not runtime.application.config.get_config_registry().entries
