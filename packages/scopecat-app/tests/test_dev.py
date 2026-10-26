"""Source desktop ownership, frozen resource identity and safe lifecycle boundaries."""

import json
import queue
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest
from filelock import FileLock
from scripts import dev
from scripts.app_build import author_materials, delivery, dev_resources
from scripts.app_build.author_resources import AuthorInputs

from scopecat_app.bundle import file_hash, inventory, target_identity
from scopecat_app.desktop_session import DesktopSession


def test_home_is_stable_canonical_external_and_worktree_specific(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        dev_resources, "user_data_path", lambda *_a, **_k: tmp_path / "user"
    )
    source = tmp_path / "source"
    source.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    assert dev_resources.development_home(source) == dev_resources.development_home(
        alias
    )
    assert dev_resources.development_home(source) != dev_resources.development_home(
        tmp_path / "tree2"
    )
    assert not dev_resources.development_home(source).is_relative_to(source)


def test_resources_track_dirty_untracked_locks_but_not_live_gui(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)  # noqa: S603, S607
    package = tmp_path / "packages/example/src/example"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text("one")
    lock = tmp_path / "uv.lock"
    lock.write_text("locked")
    first = dev_resources.source_identity(tmp_path)
    (tmp_path / ".gitignore").write_text("*.py\n")
    module.write_text("two")
    second = dev_resources.source_identity(tmp_path)
    assert second != first
    lock.write_text("changed")
    third = dev_resources.source_identity(tmp_path)
    assert third != second
    ui = tmp_path / "apps/scopecat-ui/src"
    ui.mkdir(parents=True)
    (ui / "App.tsx").write_text("HMR")
    assert dev_resources.source_identity(tmp_path) == third
    module.unlink()
    assert dev_resources.source_identity(tmp_path) != third


@pytest.mark.parametrize("busy", [True, False])
def test_restart_uses_atomic_idle_admission_and_updates_endpoint_before_windows(
    busy: bool,
):
    runtime = Mock()
    runtime.stop_if_idle.return_value = not busy
    session = DesktopSession(runtime, threading.Event())
    session.ui_url = "http://127.0.0.1:5173"
    session.connected("http://127.0.0.1:1000")
    events: list[tuple[str, str]] = []
    session.endpoint_changed = lambda url: events.append(("proxy", url))
    session.connection_changed = lambda _old, new: events.append(("windows", new))
    start = Mock(side_effect=lambda: session.connected("http://127.0.0.1:2000"))
    if busy:
        with pytest.raises(ValueError, match="后台仍有工作"):
            session.restart(start)
        start.assert_not_called()
        assert not events
    else:
        session.restart(start)
        assert events == [
            ("proxy", "http://127.0.0.1:2000"),
            ("windows", "http://127.0.0.1:2000"),
        ]
        assert session.page_url == "http://127.0.0.1:5173"
    runtime.stop.assert_not_called()


def test_unknown_activity_never_restarts():
    runtime = Mock()
    runtime.stop_if_idle.side_effect = ValueError("unknown")
    session = DesktopSession(runtime, threading.Event())
    start = Mock()
    with pytest.raises(ValueError, match="unknown"):
        session.restart(start)
    start.assert_not_called()
    runtime.stop.assert_not_called()


def test_existing_owner_reused_without_preparing_or_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    home = tmp_path / "home"
    home.mkdir()
    (home / "development.json").write_text(
        json.dumps({"source": str(source.resolve()), "ui": "http://127.0.0.1:1"})
    )
    prepare = Mock()
    monkeypatch.setattr(dev, "prepare_application", prepare)
    with FileLock(home / "development.lock"):
        dev.run(source, home, browser=False)
    prepare.assert_not_called()
    assert (home / "desktop/activate").read_text() == "source-development"


