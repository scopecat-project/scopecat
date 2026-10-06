"""Application ownership is independent of source folders and live interpreters."""

import sys
from pathlib import Path

import psutil
import pytest
from filelock import FileLock

from lab_tools import application_runtime
from lab_tools.application_runtime import ApplicationRuntime
from scopecat.author_workspaces import LocalAuthorWorkspaces, author_bindings_path
from scopecat.project import load_captured_project, open_project
from scopecat.project_sources import capture_sources, materialize_sources
from scopecat_server.lifecycle import write_daemon_endpoint_record


def test_foreground_source_development_owns_and_stops_its_application(tmp_path):
    from lab_tools.dev import development_session
    from scopecat.project import open_project
    from scopecat_server.lifecycle import inspect_daemon

    home = tmp_path / "source development"
    with development_session(home) as record:
        assert record.base_url.startswith("http://127.0.0.1:")
        assert not (home / "installation.json").exists()
        assert not (home / "releases").exists()
        with (
            pytest.raises(RuntimeError, match="developer failure"),
            development_session(tmp_path / "another"),
        ):
            raise RuntimeError("developer failure")
    assert inspect_daemon(open_project(home / "runtime")).state == "stopped"
    assert not (home / "authors/scopecat.runtime.toml").exists()
    assert inspect_daemon(open_project(tmp_path / "another/runtime")).state == "stopped"
    assert not (tmp_path / "another/authors/scopecat.runtime.toml").exists()
    # Editable overlays can use an ephemeral interpreter. A later launch must
    # explicitly register its current interpreter rather than reusing that path.
    location = author_bindings_path(home / "runtime")
    registry = LocalAuthorWorkspaces.model_validate_json(location.read_bytes())
    previous = registry.items[0].model_copy(update={"python": tmp_path / "gone/python"})
    location.write_text(
        registry.model_copy(update={"items": (previous,)}).model_dump_json()
    )
    with development_session(home):
        current = LocalAuthorWorkspaces.model_validate_json(location.read_bytes())
        assert current.items[0].python == Path(sys.executable)


@pytest.fixture
def application(tmp_path: Path):
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>application</html>")
    runtime = ApplicationRuntime(tmp_path / "home")
    runtime.configure(static_dir=gui)
    yield runtime
    runtime.stop()


def test_development_source_registration_does_not_activate_drivers(tmp_path: Path):
    import httpx2

    from lab_tools.dev import development_session

    source = tmp_path / "driver-source"
    (source / "src").mkdir(parents=True)
    (source / "scopecat.toml").write_text(
        '[lab]\ninstrument_backend = "vendor_driver:create_backend"\n'
        '[authors]\nsource_roots = ["src"]\ndependencies = []\n'
    )
    (source / "src/vendor_driver.py").write_text(
        'raise RuntimeError("vendor environment is not prepared")\n'
    )
    with development_session(tmp_path / "development", workspace=source) as record:
        with httpx2.Client(base_url=record.base_url, trust_env=False) as client:
            assert client.get("/api/v1/health").status_code == 200
            selected = client.get("/api/v1/devices/driver-source")
            assert selected.status_code == 200
            assert selected.json() == {"active": None}
        assert (
            LocalAuthorWorkspaces.model_validate_json(
                author_bindings_path(record.project_root).read_bytes()
            )
            .items[0]
            .root
            == source
        )


def test_idle_exit_uses_live_service_and_releases_ownership(application):
    application.start()
    assert not application.activity().busy
    assert application.stop_if_idle()
    assert application.status().state == "stopped"


def test_installation_qualification_does_not_load_driver_environment(application):
    before = application.installation()
    composition = (
        '[lab]\ninstrument_backend = "unavailable_vendor_sdk:create_backend"\n'
        "[authors]\ndependencies = []\n"
    )
    candidate = application.qualify(
        before.python, before.static_dir, composition=composition
    )
    assert candidate.composition == composition
    assert candidate.environment == before.environment
    assert application.installation() == before
    assert application.status().state == "stopped"


def test_unavailable_author_folder_does_not_block_application_update(
    application, tmp_path
):
    import httpx2

    source = tmp_path / "author"
    (source / "src").mkdir(parents=True)
    (source / "scopecat.toml").write_text(
        '[authors]\nsource_roots=["src"]\nrefresh_roots=["src"]\n'
        'modules=["experiment"]\ndependencies=[]\n'
    )
    (source / "src/experiment.py").write_text('name = "experiment"\n')
    application.register_source(source)
    source.rename(tmp_path / "moved-author")
    before = application.installation()
    candidate = application.qualify(before.python, before.static_dir)
    application.select(candidate)
    record = application.start()
    with httpx2.Client(trust_env=False) as client:
        response = client.get(record.base_url + "/api/v1/health")
        assert response.status_code == 200
    assert not source.exists()


def test_select_existing_environment_validates_before_publishing(
    application, tmp_path, monkeypatch
):
    source = tmp_path / "author"
    (source / "src").mkdir(parents=True)
    (source / "scopecat.toml").write_text(
        '[authors]\nsource_roots=["src"]\nrefresh_roots=["src"]\n'
        'modules=["experiment"]\ndependencies=[]\n'
    )
    (source / "src/experiment.py").write_text('name = "experiment"\n')
    application.register_source(source)
    location = author_bindings_path(application.root)
    original = LocalAuthorWorkspaces.model_validate_json(location.read_bytes()).items[0]
    python = tmp_path / "environment/python"
    python.parent.mkdir()
    python.symlink_to(sys.executable)
    checked = []

    def capture(root, interpreter):
        checked.append((root, interpreter))

    monkeypatch.setattr("scopecat_server.author_environment.capture", capture)
    monkeypatch.chdir(tmp_path)
    application.select_source_environment(Path("author"), Path("environment/python"))
    selected = LocalAuthorWorkspaces.model_validate_json(location.read_bytes()).items[0]
    assert selected.python == python
    assert selected.retained_pythons == (original.python,)
    assert checked == [(source, python)]
    before = location.read_bytes()

    def fail(*args, **kwargs):
        raise ValueError("incompatible execution environment")

    monkeypatch.setattr("scopecat_server.author_environment.capture", fail)
    with pytest.raises(ValueError, match="incompatible"):
        application.select_source_environment(source, tmp_path / "invalid/python")
    assert location.read_bytes() == before


