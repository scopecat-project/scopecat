"""Bounded in-place reset recovery using the real host, WebView and native dialog."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.bundle import file_hash
from verify_native_windows import (  # pyright: ignore[reportImplicitRelativeImport]
    host_bootstrap,
    require_hosted_runner,
    run_bounded,
)

# Like the exact-run probe, only the copied launcher's bootstrap selects this
# observer. Host startup, backend, window, dialog and reset code are production.
HOST = r"""
import json, sqlite3, sys, threading, time, traceback
from pathlib import Path
from types import SimpleNamespace
from lab_tools import desktop, native_bootstrap
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.installation_paths import InstallationPaths
from scopecat_server.storage.sqlite.schema import PROJECT_SCHEMA_VERSION
import webview

home, payload, stage = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
paths = InstallationPaths.isolated(home / "fixture")
args = SimpleNamespace(payload=payload)
checks = []
result = {"stage": stage, "checks": checks}


def prepare():
    native_bootstrap.prepare(args, paths)



def wait(check, label):
    end = time.monotonic() + 45
    while not check():
        if time.monotonic() > end:
            raise TimeoutError(label)
        time.sleep(0.05)


if stage == "reset":
    paths.state.mkdir(parents=True)
    prepare()
    data = paths.state / "runtime/.scopecat"
    data.mkdir(exist_ok=True)
    with sqlite3.connect(data / "control.sqlite3") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        db.execute("CREATE TABLE project_schema(singleton INTEGER, version INTEGER)")
        db.execute(
            "INSERT INTO project_schema VALUES (1, ?)", (PROJECT_SCHEMA_VERSION - 1,)
        )
        db.execute("CREATE TABLE old_science(value TEXT)")
        db.execute("INSERT INTO old_science VALUES ('preserve me')")
    db.close()
    (paths.state / "runtime/author.py").write_text("raise RuntimeError('never import')")
    original = {
        str(p.relative_to(paths.state)): p.read_bytes().hex()
        for p in paths.state.rglob("*")
        if p.is_file() and p.suffix != ".lock"
        and not p.is_relative_to(data)
    }
    (home / "original.json").write_text(json.dumps(original))
    old_store = {str(p.relative_to(data)): p.read_bytes().hex()
                 for p in data.rglob("*") if p.is_file() and p.suffix != ".lock"}
    (home / "old-store.json").write_text(json.dumps(old_store))


# Automate an actual native dialog button; never substitute the dialog or API.
def answer(accept):
    if sys.platform == "darwin":
        import AppKit
        from PyObjCTools import AppHelper

        done = threading.Event()

        def click():
            modal = AppKit.NSApplication.sharedApplication().modalWindow()
            if modal is None:
                return

            def visit(view):
                if isinstance(view, AppKit.NSButton):
                    key = view.keyEquivalent()
                    if key == ("\r" if accept else "\x1b"):
                        view.performClick_(None)
                        done.set()
                        return True
                return any(visit(child) for child in view.subviews())

            visit(modal.contentView())

        def attempt():
            AppHelper.callAfter(click)
            return done.wait(0.05)

        wait(attempt, "Cocoa confirmation button")
    else:
        import ctypes
        from ctypes import wintypes

        user = ctypes.windll.user32
        user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowW.restype = wintypes.HWND
        user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetDlgItem.restype = wintypes.HWND
        user.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user.SendMessageW.restype = wintypes.LPARAM

        def click():
            dialog = user.FindWindowW("#32770", "删除应用数据并重新初始化\uff1f")
            if not dialog:
                return False
            button = user.GetDlgItem(dialog, 1 if accept else 2)
            if not button:
                return False
            user.SendMessageW(button, 0xF5, 0, 0)  # BM_CLICK
            return True

        wait(click, "Windows confirmation button")