@pytest.mark.parametrize("layout", ["legacy", "application"])
def test_installed_or_other_checkout_home_never_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
):
    home = tmp_path / "home"
    home.mkdir()
    if layout == "legacy":
        (home / "runtime").mkdir()
        (home / "installation.json").write_text("{}")
    else:
        (home / "scopecat.application.json").write_text(
            '{"format": 1, "layout": "application"}'
        )
    stop = Mock()
    monkeypatch.setattr(dev, "stop_when_idle", stop)
    with pytest.raises(ValueError, match="not an owned"):
        dev.run(tmp_path, home, browser=True)
    stop.assert_not_called()
    (home / "development.json").write_text(json.dumps({"source": "another"}))
    with pytest.raises(ValueError, match="another checkout"):
        dev.run(tmp_path, home, browser=True)
    stop.assert_not_called()


def test_terminal_wait_restarts_only_after_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = Mock()
    runtime.stop_if_idle.side_effect = [False, False, True]
    session = DesktopSession(runtime, threading.Event())
    monkeypatch.setattr(dev, "source_stamp", lambda _: "source")
    commands: queue.Queue[str] = queue.Queue()
    commands.put("w")
    start = Mock(side_effect=session.closing.set)
    thread = threading.Thread(
        target=dev.control_session, args=(session, tmp_path, commands, start)
    )
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    start.assert_called_once()
    runtime.stop.assert_not_called()


def test_failed_source_watcher_does_not_disable_safe_quit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import itertools

    runtime = Mock()
    runtime.stop_if_idle.return_value = True
    session = DesktopSession(runtime, threading.Event())
    checked = threading.Event()

    def changed(_source: Path) -> None:
        checked.set()
        raise FileNotFoundError("editor replaced file")

    clock = itertools.count(0, 3)
    monkeypatch.setattr(dev, "source_stamp", changed)
    monkeypatch.setattr(dev.time, "monotonic", lambda: next(clock))
    commands: queue.Queue[str] = queue.Queue()
    thread = threading.Thread(
        target=dev.control_session, args=(session, tmp_path, commands, Mock())
    )
    thread.start()
    assert checked.wait(3)
    commands.put("q")
    thread.join(3)
    assert not thread.is_alive()
    assert session.closing.is_set()
    runtime.stop_if_idle.assert_called_once()
    runtime.stop.assert_not_called()


def test_native_endpoint_change_updates_proxy_and_reuse_report(tmp_path: Path):
    marker = tmp_path / "development.json"
    marker.write_text(json.dumps({"source": "checkout", "ui": "http://127.0.0.1:5000"}))
    dev.publish_endpoint(tmp_path, "http://127.0.0.1:9001")
    assert json.loads((tmp_path / "backend.json").read_text()) == {
        "url": "http://127.0.0.1:9001"
    }
    assert json.loads(marker.read_text()) == {
        "source": "checkout",
        "ui": "http://127.0.0.1:5000",
        "backend": "http://127.0.0.1:9001",
    }


def test_frontend_failure_reports_without_stopping_background_and_can_quit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = Mock()
    runtime.stop_if_idle.return_value = True
    session = DesktopSession(runtime, threading.Event())
    hidden = Mock()
    session.keep_running(hidden)
    reported = threading.Event()
    from types import SimpleNamespace

    logger = Mock()
    logger.error.side_effect = lambda _message: reported.set()
    monkeypatch.setattr(dev, "logging", SimpleNamespace(getLogger=lambda _name: logger))
    monkeypatch.setattr(dev, "source_stamp", lambda _source: "source")
    frontend = Mock(returncode=1)
    frontend.poll.return_value = 1
    commands: queue.Queue[str] = queue.Queue()
    thread = threading.Thread(
        target=dev.control_session,
        args=(session, tmp_path, commands, Mock(), frontend, tmp_path / "vite.log"),
    )
    thread.start()
    try:
        assert reported.wait(3)
        assert not session.closing.is_set()
        runtime.stop_if_idle.assert_not_called()
        runtime.stop.assert_not_called()
        assert logger.error.call_args is not None
        message = logger.error.call_args.args[0]
        assert "Vite exited (1)" in message
        assert str(tmp_path / "vite.log") in message
        assert "Ctrl-C" in message
        commands.put("q")
        thread.join(3)
        assert not thread.is_alive()
        assert session.closing.is_set()
        logger.error.assert_called_once()
        runtime.stop_if_idle.assert_called_once()
        runtime.stop.assert_not_called()
    finally:
        session.closing.set()
        thread.join(3)