def test_two_sources_share_empty_application_without_owning_it(application, tmp_path):
    import httpx2

    from scopecat.daemon.endpoint import resolve_daemon_endpoint

    running = application.start()
    sources = []
    for name in ("first", "second"):
        root = tmp_path / name
        (root / "src").mkdir(parents=True)
        (root / "scopecat.toml").write_text(
            '[authors]\nsource_roots=["src"]\nrefresh_roots=["src"]\n'
            'modules=["experiment"]\ndependencies=[]\n'
        )
        (root / "src/experiment.py").write_text(f"name = {name!r}\n")
        identity = application.register_source(root)
        assert resolve_daemon_endpoint(root) == running.base_url
        assert application.source(root) == identity
        sources.append((root, identity))
        with httpx2.Client(base_url=running.base_url, trust_env=False) as client:
            response = client.get("/api/v1/author-workspaces")
            response.raise_for_status()
            assert identity in {item["id"] for item in response.json()["items"]}
    assert sources[0][1] != sources[1][1]
    record = application.start()
    assert record.pid == running.pid
    assert application.start() == record
    assert record.project_root == application.root
    assert all(
        open_project(root).runtime_binding.data_root == record.data_root
        for root, _ in sources
    )
    bundle = capture_sources(open_project(sources[0][0]))
    archived = materialize_sources(bundle, tmp_path / "archive")
    application.stop()
    # Captured sources do not consult a current source registration on reopen.
    assert load_captured_project(archived).author_only
    assert load_captured_project(archived).lab_adapter is None
    assert application.start().data_root == record.data_root


def test_killed_owner_reconciles_without_touching_data(application):
    record = application.start()
    evidence = record.data_root / "retained-evidence"
    evidence.write_bytes(b"retain")
    process = psutil.Process(record.pid)
    process.kill()
    process.wait(timeout=10)
    assert application.status().state == "stale"
    with FileLock(record.data_root / "daemon.lock"):
        with pytest.raises(ValueError, match="仍在运行或更新中"):
            application.select(application.installation())
        assert application.status().state == "stale"
    application.select(application.installation())
    assert application.status().state == "stopped"
    assert evidence.read_bytes() == b"retain"
    assert application.start().data_root == record.data_root


def test_conflicting_interpreter_has_explicit_stop_recovery(application):
    record = application.start()
    assert record.python == Path(sys.executable).absolute()
    write_daemon_endpoint_record(record.model_copy(update={"python": None}))
    with pytest.raises(ValueError, match="停止后台并重新启动"):
        application.start()
    application.stop()
    assert application.start().python == Path(sys.executable).absolute()


def test_failed_candidate_does_not_replace_selected_runtime(application):
    before = application.installation()
    application.start()
    with pytest.raises(ValueError, match="仍在运行或更新中"):
        application.select(before)
    assert application.installation() == before
    application.stop()
    changed = before.model_copy(update={"adapter_identity": "changed"})
    with pytest.raises(ValueError, match="应用文件在检查后改变"):
        application.select(changed)
    assert application.installation() == before
    assert not application.pending.exists()


def test_missing_candidate_capability_preserves_running_application(application):
    before = application.installation()
    record = application.start()
    declaration = (application.root / "scopecat.toml").read_bytes()
    with pytest.raises(ValueError, match="missing-candidate"):
        application.qualify(
            before.python,
            before.static_dir,
            composition='[lab.adapter]\ndistribution="missing-candidate"\n'
            'manifest="missing/adapter.toml"\n',
        )
    assert application.status().record == record
    assert application.installation() == before
    assert (application.root / "scopecat.toml").read_bytes() == declaration


def test_interrupted_selection_is_fenced_and_retryable(application, monkeypatch):
    before = application.installation()
    candidate = application.qualify(
        before.python,
        before.static_dir,
        composition=before.composition + "# candidate\n",
    )
    write = application_runtime.write_state

    def fail_selection(path, content):
        if path == application.selection:
            raise OSError("interrupted selection")
        write(path, content)

    with monkeypatch.context() as patch:
        patch.setattr(application_runtime, "write_state", fail_selection)
        with pytest.raises(OSError, match="interrupted selection"):
            application.select(candidate)
    assert application.installation() == before
    with pytest.raises(ValueError, match="登记尚未完成"):
        application.start()
    # The original package may have been replaced before the next launch.
    # A newly verified package must be able to complete registration.
    replacement = application.qualify(
        before.python,
        before.static_dir,
        composition=before.composition + "# replacement\n",
    )
    application.select(replacement)
    assert not application.pending.exists()
    assert application.installation() == replacement
    assert application.start().project_root == application.root


def test_stopping_development_does_not_stop_another_application_home(
    application, tmp_path
):
    other = ApplicationRuntime(tmp_path / "daily")
    other.configure(static_dir=application.installation().static_dir)
    try:
        daily = other.start()
        application.start()
        application.stop()
        assert other.status().record == daily
        assert other.status().state == "running"
    finally:
        other.stop()
