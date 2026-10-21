"""Author discovery never guesses an owner or mutates scientific data."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from filelock import FileLock
from scopecat.application_instance import ApplicationInstance
from scopecat.application_locator import author_location_path, locator_home
from scopecat.author_workspaces import author_bindings_path, author_workspace_id
from scopecat.daemon.endpoint import DaemonEndpointError, resolve_daemon_endpoint
from scopecat.project import open_project
from scopecat.project_sources import capture_sources
from scopecat.runtime_binding import RuntimeBindingError, load_runtime_binding

from scopecat_server.author_registration import register_author_workspace


def application(root: Path) -> ApplicationInstance:
    root.mkdir()
    instance = ApplicationInstance(root)
    (root / "scopecat.application.json").write_text(instance.declaration())
    return instance


def author(root: Path) -> Path:
    root.mkdir()
    (root / "scopecat.toml").write_text("[authors]\n")
    return root


def test_registration_leaves_source_tree_untouched(tmp_path: Path) -> None:
    owner = application(tmp_path / "app")
    root = author(tmp_path / "作者 folder")
    before = capture_sources(open_project(root)).manifest
    registered = register_author_workspace(owner.root, root)
    assert sorted(path.name for path in root.iterdir()) == ["scopecat.toml"]
    assert capture_sources(open_project(root)).manifest == before
    assert author_workspace_id(root) == registered.id
    assert load_runtime_binding(root) == replace(owner.runtime_binding, workspace=root)
    assert register_author_workspace(owner.root, root).id == registered.id
    with pytest.raises(DaemonEndpointError, match="no daemon endpoint") as failure:
        resolve_daemon_endpoint(root)
    assert "open the registered Scopecat application" in str(failure.value)
    assert "Settings → Author code" in str(failure.value)
    assert not (root / ".scopecat").exists()


def test_multiple_targets_keep_default_and_missing_default_fails_closed(
    tmp_path: Path,
) -> None:
    owner = application(tmp_path / "app")
    other = application(tmp_path / "other")
    root = author(tmp_path / "author")
    register_author_workspace(owner.root, root)
    saved = author_location_path(root).read_bytes()
    second = register_author_workspace(other.root, root)
    assert author_workspace_id(root, target=other.root) == second.id
    assert author_location_path(root).read_bytes() == saved
    owner.root.rename(tmp_path / "offline")
    with pytest.raises(RuntimeBindingError, match="unavailable"):
        load_runtime_binding(root)
    assert not owner.root.exists()
    assert (
        load_runtime_binding(root, target=other.root).data_root
        == other.runtime_binding.data_root
    )
    assert not (root / ".scopecat").exists()


def test_ambiguous_or_replaced_registry_fails_closed(tmp_path: Path) -> None:
    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    register_author_workspace(owner.root, root)
    path = author_bindings_path(owner.root)
    registry = json.loads(path.read_text())
    registry["items"].append(registry["items"][0].copy())
    path.write_text(json.dumps(registry))
    with pytest.raises(RuntimeBindingError, match="uniquely registered"):
        load_runtime_binding(root)
    registry["items"] = registry["items"][:1]
    registry["items"][0]["id"] = "different-owner"
    path.write_text(json.dumps(registry))
    with pytest.raises(RuntimeBindingError, match="uniquely registered"):
        load_runtime_binding(root)


def test_explicit_worktree_override_requires_registration_and_agreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = application(tmp_path / "app")
    other = application(tmp_path / "other")
    root = author(tmp_path / "author")
    register_author_workspace(owner.root, root)
    monkeypatch.setenv("SCOPECAT_APPLICATION_ROOT", str(other.root))
    with pytest.raises(RuntimeBindingError, match="Cannot verify"):
        load_runtime_binding(root)
    monkeypatch.setenv("SCOPECAT_APPLICATION_ROOT", str(owner.root))
    assert author_workspace_id(root)
    author_location_path(root).unlink()
    assert author_workspace_id(root)
    monkeypatch.delenv("SCOPECAT_APPLICATION_ROOT")
    with pytest.raises(DaemonEndpointError, match="no daemon endpoint"):
        resolve_daemon_endpoint(root)
    assert not (root / ".scopecat").exists()


def test_legacy_binding_preserved_and_all_paths_checked(tmp_path: Path) -> None:
    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    legacy = root / "scopecat.runtime.toml"
    binding = owner.runtime_binding
    text = "[runtime]\n" + "".join(
        f"{field} = {json.dumps(str(getattr(binding, field)))}\n"
        for field in ("data_root", "deployment_root", "state_root", "cache_root")
    )
    legacy.write_text(text)
    registered = register_author_workspace(owner.root, root)
    assert legacy.read_text() == text
    assert author_workspace_id(root) == registered.id
    legacy.write_text(
        text.replace(
            json.dumps(str(binding.cache_root)),
            json.dumps(str(tmp_path / "custom-cache")),
        )
    )
    with pytest.raises(RuntimeBindingError, match="conflicts"):
        load_runtime_binding(root)
    with pytest.raises(ValueError, match="different explicit runtime binding"):
        register_author_workspace(owner.root, root)
    assert legacy.exists()


def test_existing_independent_locks_and_data_are_preserved(tmp_path: Path) -> None:
    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    old = root / ".scopecat"
    old.mkdir()
    for name in ("deployment.lock", "daemon.lock"):
        with (
            FileLock(old / name, preserve_lock_file=True),
            pytest.raises(ValueError, match="running application"),
        ):
            register_author_workspace(owner.root, root)
        assert (old / name).exists()
    database = old / "control.sqlite3"
    database.write_bytes(b"old scientific data")
    with pytest.raises(ValueError, match="own scientific store"):
        register_author_workspace(owner.root, root)
    assert database.read_bytes() == b"old scientific data"
    assert not author_location_path(root).exists()


def test_locator_platform_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SCOPECAT_LOCATOR_HOME")
    monkeypatch.setattr("scopecat.application_locator.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local AppData"))
    assert locator_home() == tmp_path / "Local AppData/Scopecat/author-locations"
    monkeypatch.delenv("LOCALAPPDATA")
    with pytest.raises(RuntimeBindingError, match="LOCALAPPDATA"):
        locator_home()
    monkeypatch.setattr("scopecat.application_locator.sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert locator_home() == tmp_path / "xdg/Scopecat/author-locations"
    monkeypatch.setenv("SCOPECAT_LOCATOR_HOME", "relative")
    with pytest.raises(RuntimeBindingError, match="absolute"):
        locator_home()


def test_registration_fences_concurrent_independent_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from filelock import Timeout

    from scopecat_server.runtime import LocalDaemonRuntime

    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")

    def race():
        with pytest.raises(Timeout):
            LocalDaemonRuntime(root)
        assert not (root / ".scopecat").exists()
        return uuid4()

    monkeypatch.setattr("scopecat_server.author_registration.uuid4", race)
    register_author_workspace(owner.root, root)
    with pytest.raises(ValueError, match="service workspace"):
        LocalDaemonRuntime(root)


@pytest.mark.parametrize("failure", ["registry", "final-locator"])
def test_interrupted_registration_retains_owner_fence_and_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from scopecat.application_locator import read_author_location

    owner = application(tmp_path / "app")
    other = application(tmp_path / "other")
    root = author(tmp_path / "author")
    locator = author_location_path(root)
    registry = author_bindings_path(owner.root)
    original_replace = Path.replace
    locator_writes = 0

    def fail_replace(path: Path, target: Path) -> Path:
        nonlocal locator_writes
        if target == locator:
            locator_writes += 1
        if (failure == "registry" and target == registry) or (
            failure == "final-locator" and target == locator and locator_writes == 2
        ):
            raise OSError("interrupted registration")
        return original_replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="interrupted"):
            register_author_workspace(owner.root, root)
    pending = read_author_location(root)
    assert pending is not None and pending.pending
    with pytest.raises(RuntimeBindingError, match="incomplete"):
        load_runtime_binding(root)
    alternative = register_author_workspace(other.root, root)
    assert author_workspace_id(root, target=other.root) == alternative.id
    assert read_author_location(root) == pending
    resumed = register_author_workspace(owner.root, root)
    assert resumed.id == pending.workspace_id
    assert author_workspace_id(root) == resumed.id


def test_data_identity_is_distinct_from_location_and_author_id(tmp_path: Path) -> None:
    import sqlite3

    from scopecat.application_locator import read_author_location, read_data_identity

    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    registered = register_author_workspace(owner.root, root)
    location = read_author_location(root)
    assert location is not None
    assert location.project_id == read_data_identity(owner.runtime_binding.data_root)
    assert location.workspace_id == registered.id
    # Replacing data at the same path is a different space even if the machine's
    # author registry survived. Neither connection nor re-registration adopts it.
    with sqlite3.connect(owner.runtime_binding.data_root / "control.sqlite3") as db:
        db.execute("UPDATE project_identity SET identity = 'local:replacement'")
    with pytest.raises(RuntimeBindingError, match="identity has changed"):
        load_runtime_binding(root)
    with pytest.raises(ValueError, match="identity has changed"):
        register_author_workspace(owner.root, root)
    assert read_author_location(root) == location


def test_endpoint_must_match_data_identity_and_author_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx2
    from scopecat.application_locator import read_data_identity

    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    registered = register_author_workspace(owner.root, root)
    health = {
        "status": "ok",
        "project_id": read_data_identity(owner.runtime_binding.data_root),
        "deployment_id": "deployment",
        "project_name": "application",
        "project_root": str(owner.root),
        "data_root": str(owner.runtime_binding.data_root),
        "deployment_root": str(owner.runtime_binding.deployment_root),
        "author_workspaces": {str(root): registered.id},
    }
    client = httpx2.Client
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=health))

    def client_factory(*, timeout: float, trust_env: bool) -> httpx2.Client:
        return client(transport=transport, timeout=timeout, trust_env=trust_env)

    monkeypatch.setattr("scopecat.daemon.endpoint.httpx2.Client", client_factory)
    assert resolve_daemon_endpoint(root, explicit="http://localhost:9000")
    health["project_id"] = "other-space"
    with pytest.raises(DaemonEndpointError, match="another binding"):
        resolve_daemon_endpoint(root, explicit="http://localhost:9000")
    health["project_id"] = read_data_identity(owner.runtime_binding.data_root)
    health["author_workspaces"] = {str(root): "different-author"}
    with pytest.raises(DaemonEndpointError, match="another binding"):
        resolve_daemon_endpoint(root, explicit="http://localhost:9000")


def test_explicit_application_without_locator_still_checks_health_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx2

    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    registered = register_author_workspace(owner.root, root)
    author_location_path(root).unlink()
    monkeypatch.setenv("SCOPECAT_APPLICATION_ROOT", str(owner.root))
    health = {
        "status": "ok",
        "project_id": "wrong-space",
        "deployment_id": "deployment",
        "project_name": "app",
        "project_root": str(owner.root),
        "data_root": str(owner.runtime_binding.data_root),
        "deployment_root": str(owner.runtime_binding.deployment_root),
        "author_workspaces": {str(root): registered.id},
    }
    client = httpx2.Client
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=health))

    def client_factory(*, timeout: float, trust_env: bool) -> httpx2.Client:
        return client(transport=transport, timeout=timeout, trust_env=trust_env)

    monkeypatch.setattr("scopecat.daemon.endpoint.httpx2.Client", client_factory)
    with pytest.raises(DaemonEndpointError, match="another binding"):
        resolve_daemon_endpoint(root, explicit="http://localhost:9000")


@pytest.mark.parametrize("remnant", ["registry", "objects"])
def test_missing_database_in_existing_owner_never_initializes_new_space(
    tmp_path: Path, remnant: str
) -> None:
    owner = application(tmp_path / "app")
    first = author(tmp_path / "first")
    second = author(tmp_path / "second")
    if remnant == "registry":
        register_author_workspace(owner.root, first)
        database = owner.runtime_binding.data_root / "control.sqlite3"
        database.rename(tmp_path / "preserved.sqlite3")
    else:
        (owner.runtime_binding.data_root / "objects").mkdir(parents=True)
    with pytest.raises(ValueError, match="database is missing"):
        register_author_workspace(owner.root, second)
    assert not (owner.runtime_binding.data_root / "control.sqlite3").exists()
    assert not author_location_path(second).exists()


def test_initial_owner_registration_preserves_existing_ownership_locks(
    tmp_path: Path,
) -> None:
    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    paths = (
        owner.runtime_binding.deployment_root / "deployment.lock",
        owner.runtime_binding.data_root / "daemon.lock",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    register_author_workspace(owner.root, root)
    assert all(path.exists() for path in paths)


def test_explicit_target_survives_corrupt_default_and_retains_identity(
    tmp_path: Path,
) -> None:
    import sqlite3

    owner = application(tmp_path / "app")
    other = application(tmp_path / "other")
    root = author(tmp_path / "author")
    register_author_workspace(owner.root, root)
    register_author_workspace(other.root, root)
    author_location_path(root).write_text("invalid default")
    assert (
        load_runtime_binding(root, target=other.root).data_root
        == other.runtime_binding.data_root
    )
    with pytest.raises(RuntimeBindingError, match="Cannot read"):
        load_runtime_binding(root)
    with sqlite3.connect(other.runtime_binding.data_root / "control.sqlite3") as db:
        db.execute("UPDATE project_identity SET identity = 'replacement'")
    with pytest.raises(RuntimeBindingError, match="identity has changed"):
        load_runtime_binding(root, target=other.root)


def test_pinned_requests_reject_restarted_service_before_dispatch(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from scopecat_server.http.transport import create_app
    from scopecat_server.runtime import LocalDaemonRuntime

    owner = application(tmp_path / "app")
    root = author(tmp_path / "author")
    entry = register_author_workspace(owner.root, root)
    with LocalDaemonRuntime(owner.root) as runtime:
        first = TestClient(create_app(runtime.application))
        response = first.get("/api/v1/health")
        headers = {
            "X-Scopecat-Instance-ID": response.headers["X-Scopecat-Instance-ID"],
            "X-Scopecat-Project-ID": response.json()["project_id"],
            "X-Scopecat-Workspace": entry.id,
        }
        assert first.get("/api/v1/health", headers=headers).status_code == 200
        # A write to a nonexistent route would be 404 if it reached dispatch.
        assert (
            first.post(
                "/not-dispatched", headers={**headers, "X-Scopecat-Project-ID": "other"}
            ).status_code
            == 409
        )
        second = TestClient(create_app(runtime.application))
        assert second.post("/not-dispatched", headers=headers).status_code == 409
        assert (
            second.get("/api/v1/health").json()["project_id"]
            == headers["X-Scopecat-Project-ID"]
        )


def test_pinned_client_does_not_replay_uncertain_write() -> None:
    import httpx2
    from scopecat.daemon.client import DaemonClient
    from scopecat.records.launch_request import LaunchRequest

    writes = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal writes
        writes += 1
        raise httpx2.ReadError("uncertain", request=request)

    with DaemonClient(
        "http://localhost", transport=httpx2.MockTransport(respond)
    ) as client:
        client.session_project_id = "pinned"
        with pytest.raises(httpx2.ReadError, match="uncertain"):
            client._post_idempotent_response(
                "/write", LaunchRequest(action="list", workspace_id="author")
            )
    assert writes == 1


@pytest.mark.parametrize("failure", ["registry", "receipt"])
def test_alternative_registration_failure_does_not_block_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from scopecat.application_locator import author_target_path, read_author_location

    owner = application(tmp_path / "app")
    other = application(tmp_path / "other")
    root = author(tmp_path / "author")
    first = register_author_workspace(owner.root, root)
    saved = author_location_path(root).read_bytes()
    receipt = author_target_path(root, other.root)
    registry = author_bindings_path(other.root)
    replace_path = Path.replace
    writes = 0

    def interrupt(path: Path, target: Path) -> Path:
        nonlocal writes
        if target == receipt:
            writes += 1
        if (failure == "registry" and target == registry) or (
            failure == "receipt" and target == receipt and writes == 2
        ):
            raise OSError("interrupted target")
        return replace_path(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            register_author_workspace(other.root, root)
    pending = read_author_location(root, other.root)
    assert pending is not None and pending.pending
    assert author_workspace_id(root) == first.id
    with pytest.raises(RuntimeBindingError, match="incomplete"):
        load_runtime_binding(root, target=other.root)
    assert register_author_workspace(other.root, root).id == pending.workspace_id
    assert author_location_path(root).read_bytes() == saved


def test_pinned_job_reconnect_requires_same_store_source_and_receipt_root(
    tmp_path: Path,
) -> None:
    from scopecat.application.author_project import AuthorJob, AuthorProject
    from scopecat.records.launch_request import LaunchRequest

    with (
        AuthorProject(
            "http://localhost", workspace_id="author", receipts=tmp_path / "a"
        ) as first,
        AuthorProject(
            "http://localhost", workspace_id="author", receipts=tmp_path / "a"
        ) as restarted,
        AuthorProject(
            "http://localhost", workspace_id="author", receipts=tmp_path / "b"
        ) as other,
    ):
        first.session_project_id = restarted.session_project_id = "space-a"
        other.session_project_id = "space-b"
        job = AuthorJob.retain(
            first,
            tmp_path / "a/job.json",
            LaunchRequest(action="list", workspace_id="author"),
        )
        first.close()
        assert job.reconnect(restarted).request == job.request
        with pytest.raises(ValueError, match="another data space"):
            job.reconnect(other)
        with pytest.raises(ValueError, match="does not belong"):
            other.reopen(job.receipt)


def test_concurrent_target_registration_serializes_default_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    owner = application(tmp_path / "app")
    other = application(tmp_path / "other")
    root = author(tmp_path / "author")

    def during_first_commit():
        with pytest.raises(ValueError, match="busy"):
            register_author_workspace(other.root, root)
        return uuid4()

    with monkeypatch.context() as patch:
        patch.setattr("scopecat_server.author_registration.uuid4", during_first_commit)
        first = register_author_workspace(owner.root, root)
    second = register_author_workspace(other.root, root)
    assert author_workspace_id(root) == first.id
    assert author_workspace_id(root, target=other.root) == second.id


def test_worker_context_overrides_inherited_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat_server.worker_environment import author_worker_environment

    monkeypatch.setenv("SCOPECAT_DAEMON_URL", "http://wrong")
    monkeypatch.setenv("SCOPECAT_APPLICATION_ROOT", str(tmp_path / "default"))
    environment = author_worker_environment(tmp_path / "selected")
    assert environment["SCOPECAT_APPLICATION_ROOT"] == str(tmp_path / "selected")
    assert "SCOPECAT_DAEMON_URL" not in environment
    assert "SCOPECAT_APPLICATION_ROOT" not in author_worker_environment(None)


def test_notebook_pin_requires_lifetime_protocol(tmp_path: Path) -> None:
    import httpx2
    from scopecat.daemon.client import DaemonClient

    health = {
        "status": "ok",
        "project_id": "space",
        "deployment_id": "deployment",
        "project_name": "app",
        "project_root": str(tmp_path),
        "data_root": str(tmp_path / "data"),
        "deployment_root": str(tmp_path / "state"),
        "author_workspaces": {str(tmp_path): "author"},
    }
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=health))
    with DaemonClient(
        "http://localhost", workspace_id="author", transport=transport
    ) as client:
        with pytest.raises(ValueError, match="incompatible service"):
            client.pin_session(project_id="space", source_root=tmp_path)
        assert client.session_project_id is None