def test_source_reminder_reads_metadata_but_cache_still_hashes_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import os

    source = tmp_path / "packages/example/src/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("one")
    first = dev_resources.source_identity(tmp_path)
    stat = source.stat()
    source.write_text("two")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert dev_resources.source_identity(tmp_path) != first

    def reject_read(_path: Path) -> bytes:
        raise AssertionError("idle reminder must not read file contents")

    monkeypatch.setattr(type(source), "read_bytes", reject_read)
    stamp = dev_resources.source_stamp(tmp_path)
    source.rename(source.with_name("renamed.py"))
    assert dev_resources.source_stamp(tmp_path) != stamp


@pytest.mark.parametrize("platform", ["darwin", "win32"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_cached_payload_repairs_native_wheel_and_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str, platform: str
):
    import hashlib
    import io
    import zipfile

    source = tmp_path / "source"
    cache = tmp_path / "cache"
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("webview/__init__.py", "reviewed native host")
    wheel_bytes = content.getvalue()
    builds: list[Path] = []
    monkeypatch.setattr(
        dev_resources, "sys", SimpleNamespace(platform=platform, path=list(sys.path))
    )
    monkeypatch.setattr(dev_resources, "source_identity", lambda _: "bytes")
    monkeypatch.setattr(dev_resources, "resource_identity", lambda _: "identity")
    monkeypatch.setattr(
        dev_resources, "verify_resources", lambda _: {"sources": {"public": "bytes"}}
    )

    def verify(wheels: Path) -> None:
        if (wheels / "pywebview-1.whl").read_bytes() != wheel_bytes:
            raise ValueError("corrupt wheel")

    def materials(
        _source: Path,
        destination: Path,
        _gui: Path,
        *,
        native_wheels: Path,
        source_digest: str,
    ) -> AuthorInputs:
        builds.append(destination)
        native_wheels.mkdir()
        (native_wheels / "pywebview-1.whl").write_bytes(wheel_bytes)
        return AuthorInputs(destination, {}, {"public": source_digest}, {})

    def project(_inputs: AuthorInputs, destination: Path) -> Path:
        destination.mkdir()
        (destination / "identity").write_text("immutable author resources")
        return destination

    monkeypatch.setattr(dev_resources, "_verify_native_wheel", verify)
    monkeypatch.setattr(dev_resources, "prepare_materials", materials)
    monkeypatch.setattr(dev_resources, "build_author_resources", project)
    payload = dev_resources.prepare_resources(source, cache)
    wheel = cache / "native-wheels/identity/pywebview-1.whl"
    if damage == "missing":
        wheel.unlink()
    else:
        wheel.write_bytes(b"corrupt")
    assert dev_resources.prepare_resources(source, cache) == payload
    assert len(builds) == 2
    assert (payload / "identity").read_text() == "immutable author resources"
    verify(wheel.parent)
    monkeypatch.setattr(dev_resources.sys, "path", list(dev_resources.sys.path))
    dev_resources.prepare_native_dependency(payload, cache)
    extracted = (
        cache
        / "native"
        / hashlib.sha256(wheel_bytes).hexdigest()
        / "webview/__init__.py"
    )
    extracted.write_text("corrupt extraction")
    dev_resources.prepare_native_dependency(payload, cache)
    assert extracted.read_text() == "reviewed native host"


def test_ui_handoff_tracks_backend_restart(tmp_path: Path):
    from scopecat.application_instance import ApplicationInstance
    from scopecat_server.development_ui import RECORD_NAME, DevelopmentUI

    instance = ApplicationInstance(tmp_path)
    (tmp_path / "scopecat.application.json").write_text(instance.declaration())
    (tmp_path / "development.json").write_text(json.dumps({"source": "checkout"}))
    runtime = Mock()
    runtime.instance = instance
    session = DesktopSession(runtime, threading.Event())
    session.base_url = "http://127.0.0.1:8100"
    session.ui_url = "http://127.0.0.1:8200"
    dev.publish_ui(session, psutil.Process())
    path = instance.runtime_binding.coordination_root / RECORD_NAME
    before = DevelopmentUI.model_validate_json(path.read_bytes())
    dev.publish_endpoint(tmp_path, "http://127.0.0.1:8101")
    after = DevelopmentUI.model_validate_json(path.read_bytes())
    assert after.backend == "http://127.0.0.1:8101"
    assert after.ui == before.ui
    assert after.owner_created == before.owner_created


def test_material_preparation_runs_only_export_and_two_source_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text('[[package]]\nname="uv"\nversion="locked"\n')
    for name in ("scopecat", "scopecat-server"):
        package = source / "packages" / name
        package.mkdir(parents=True)
        (package / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="0.1.0"\n'
        )
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("Vite handoff")
    calls: list[list[str]] = []

    def run(command: list[str], *, cwd: Path) -> None:
        assert cwd == source
        calls.append(command)
        if command[1] == "export":
            Path(command[-1]).write_text("dependency==1 --hash=sha256:locked\n")
        else:
            assert command[1] == "build"
            package = Path(command[-1]).name.replace("-", "_")
            wheels = Path(command[command.index("--out-dir") + 1])
            (wheels / ".gitignore").write_text("*")
            with zipfile.ZipFile(
                wheels / f"{package}-1-py3-none-any.whl", "w"
            ) as wheel:
                wheel.writestr(
                    f"{package}-1.dist-info/METADATA", f"Name: {package}\nVersion: 1\n"
                )

    monkeypatch.setattr(
        author_materials,
        "sys",
        SimpleNamespace(platform="linux", executable=sys.executable),
    )
    monkeypatch.setattr(author_materials, "version", lambda _: "locked")
    monkeypatch.setattr(author_materials.toolchain, "baseline", lambda _: "3.15.1")
    monkeypatch.setattr(delivery, "package_version", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery, "run", run)
    inputs = author_materials.prepare(
        source,
        tmp_path / "materials",
        gui,
        source_digest="source-bytes",
        native_wheels=tmp_path / "native",
    )
    assert [c[1] for c in calls] == ["export", "build", "build"]
    assert "--locked" in calls[0] and "--no-emit-local" in calls[0]
    assert all(
        "--no-python-downloads" in c and "--build-constraints" in c for c in calls[1:]
    )
    assert inputs.sources == {"public": "source-bytes"}
    assert {p.name for p in inputs.root.iterdir()} == {
        "gui",
        "wheels",
        "requirements.lock",
    }
    constraints = (inputs.root / "requirements.lock").read_text()
    assert "dependency==1 --hash=sha256:locked" in constraints
    assert "scopecat==1 --hash=sha256:" in constraints
    assert "scopecat_server==1 --hash=sha256:" in constraints
    assert not (tmp_path / "native").exists()
    dev_resources.build_author_resources(inputs, tmp_path / "projected")
    assert "wheels/.gitignore" not in inputs.files


@pytest.fixture
def prepared_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, list[str]]:
    source = tmp_path / "source"
    module = source / "packages/scopecat/src/scopecat/example.py"
    module.parent.mkdir(parents=True)
    module.write_text("one")
    calls: list[str] = []

    def materials(
        _source: Path,
        destination: Path,
        gui: Path,
        *,
        source_digest: str,
        native_wheels: Path,
    ) -> AuthorInputs:
        import shutil

        calls.append(source_digest)
        shutil.copytree(gui, destination / "gui")
        (destination / "wheels").mkdir()
        for package in ("scopecat", "scopecat_server"):
            (destination / "wheels" / f"{package}-1-py3-none-any.whl").write_text(
                module.read_text()
            )
        (destination / "requirements.lock").write_text("locked")
        files = inventory(destination, ("gui", "wheels"))
        files["requirements.lock"] = file_hash(destination / "requirements.lock")
        return AuthorInputs(
            destination, target_identity(), {"public": source_digest}, files
        )

    monkeypatch.setattr(dev_resources, "prepare_materials", materials)
    monkeypatch.setattr(dev_resources, "_native_ready", lambda *_: True)
    monkeypatch.setattr(
        dev_resources, "sys", SimpleNamespace(platform="linux", version=sys.version)
    )
    monkeypatch.setattr(
        dev_resources, "resource_identity", dev_resources.source_identity
    )
    return source, module, calls


