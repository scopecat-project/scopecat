"""Stopped legacy data remains intact through explicit host recovery."""

import json
import os
import sqlite3
import threading
from unittest.mock import Mock

import psutil
import pytest
from filelock import FileLock

from lab_tools import application_runtime, data_spaces
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.data_spaces import UnsupportedDataSpace, check_format, fresh_start
from lab_tools.desktop import DesktopAPI, _recovery
from lab_tools.desktop_session import DesktopSession
from scopecat_server.storage.sqlite.schema import PROJECT_SCHEMA_VERSION


@pytest.fixture
def legacy(tmp_path):
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>empty application</html>")
    runtime = ApplicationRuntime(tmp_path / "home")
    runtime.configure(static_dir=gui)
    data = runtime.root / ".scopecat"
    data.mkdir(exist_ok=True)
    database = data / "control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        connection.execute(
            "CREATE TABLE project_schema(singleton INTEGER, version INTEGER)"
        )
        connection.execute(
            "INSERT INTO project_schema VALUES (1, ?)", (PROJECT_SCHEMA_VERSION - 1,)
        )
        connection.execute("CREATE TABLE old_science(value TEXT)")
        connection.execute("INSERT INTO old_science VALUES ('retain exactly')")
    connection.close()
    (data / "objects").mkdir()
    (data / "objects/original").write_bytes(b"immutable old object")
    (runtime.root / "author.py").write_text("raise RuntimeError('do not import')")
    (runtime.home / "environments").mkdir()
    (runtime.home / "environments/retain").write_text("external dependency receipt")
    original = {
        p: p.read_bytes()
        for p in runtime.home.rglob("*")
        if p.is_file() and p.suffix != ".lock"
    }
    with pytest.raises(UnsupportedDataSpace) as caught:
        check_format(runtime)

    def prepare(home):
        ApplicationRuntime(home).configure(static_dir=gui)

    yield runtime, caught.value, prepare, original
    # No real user data: stop only this fixture's selected/candidate service.
    runtime.stop()
    journal = runtime.anchor / "reset-attempt.json"
    if journal.exists():
        attempt = json.loads(journal.read_text())
        candidate = ApplicationRuntime(runtime.anchor / "spaces" / attempt["space"])
        if candidate.selection.exists():
            candidate.stop()
    for path, content in original.items():
        assert path.read_bytes() == content, path


def test_host_failure_cancel_reset_and_reopen_without_backend(legacy):
    runtime, failure, prepare, _ = legacy
    anchor = runtime.home
    stale = ApplicationRuntime(anchor)
    session = DesktopSession(runtime, threading.Event())
    session.prepare_fresh = prepare
    window = Mock()
    api = DesktopAPI(session, lambda: window, lambda: check_format(runtime))
    with pytest.raises(UnsupportedDataSpace):
        api.retry()
    html = window.load_html.call_args.args[0]
    assert "保留旧数据，重新开始" in html and str(anchor) in html
    assert failure.actual == PROJECT_SCHEMA_VERSION - 1
    window.create_confirmation_dialog.return_value = False
    assert api.reset_data() is False
    assert not (anchor / "reset-attempt.json").exists()
    window.create_confirmation_dialog.return_value = True
    assert api.reset_data() is True
    assert runtime.home != anchor
    for action in (
        stale.start,
        stale.configure,
        lambda: stale.select(stale.installation()),
    ):
        with pytest.raises(ValueError, match="数据空间已改变"):
            action()
    assert ApplicationRuntime(anchor).home == runtime.home
    assert runtime.status().state == "running"
    # Joining a live service must not try to copy its WAL or acquire its locks.
    check_format(ApplicationRuntime(anchor))
    window.run_js.assert_called_once()
    with sqlite3.connect(runtime.root / ".scopecat/control.sqlite3") as connection:
        assert (
            connection.execute("SELECT version FROM project_schema").fetchone()[0]
            == PROJECT_SCHEMA_VERSION
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='old_science'"
            ).fetchone()
            is None
        )
    assert not (runtime.root / "author.py").exists()
    runtime.stop()
    reopened = ApplicationRuntime(anchor)
    reopened.start()
    assert reopened.home == runtime.home
    reopened.stop()
    with pytest.raises(ValueError, match="只有"):
        api.reset_data()


@pytest.mark.parametrize("stage", ["prepare", "start", "publish"])
def test_failure_keeps_old_selection_and_retry_uses_same_attempt(
    legacy, monkeypatch, stage
):
    runtime, failure, prepare, _ = legacy
    anchor = runtime.home
    write = application_runtime.write_state
    start = ApplicationRuntime.start
    with monkeypatch.context() as patch:
        if stage == "publish":

            def fail_pointer(path, content):
                if path.name == "current-space.json":
                    raise OSError("disk full")
                write(path, content)

            patch.setattr(application_runtime, "write_state", fail_pointer)
        if stage == "start":
            patch.setattr(
                ApplicationRuntime,
                "start",
                Mock(side_effect=OSError("permission denied")),
            )
        selected_prepare = (
            Mock(side_effect=OSError("disk full")) if stage == "prepare" else prepare
        )
        with pytest.raises(OSError, match=r"disk full|permission denied"):
            fresh_start(runtime, failure, selected_prepare)
    assert ApplicationRuntime(anchor).home == anchor
    attempt = (anchor / "reset-attempt.json").read_bytes()
    fresh_start(runtime, failure, prepare)
    assert (anchor / "reset-attempt.json").read_bytes() == attempt
    assert ApplicationRuntime(anchor).home == runtime.home
    assert ApplicationRuntime.start == start


