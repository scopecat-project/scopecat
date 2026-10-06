"""Destructive recovery operates only on newly created temporary fixtures."""

import json
import os
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import psutil
import pytest
import webview
from filelock import FileLock

from lab_tools import data_reset
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.data_reset import (
    ResetIncomplete,
    UnsupportedDataSpace,
    check_format,
    reset_store,
)
from lab_tools.desktop import DesktopAPI, _recovery
from lab_tools.desktop_session import DesktopSession
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
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
    with sqlite3.connect(data / "control.sqlite3") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        db.execute("CREATE TABLE project_schema(singleton INTEGER, version INTEGER)")
        db.execute(
            "INSERT INTO project_schema VALUES (1, ?)", (PROJECT_SCHEMA_VERSION - 1,)
        )
        db.execute("CREATE TABLE old_science(value TEXT)")
        db.execute("INSERT INTO old_science VALUES ('old result')")
    db.close()
    objects = ImmutableObjectStore(data / "objects")
    objects.put(b"old scientific bytes")
    (runtime.root / "author.py").write_text("raise RuntimeError('do not import')")
    (runtime.home / "environments").mkdir()
    (runtime.home / "environments/keep").write_text("environment")
    (data / "unknown.txt").write_text("unowned")
    (data / "author-workspaces.json").write_text(
        json.dumps({"service_root": str(runtime.root), "items": []})
    )
    protected = {
        p: p.read_bytes()
        for p in runtime.home.rglob("*")
        if p.is_file()
        and p.name not in data_reset._FILES
        and not p.is_relative_to(data / "objects")
        and p.suffix != ".lock"
    }
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(UnsupportedDataSpace) as error:
        check_format(runtime)

    def prepare():
        # Distinct runtime instance exercises the same lock nesting as native prepare.
        fresh = ApplicationRuntime(runtime.home)
        check_format(fresh)
        fresh.configure(static_dir=gui)

    yield runtime, error.value, prepare, backup
    runtime.stop()
    for path, content in protected.items():
        assert path.read_bytes() == content


def assert_empty(runtime):
    with sqlite3.connect(runtime.root / ".scopecat/control.sqlite3") as db:
        assert (
            db.execute("SELECT version FROM project_schema").fetchone()[0]
            == PROJECT_SCHEMA_VERSION
        )
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='old_science'"
            ).fetchone()
            is None
        )
    assert not (runtime.home / "data-reset.json").exists()
    assert not (runtime.home / "spaces").exists()


@pytest.mark.parametrize("backup_first", [True, False])
def test_host_confirm_reset_and_restart(legacy, backup_first):
    runtime, _, prepare, backup = legacy
    home = runtime.home
    data = runtime.root / ".scopecat"
    old_db, old_wal = (
        (data / "control.sqlite3").read_bytes(),
        (data / "control.sqlite3-wal").read_bytes(),
    )
    session = DesktopSession(runtime, threading.Event())
    session.prepare_reset = prepare
    window = create_autospec(webview.Window, instance=True)
    window.create_file_dialog.return_value = (str(backup),)
    api = DesktopAPI(session, lambda: window, lambda: check_format(runtime))
    with pytest.raises(UnsupportedDataSpace):
        api.retry()
    assert "skip-backup" in window.load_html.call_args.args[0]
    window.create_confirmation_dialog.return_value = False
    assert not api.reset_data(skip_backup=not backup_first)
    assert (data / "control.sqlite3").read_bytes() == old_db
    assert (data / "control.sqlite3-wal").read_bytes() == old_wal
    assert not list(backup.iterdir())
    window.create_confirmation_dialog.return_value = True
    assert api.reset_data(skip_backup=not backup_first)
    assert runtime.home == home
    assert_empty(runtime)
    assert runtime.status().state == "running"
    check_format(runtime)
    runtime.stop()
    reopened = ApplicationRuntime(home)
    reopened.start()
    reopened.stop()
    if backup_first:
        window.create_file_dialog.assert_called_with(
            webview.FileDialog.FOLDER, directory=""
        )
        (archive,) = backup.iterdir()
        assert (archive / "store/control.sqlite3").read_bytes() == old_db
        assert (archive / "store/control.sqlite3-wal").read_bytes() == old_wal
        assert (
            json.loads((archive / "manifest.json").read_text())["kind"]
            == "scopecat-opaque-store-originals"
        )
        assert json.loads((home / "desktop/reset-backup.json").read_text()) == {
            "directory": str(backup)
        }
    else:
        assert not list(backup.iterdir())
        assert not (home / "desktop/reset-backup.json").exists()
        window.create_file_dialog.assert_not_called()


