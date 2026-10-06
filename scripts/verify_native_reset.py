"""Bounded fresh-start recovery using the real host, WebView and native dialog."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.bundle import file_hash
from verify_native_windows import (  # pyright: ignore[reportImplicitRelativeImport]
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


def fresh(destination):
    from dataclasses import replace

    native_bootstrap.prepare(args, replace(paths, state=destination))


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
    }
    (home / "original.json").write_text(json.dumps(original))


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
            dialog = user.FindWindowW("#32770", "保留旧数据\uff0c重新开始\uff1f")
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
            assert not (paths.state / "reset-attempt.json").exists()
            checks.append("native cancel creates no candidate")
            window.evaluate_js(
                "document.querySelector('[onclick=\"resetData()\"]').click(); true"
            )
            answer(True)
        wait(
            lambda: (
                window.get_current_url()
                and window.get_current_url().startswith("http://")
            ),
            "fresh workbench",
        )
        runtime = ApplicationRuntime(paths.state)
        assert runtime.home != paths.state
        assert runtime.status().state == "running"
        checks.append("fresh workbench running at remembered space")
        original = json.loads((home / "original.json").read_text())
        for relative, content in original.items():
            assert (paths.state / relative).read_bytes().hex() == content, relative
        checks.append("all original fixture files unchanged including WAL and source")
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
desktop.run(paths.state, prepare=prepare, prepare_fresh=fresh)
assert result.get("status") == "passed", result
"""


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
        try:
            for stage in ("reset", "reopen"):
                (resources / "bootstrap.py").write_text(
                    "import runpy, sys\n"
                    f"sys.argv = [{str(probe)!r}, {str(reports)!r}, "
                    f"{str(resources / 'payload')!r}, {stage!r}]\n"
                    f"runpy.run_path({str(probe)!r}, run_name='__main__')\n",
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
        finally:
            # The candidate may be detached even when a GUI probe times out.
            cleanup = (
                "import json,sys\nfrom pathlib import Path\n"
                "from lab_tools.application_runtime import ApplicationRuntime\n"
                "root=Path(sys.argv[1])\nruntime=ApplicationRuntime(root)\n"
                "runtime.stop()\n"
                "journal=root/'reset-attempt.json'\n"
                "if journal.exists():\n"
                " attempt=json.loads(journal.read_text())\n"
                " candidate_home=root/'spaces'/attempt['space']\n"
                " candidate=ApplicationRuntime(candidate_home)\n"
                " if candidate.selection.exists(): candidate.stop()\n"
            )
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
            (reports / "result.json").write_text(
                json.dumps(
                    {
                        "status": "passed" if passed else "failed",
                        "probe_sha256": file_hash(Path(__file__)),
                        "stages": results,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