def observe():
    window = None
    try:
        wait(lambda: bool(webview.windows), "host window")
        window = webview.windows[0]
        if stage == "reset":
            wait(
                lambda: window.evaluate_js(
                    "Boolean(document.querySelector('[onclick=\"resetData()\"]'))"
                ),
                "backend-independent recovery page",
            )
            assert str(paths.state) in window.evaluate_js("document.body.innerText")
            checks.append("unsupported-format recovery page visible before backend")
            window.evaluate_js("document.getElementById('skip-backup').checked = true")
            window.evaluate_js(
                "document.querySelector('[onclick=\"resetData()\"]').click(); true"
            )
            answer(False)
            wait(
                lambda: window.evaluate_js(
                    "!document.querySelector('[onclick=\"resetData()\"]').disabled"
                ),
                "cancel completed",
            )
            assert not (paths.state / "data-reset.json").exists()
            old_store = json.loads((home / "old-store.json").read_text())
            for name, content in old_store.items():
                original_file = paths.state / "runtime/.scopecat" / name
                assert original_file.read_bytes().hex() == content
            checks.append("native cancel leaves original store unchanged")
            window.evaluate_js("document.getElementById('skip-backup').checked = true")
            window.evaluate_js(
                "document.querySelector('[onclick=\"resetData()\"]').click(); true"
            )
            answer(True)
        wait(
            lambda: (
                window.get_current_url()
                and window.get_current_url().startswith("http://")
            ),
            "in-place backend navigation",
        )
        runtime = ApplicationRuntime(paths.state)
        assert runtime.home == paths.state
        assert runtime.status().state == "running"
        checks.append("in-place backend running")
        with sqlite3.connect(paths.state / "runtime/.scopecat/control.sqlite3") as db:
            assert (db.execute("SELECT version FROM project_schema").fetchone()[0]
                    == PROJECT_SCHEMA_VERSION)
            assert db.execute(
                "SELECT name FROM sqlite_master WHERE name='old_science'"
            ).fetchone() is None
        checks.append("current empty schema replaced the old database")
        original = json.loads((home / "original.json").read_text())
        for relative, content in original.items():
            assert (paths.state / relative).read_bytes().hex() == content, relative
        checks.append("protected source and installation files unchanged")
        result["selected"] = str(runtime.home)
        result["status"] = "passed"
    except BaseException:
        result["status"] = "failed"
        result["error"] = traceback.format_exc()
    finally:
        (home / (stage + ".json")).write_text(json.dumps(result, indent=2))
        if window is not None:
            window._js_api.exit(False)


threading.Thread(target=observe, daemon=True).start()
desktop.run(paths.state, prepare=prepare, prepare_reset=prepare)
assert result.get("status") == "passed", result
"""


def retain_diagnostics(reports: Path) -> None:
    anchor = reports / "fixture/data"
    sources = {"desktop.log": anchor / "desktop/desktop.log"}
    sources["daemon.log"] = anchor / "runtime/.scopecat/daemon.log"
    for name, source in sources.items():
        if source.is_file():
            shutil.copyfile(source, reports / name)


def verify(app: Path, home: Path) -> None:
    require_hosted_runner(home)
    if home.is_relative_to(app):
        raise ValueError("Acceptance home must be outside the application")
    reports = home / "reset-recovery"
    reports.mkdir(exist_ok=False)
    original_package = ApplicationRuntime(home / "data").installation()
    with tempfile.TemporaryDirectory(prefix="host-", dir=reports) as directory:
        copied = Path(directory) / app.name
        shutil.copytree(app, copied, symlinks=True)
        resources = copied / (
            "Contents/Resources" if sys.platform == "darwin" else "resources"
        )
        executable = copied / (
            "Contents/MacOS/Scopecat" if sys.platform == "darwin" else "Scopecat.exe"
        )
        probe = reports / "probe.py"
        probe.write_text(HOST, encoding="utf-8")
        results = {}
        passed = False
        failure = None
        cleanup_error = None
        try:
            for stage in ("reset", "reopen"):
                (resources / "bootstrap.py").write_text(
                    host_bootstrap(
                        probe,
                        [str(reports), str(resources / "payload"), stage],
                        reports / f"{stage}-python.log",
                    ),
                    encoding="utf-8",
                )
                run_bounded(
                    [str(executable), "--check-result"],
                    reports / f"{stage}.log",
                    timeout=180,
                )
                results[stage] = json.loads((reports / f"{stage}.json").read_text())
                assert results[stage]["status"] == "passed"
            assert results["reset"]["selected"] == results["reopen"]["selected"]
            passed = True
        except BaseException:
            failure = traceback.format_exc()
            raise
        finally:
            # The backend may be detached even when a GUI probe times out.
            cleanup = (
                "import json,sys\nfrom pathlib import Path\n"
                "from lab_tools.application_runtime import ApplicationRuntime\n"
                "root=Path(sys.argv[1])\nruntime=ApplicationRuntime(root)\n"
                "runtime.stop()\n"
            )
            try:
                run_bounded(
                    [
                        str(original_package.python),
                        "-I",
                        "-B",
                        "-c",
                        cleanup,
                        str(reports / "fixture/data"),
                    ],
                    reports / "cleanup.log",
                    timeout=45,
                )
            except BaseException:
                cleanup_error = traceback.format_exc()
                if failure is None:
                    raise
            finally:
                (reports / "result.json").write_text(
                    json.dumps(
                        {
                            "status": "passed"
                            if passed and not cleanup_error
                            else "failed",
                            "error": failure,
                            "cleanup_error": cleanup_error,
                            "probe_sha256": file_hash(Path(__file__)),
                            "stages": results,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                retain_diagnostics(reports)


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