def test_source_resources_first_reuse_damage_and_byte_changes(
    prepared_source: tuple[Path, Path, list[str]], tmp_path: Path
):
    import os

    source, module, calls = prepared_source
    cache = tmp_path / "cache"
    first = dev_resources.prepare_resources(source, cache)
    assert dev_resources.prepare_resources(source, cache) == first
    assert len(calls) == 1
    ui = source / "apps/scopecat-ui/src/App.tsx"
    ui.parent.mkdir(parents=True)
    ui.write_text("HMR edit")
    assert dev_resources.prepare_resources(source, cache) == first
    assert len(calls) == 1
    (first / "requirements.lock").unlink()
    assert dev_resources.prepare_resources(source, cache) == first
    assert len(calls) == 2
    (first / "gui/index.html").write_text("corrupt")
    assert dev_resources.prepare_resources(source, cache) == first
    assert len(calls) == 3
    manifest = first / "bundle.json"
    document = json.loads(manifest.read_text())
    document["sources"] = {"public": "wrong source"}
    manifest.write_text(json.dumps(document))
    assert dev_resources.prepare_resources(source, cache) == first
    assert len(calls) == 4
    stat = module.stat()
    module.write_text("two")
    os.utime(module, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    second = dev_resources.prepare_resources(source, cache)
    assert second != first and first.is_dir()
    assert len(calls) == 5
    assert "development-ui.marker" in {p.name for p in (second / "gui").iterdir()}


def test_source_change_during_projection_never_publishes(
    prepared_source: tuple[Path, Path, list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, module, _ = prepared_source
    cache = tmp_path / "cache"
    key = dev_resources.resource_identity(source)
    project = dev_resources.build_author_resources

    def changed(inputs: AuthorInputs, destination: Path) -> Path:
        result = project(inputs, destination)
        module.write_text("changed during projection")
        return result

    monkeypatch.setattr(dev_resources, "build_author_resources", changed)
    with pytest.raises(ValueError, match="Source changed"):
        dev_resources.prepare_resources(source, cache)
    assert not (cache / key).exists()


@pytest.mark.parametrize("damage", ["version", "hash"])
def test_native_materials_reject_lock_drift_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
):
    patch = author_materials.cocoa_dependency
    version = "other" if damage == "version" else patch.UPSTREAM_VERSION
    digest = "0" * 64 if damage == "hash" else patch.UPSTREAM_SHA256
    (tmp_path / "uv.lock").write_text(
        f'[[package]]\nname="pywebview"\nversion="{version}"\n'
        f'wheels=[{{url="https://files.pythonhosted.org/pywebview-{patch.UPSTREAM_VERSION}-py3-none-any.whl",hash="sha256:{digest}"}}]\n'
    )
    monkeypatch.setattr(author_materials, "sys", SimpleNamespace(platform="darwin"))
    with pytest.raises(ValueError, match="differs from the reviewed repair"):
        author_materials.prepare_native_wheel(tmp_path, tmp_path / "native")
    assert not (tmp_path / "native").exists()
