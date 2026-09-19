"""Reading source choices never publishes, imports, or rebinds author code."""

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from scopecat.author_workspaces import LocalAuthorWorkspaces, author_bindings_path
from scopecat.records.author_workspace import AuthorWorkspaceCatalog
from scopecat_testkit.config_registry import load_config

from scopecat_server.author_registration import register_author_workspace
from scopecat_server.lifecycle import initialize_project
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.services.author_revisions import AuthorRevisionService


def _assert_no_author_publication(runtime: LocalDaemonRuntime) -> None:
    manager = runtime.application.author_workspaces
    with manager.store.sqlite.read_connection() as connection:
        for query in (
            "SELECT COUNT(*) FROM author_revisions",
            "SELECT COUNT(*) FROM author_workspace_heads",
            "SELECT COUNT(*) FROM author_workspace_preparations",
            "SELECT COUNT(*) FROM author_workspace_revisions",
        ):
            assert connection.execute(query).fetchone()[0] == 0


def _forbid_source_state(
    _self: AuthorRevisionService, *, initialize: bool = True
) -> None:
    del initialize
    raise AssertionError("catalog listing must not inspect or initialize author state")


def test_catalog_keeps_baselineless_service_available_without_initialization(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        assert runtime.application.author_revisions.baseline is None
        monkeypatch.setattr(AuthorRevisionService, "state", _forbid_source_state)
        _assert_no_author_publication(runtime)
        response = transport.get("/api/v1/author-workspaces")
        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {
                    "id": "legacy",
                    "name": "Original workspace",
                    "available": True,
                    "unavailable_reason": None,
                }
            ]
        }
        _assert_no_author_publication(runtime)


def test_catalog_reports_qualified_rejected_and_retained_unbound_sources_readonly(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    owner = initialize_project(tmp_path / "owner")
    second = initialize_project(tmp_path / "second")
    qualified = register_author_workspace(
        owner.root, second.root, name="Other experiments"
    )
    bindings_path = author_bindings_path(owner.root)
    bindings = LocalAuthorWorkspaces.model_validate_json(bindings_path.read_bytes())
    rejected = qualified.model_copy(
        update={
            "id": "rejected",
            "name": "Wrong environment",
            "python": tmp_path / "missing-python",
        }
    )
    bindings_path.write_text(
        bindings.model_copy(
            update={"items": (*bindings.items, rejected)}
        ).model_dump_json()
    )
    # Source capture is allowed at daemon construction, execution/import is not.
    source = second.root / "src/scopecat_lab/authored/signal.py"
    source.write_text(
        source.read_text() + '\nraise RuntimeError("catalog must not import authors")\n'
    )
    with (
        LocalDaemonRuntime(owner.root) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        manager = runtime.application.author_workspaces
        assert qualified.id in manager.services
        assert "rejected" in manager.unavailable
        with manager.store.sqlite.write_transaction() as connection:
            connection.execute(
                "INSERT INTO author_workspaces VALUES (?,?)",
                ("retained", "Restored source"),
            )
        before_bindings = bindings_path.read_bytes()
        _assert_no_author_publication(runtime)
        monkeypatch.setattr(AuthorRevisionService, "state", _forbid_source_state)
        for workspace_header in ("legacy", "retained"):
            response = transport.get(
                "/api/v1/author-workspaces",
                headers={"X-Scopecat-Workspace": workspace_header},
            )
            assert response.status_code == 200
            catalog = AuthorWorkspaceCatalog.model_validate(response.json())
            by_id = {item.id: item for item in catalog.items}
            assert set(by_id) == {"legacy", qualified.id, "rejected", "retained"}
            assert by_id["legacy"].available
            assert by_id[qualified.id].available
            assert by_id[qualified.id].name == "Other experiments"
            assert by_id[qualified.id].unavailable_reason is None
            assert not by_id["rejected"].available
            assert (
                by_id["rejected"].unavailable_reason == manager.unavailable["rejected"]
            )
            assert not by_id["retained"].available
            assert (
                by_id["retained"].unavailable_reason
                == "Author workspace is not registered for this deployment"
            )
            assert all(
                set(item) == {"id", "name", "available", "unavailable_reason"}
                for item in response.json()["items"]
            )
        assert bindings_path.read_bytes() == before_bindings
        _assert_no_author_publication(runtime)