@pytest.mark.parametrize(
    "unsafe",
    [
        "binding",
        "composition",
        "worker",
        "lock",
        "instrument",
        "symlink",
        "unknown-object",
        "journal",
        "inside-backup",
    ],
)
def test_refusal_preserves_database(legacy, unsafe):
    runtime, error, prepare, backup = legacy
    data = runtime.root / ".scopecat"
    original = (data / "control.sqlite3").read_bytes()
    manifest = runtime.root / "scopecat.toml"
    saved = manifest.read_bytes()
    lock = None
    if unsafe == "binding":
        (runtime.root / "scopecat.runtime.toml").write_text(
            '[runtime]\ndata_root="elsewhere"\ndeployment_root="elsewhere"'
        )
    elif unsafe == "composition":
        manifest.write_text('[lab]\nbootstrap="never:load"')
    elif unsafe == "worker":
        p = data / "procedure-workers/old/process.json"
        p.parent.mkdir(parents=True)
        p.write_text(
            json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()})
        )
    elif unsafe == "lock":
        lock = FileLock(data / "daemon.lock")
    elif unsafe == "instrument":
        (data / "worker-diagnostics").mkdir()
        lock = FileLock(data / "worker-diagnostics/live.jsonl.lock")
    elif unsafe == "symlink":
        (data / "objects/redirect").symlink_to(backup, target_is_directory=True)
    elif unsafe == "unknown-object":
        (data / "objects/notes.txt").write_text("do not delete")
    elif unsafe == "journal":
        (data / "control.sqlite3-journal").write_bytes(b"unresolved journal")
    elif unsafe == "inside-backup":
        backup = runtime.home
    try:
        if lock:
            lock.acquire()
        with pytest.raises(
            ValueError, match=r"自定义|进程|后台|符号链接|未知|journal|备份目录"
        ):
            reset_store(runtime, error, prepare, backup)
        assert (data / "control.sqlite3").read_bytes() == original
        assert not (runtime.home / "data-reset.json").exists()
    finally:
        if lock:
            lock.release()
        manifest.write_bytes(saved)
        (runtime.root / "scopecat.runtime.toml").unlink(missing_ok=True)


