"""Source desktop ownership, frozen resource identity and safe lifecycle boundaries."""

import json
import queue
import subprocess
import threading
from unittest.mock import Mock

import pytest
from filelock import FileLock

from lab_tools import dev, dev_resources
from lab_tools.desktop_session import DesktopSession


def test_home_is_stable_canonical_external_and_worktree_specific(tmp_path, monkeypatch):
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


def test_resources_track_dirty_untracked_locks_but_not_live_gui(tmp_path):
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
def test_restart_uses_atomic_idle_admission_and_updates_endpoint_before_windows(busy):
    runtime = Mock()
    runtime.stop_if_idle.return_value = not busy
    session = DesktopSession(runtime, threading.Event())
    session.ui_url = "http://127.0.0.1:5173"
    session.connected("http://127.0.0.1:1000")
    events = []
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


def test_existing_owner_reused_without_preparing_or_stopping(tmp_path, monkeypatch):
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


def test_installed_or_other_checkout_home_never_stopped(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "installation.json").write_text("{}")
    stop = Mock()
    monkeypatch.setattr(dev, "stop_when_idle", stop)
    with pytest.raises(ValueError, match="not an owned"):
        dev.run(tmp_path, home, browser=True)
    stop.assert_not_called()
    (home / "development.json").write_text(json.dumps({"source": "another"}))
    with pytest.raises(ValueError, match="another checkout"):
        dev.run(tmp_path, home, browser=True)
    stop.assert_not_called()


def test_terminal_wait_restarts_only_after_idle(tmp_path, monkeypatch):
    runtime = Mock()
    runtime.stop_if_idle.side_effect = [False, False, True]
    session = DesktopSession(runtime, threading.Event())
    monkeypatch.setattr(dev, "source_stamp", lambda _: "source")
    commands = queue.Queue()
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


def test_failed_source_watcher_does_not_disable_safe_quit(tmp_path, monkeypatch):
    import itertools

    runtime = Mock()
    runtime.stop_if_idle.return_value = True
    session = DesktopSession(runtime, threading.Event())
    checked = threading.Event()

    def changed(_source):
        checked.set()
        raise FileNotFoundError("editor replaced file")

    clock = itertools.count(0, 3)
    monkeypatch.setattr(dev, "source_stamp", changed)
    monkeypatch.setattr(dev.time, "monotonic", lambda: next(clock))
    commands = queue.Queue()
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


def test_native_endpoint_change_updates_proxy_and_reuse_report(tmp_path):
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
    tmp_path, monkeypatch
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
    commands = queue.Queue()
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
    tmp_path, monkeypatch
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

    def reject_read(_path):
        raise AssertionError("idle reminder must not read file contents")

    monkeypatch.setattr(type(source), "read_bytes", reject_read)
    stamp = dev_resources.source_stamp(tmp_path)
    source.rename(source.with_name("renamed.py"))
    assert dev_resources.source_stamp(tmp_path) != stamp