def test_crashed_host_candidate_is_reused_only_after_confirmation(legacy):
    runtime, failure, prepare, _ = legacy
    attempt = data_spaces.ResetAttempt(space="a" * 32, source=str(runtime.home))
    application_runtime.write_state(
        runtime.anchor / "reset-attempt.json", attempt.model_dump_json()
    )
    candidate = runtime.anchor / "spaces" / attempt.space
    prepare(candidate)
    abandoned = ApplicationRuntime(candidate)
    old_record = abandoned.start()
    assert ApplicationRuntime(runtime.anchor).home == runtime.home
    fresh_start(runtime, failure, prepare)
    assert runtime.home == candidate
    assert runtime.status().record.pid != old_record.pid


@pytest.mark.parametrize(
    "unsafe", ["binding", "composition", "worker", "lock", "instrument", "symlink"]
)
def test_unsafe_layout_or_owner_refuses_without_preparing(legacy, unsafe):
    runtime, failure, _, _ = legacy
    data = runtime.root / ".scopecat"
    if unsafe == "binding":
        (runtime.root / "scopecat.runtime.toml").write_text(
            '[runtime]\ndata_root="elsewhere"\ndeployment_root="elsewhere"\n'
        )
    elif unsafe == "composition":
        (runtime.root / "scopecat.toml").write_text('[lab]\nbootstrap="never:load"\n')
    elif unsafe == "worker":
        receipt = data / "procedure-workers/owner/process.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(
            json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()})
        )
    elif unsafe == "symlink":
        (runtime.anchor / "spaces").symlink_to(
            runtime.anchor.parent, target_is_directory=True
        )
    prepare = Mock()
    lock = FileLock(data / "daemon.lock") if unsafe == "lock" else None
    if unsafe == "instrument":
        directory = data / "worker-diagnostics"
        directory.mkdir()
        lock = FileLock(directory / "generation.jsonl.lock")
    try:
        if lock:
            lock.acquire()
        with pytest.raises(ValueError, match=r"自定义|进程|后台|符号链接"):
            fresh_start(runtime, failure, prepare)
        prepare.assert_not_called()
        assert not (runtime.anchor / "current-space.json").exists()
    finally:
        if lock:
            lock.release()
        # Restore only the deliberate test mutation before fixture invariants.
        if unsafe == "binding":
            (runtime.root / "scopecat.runtime.toml").unlink()
        if unsafe == "composition":
            (runtime.root / "scopecat.toml").write_text(
                "[lab]\n[authors]\ndependencies = []\n"
            )


def test_other_startup_errors_have_no_reset_action():
    assert 'onclick="resetData()"' not in _recovery(
        OSError("permission denied"), can_reset=True
    )


def test_published_selection_sync_failure_reports_commit_truthfully(
    legacy, monkeypatch
):
    runtime, failure, prepare, _ = legacy
    original_sync = data_spaces._sync_directory

    def fail_after_commit(path):
        if path == runtime.anchor and (path / "current-space.json").exists():
            raise OSError("cannot sync directory")
        original_sync(path)

    monkeypatch.setattr(data_spaces, "_sync_directory", fail_after_commit)
    with pytest.raises(ValueError, match="新空间已选中"):
        fresh_start(runtime, failure, prepare)
    assert ApplicationRuntime(runtime.anchor).home == runtime.home != failure.home


def test_external_store_is_inspected_but_never_reset(legacy, tmp_path):
    runtime, _, _, _ = legacy
    external = tmp_path / "external-data"
    external.mkdir()
    database = external / "control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE project_schema(singleton INTEGER, version INTEGER)"
        )
        connection.execute(
            "INSERT INTO project_schema VALUES (1, ?)", (PROJECT_SCHEMA_VERSION,)
        )
    before = database.read_bytes()
    binding = runtime.root / "scopecat.runtime.toml"
    binding.write_text(
        "[runtime]\n"
        f"data_root={json.dumps(str(external))}\n"
        f"deployment_root={json.dumps(str(external))}\n"
    )
    try:
        check_format(runtime)  # Existing valid custom layouts still start normally.
        assert database.read_bytes() == before
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE project_schema SET version=?", (PROJECT_SCHEMA_VERSION - 1,)
            )
        before = database.read_bytes()
        with pytest.raises(UnsupportedDataSpace) as failure:
            check_format(runtime)
        prepare = Mock()
        with pytest.raises(ValueError, match="自定义"):
            fresh_start(runtime, failure.value, prepare)
        prepare.assert_not_called()
        assert database.read_bytes() == before
        assert not (runtime.anchor / "reset-attempt.json").exists()
    finally:
        binding.unlink()