def test_backup_failure_never_falls_back_to_delete(legacy, monkeypatch):
    runtime, error, prepare, backup = legacy
    db = runtime.root / ".scopecat/control.sqlite3"
    before = db.read_bytes()
    monkeypatch.setattr(
        data_reset.shutil, "copyfileobj", Mock(side_effect=OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        reset_store(runtime, error, prepare, backup)
    assert db.read_bytes() == before
    assert not (runtime.home / "data-reset.json").exists()
    assert not list(backup.iterdir())


def test_interrupted_delete_requires_confirmation_and_verified_backup(
    legacy, monkeypatch
):
    runtime, error, prepare, backup = legacy
    from pathlib import Path

    original = Path.unlink

    def fail_object(path, *args, **kwargs):
        if "objects" in path.parts:
            raise OSError("permission denied")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_object)
        with pytest.raises(ValueError, match="部分数据"):
            reset_store(runtime, error, prepare, backup)
    with pytest.raises(ResetIncomplete):
        check_format(runtime)
    with pytest.raises(ValueError, match="删除尚未完成"):
        runtime.start()
    reset_store(runtime, ResetIncomplete(runtime.home), prepare, None)
    assert_empty(runtime)
    assert len(list(backup.iterdir())) == 1


def test_other_errors_do_not_offer_reset():
    assert 'onclick="resetData()"' not in _recovery(
        OSError("permission denied"), can_reset=True
    )


def test_folder_cancel_does_not_delete_or_remember_skip(legacy):
    runtime, _, prepare, _ = legacy
    session = DesktopSession(runtime, threading.Event())
    session.prepare_reset = prepare
    window = Mock()
    window.create_file_dialog.return_value = None
    api = DesktopAPI(session, lambda: window, lambda: check_format(runtime))
    with pytest.raises(UnsupportedDataSpace):
        api.retry()
    before = (runtime.root / ".scopecat/control.sqlite3").read_bytes()
    assert api.reset_data() is False
    window.create_confirmation_dialog.assert_not_called()
    assert (runtime.root / ".scopecat/control.sqlite3").read_bytes() == before
    assert not (runtime.home / "desktop/reset-backup.json").exists()


@pytest.mark.parametrize("cancel", [True, False])
def test_host_uses_pinned_folder_dialog_contract(legacy, monkeypatch, cancel):
    """Exercise pywebview's real adapter; only its OS GUI backend is substituted."""
    runtime, _, prepare, backup = legacy
    settings = runtime.home / "desktop/reset-backup.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"directory": str(backup)}))
    selected = backup / "chosen folder"
    selected.mkdir()
    session = DesktopSession(runtime, threading.Event())
    session.prepare_reset = prepare
    window = create_autospec(webview.Window, instance=True)
    window.uid = "reset-contract"
    shown = threading.Event()
    shown.set()
    window.events = SimpleNamespace(shown=shown)
    window.gui = Mock()
    window.gui.create_file_dialog.return_value = None if cancel else (str(selected),)
    # The pinned library checks initial-directory existence and supplies defaults.
    window.create_file_dialog = webview.Window.create_file_dialog.__get__(window)
    window.create_confirmation_dialog.return_value = True
    reset = Mock(return_value=SimpleNamespace(base_url="http://localhost:1234"))
    monkeypatch.setattr("lab_tools.desktop.reset_store", reset)
    api = DesktopAPI(session, lambda: window, lambda: check_format(runtime))
    with pytest.raises(UnsupportedDataSpace) as diagnosed:
        api.retry()
    html = window.load_html.call_args.args[0]
    assert '<input id="skip-backup" type="checkbox">' in html
    assert api.reset_data() is not cancel  # Omitted argument must still select backup.
    window.gui.create_file_dialog.assert_called_once_with(
        webview.FileDialog.FOLDER, str(backup), False, "", (), "reset-contract"
    )
    if cancel:
        reset.assert_not_called()
        window.create_confirmation_dialog.assert_not_called()
        expected_directory = backup
    else:
        reset.assert_called_once_with(runtime, diagnosed.value, prepare, selected)
        expected_directory = selected
    assert json.loads(settings.read_text()) == {"directory": str(expected_directory)}


@pytest.mark.parametrize("damage", ["archive", "new-object"])
def test_resume_refuses_unbacked_or_corrupt_content(legacy, monkeypatch, damage):
    from pathlib import Path

    runtime, error, prepare, backup = legacy
    unlink = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path.name == "control.sqlite3":
            raise OSError("permission denied")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(ValueError, match="部分数据"):
            reset_store(runtime, error, prepare, backup)
    (archive,) = backup.iterdir()
    if damage == "archive":
        (archive / "store/control.sqlite3").write_bytes(b"damaged")
    else:
        ImmutableObjectStore(runtime.root / ".scopecat/objects").put(b"not archived")
    before = (runtime.root / ".scopecat/control.sqlite3").read_bytes()
    with pytest.raises(ValueError, match=r"校验失败|部分数据"):
        reset_store(runtime, ResetIncomplete(runtime.home), prepare, None)
    assert (runtime.root / ".scopecat/control.sqlite3").read_bytes() == before


def test_inventory_error_prevents_backup_and_deletion(legacy, monkeypatch):
    runtime, error, prepare, backup = legacy
    walk = data_reset.os.walk

    def denied(path, *args, **kwargs):
        if str(path).endswith("objects"):
            kwargs["onerror"](PermissionError("unreadable shard"))
        return walk(path, *args, **kwargs)

    monkeypatch.setattr(data_reset.os, "walk", denied)
    before = (runtime.root / ".scopecat/control.sqlite3").read_bytes()
    with pytest.raises(PermissionError, match="unreadable"):
        reset_store(runtime, error, prepare, backup)
    assert (runtime.root / ".scopecat/control.sqlite3").read_bytes() == before
    assert not list(backup.iterdir())


def test_initialization_failure_has_separate_retry_phase(legacy, monkeypatch):
    from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

    runtime, error, prepare, backup = legacy
    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteProjectStore, "bootstrap", Mock(side_effect=OSError("disk full"))
        )
        with pytest.raises(ValueError, match="部分数据"):
            reset_store(runtime, error, prepare, backup)
    marker = json.loads((runtime.home / "data-reset.json").read_bytes())
    assert marker["phase"] == "initializing"
    reset_store(runtime, ResetIncomplete(runtime.home), prepare, None)
    assert_empty(runtime)


@pytest.mark.parametrize("backup_first", [True, False])
def test_sqlite_writer_blocks_backup_and_skip(legacy, backup_first):
    runtime, error, prepare, backup = legacy
    database = runtime.root / ".scopecat/control.sqlite3"
    with sqlite3.connect(database, isolation_level=None) as writer:
        writer.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        writer.execute("BEGIN IMMEDIATE")
        before = database.read_bytes()
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                reset_store(runtime, error, prepare, backup if backup_first else None)
            assert database.read_bytes() == before
            assert not (runtime.home / "data-reset.json").exists()
            assert not list(backup.iterdir())
        finally:
            writer.rollback()
    writer.close()
