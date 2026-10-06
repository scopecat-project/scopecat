"""Bounded production-wheel storage acceptance on disposable hosted macOS only."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast, override

import psutil

from lab_tools.bundle import file_hash
from scopecat_server.validation_process import (  # noqa: TID251 - owned acceptance hosts
    terminate_validation_process_tree,
)
from verify_native_windows import (  # pyright: ignore[reportImplicitRelativeImport]
    environment,
    require_hosted_runner,
    wait_for,
)

# Runs inside an unchanged packaged native launcher and Python environment.
# No production backend, store selection or lifecycle hook is replaced here.
HOST = r"""
import hashlib, importlib.metadata, json, sys, threading, time, traceback
from pathlib import Path
from verify_native_windows import require_hosted_runner
require_hosted_runner(Path(sys.argv[2]).parent)

import AppKit, Foundation, WebKit
from PyObjCTools import AppHelper
import webview
from webview.platforms import cocoa
from lab_tools.cocoa_dependency import RESULT_SHA256, VERSION

stage, output, origin = sys.argv[1:]
output = Path(output)
checks = []
result = {"stage": stage, "origin": origin, "checks": checks}


def wait(check, label):
    end = time.monotonic() + 30
    while not check():
        if time.monotonic() > end:
            raise TimeoutError(label)
        time.sleep(0.05)


def main_call(fn):
    done = threading.Event()
    values = []

    def invoke():
        try:
            values.append((True, fn()))
        except BaseException as exc:
            values.append((False, exc))
        finally:
            done.set()

    AppHelper.callAfter(invoke)
    if not done.wait(30):
        raise TimeoutError("Cocoa main-thread operation")
    ok, value = values[0]
    if not ok:
        raise value
    return value


def cookie(name, value):
    expires = Foundation.NSDate.dateWithTimeIntervalSinceNow_(600)
    return Foundation.NSHTTPCookie.cookieWithProperties_(
        {
            Foundation.NSHTTPCookieName: name,
            Foundation.NSHTTPCookieValue: value,
            Foundation.NSHTTPCookieDomain: "127.0.0.1",
            Foundation.NSHTTPCookiePath: "/",
            Foundation.NSHTTPCookieExpires: expires,
        }
    )


# Before any private WebView exists, write and read back both real legacy stores.
# Pump the main run loop for the asynchronous WK cookie API; no fake completion.
legacy = WebKit.WKWebsiteDataStore.defaultDataStore()
ns = Foundation.NSHTTPCookieStorage.sharedHTTPCookieStorage()


def pump(operation):
    values = []
    operation(lambda *args: values.append(args))
    end = time.monotonic() + 30
    while not values:
        if time.monotonic() > end:
            raise TimeoutError("legacy cookie callback")
        Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
            Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.01)
        )
    return values[0]


def names(cookies):
    return {str(c.name()): str(c.value()) for c in cookies}


assert importlib.metadata.version("pywebview") == VERSION
assert hashlib.sha256(Path(cocoa.__file__).read_bytes()).hexdigest() == RESULT_SHA256
assert AppKit.NSBundle.mainBundle().bundleIdentifier() == "org.scopecat.desktop"
result["pywebview"] = VERSION
result["cocoa_sha256"] = RESULT_SHA256
if stage == "A":
    ns.setCookie_(cookie("scopecat_ns_sentinel", "retained"))
    pump(
        lambda cb: legacy.httpCookieStore().setCookie_completionHandler_(
            cookie("scopecat_default_sentinel", "retained"), cb
        )
    )
    assert names(ns.cookies()).get("scopecat_ns_sentinel") == "retained"
    assert (
        names(pump(legacy.httpCookieStore().getAllCookies_)[0]).get(
            "scopecat_default_sentinel"
        )
        == "retained"
    )
    checks.append("legacy sentinels seeded and read back before private windows")

windows = [webview.create_window("Scopecat storage " + stage, origin, hidden=True)]
failure = []


def js(window, code):
    return window.evaluate_js(code)


def cookies(window):
    return {
        key: morsel.value
        for item in window.get_cookies()
        for key, morsel in item.items()
    }


def no_import(window):
    assert "sentinel" not in js(window, "document.cookie")
    assert not any("sentinel" in key for key in cookies(window))
    js(
        window,
        "window.__echo = null; fetch('/echo').then(r => r.text())"
        ".then(v => window.__echo = v)",
    )
    wait(lambda: js(window, "window.__echo") is not None, "cookie echo")
    assert "sentinel" not in js(window, "window.__echo")


def actual(window):
    view = cocoa.BrowserView.instances[window.uid]
    store = view.webview.configuration().websiteDataStore()
    assert not store.isPersistent()
    assert store == view.datastore == cocoa.BrowserView._private_datastore
    return store


def exercise():
    try:
        first = windows[0]
        assert first.events.loaded.wait(30)
        main_call(lambda: actual(first))
        no_import(first)
        assert js(first, "localStorage.getItem('scopecat_host_marker')") is None
        assert "scopecat_host_cookie" not in cookies(first)
        checks.append("actual private store starts empty; legacy cookies not imported")
        if stage == "C":
            checks.append("same-origin restart does not retain previous host data")
        else:
            js(
                first,
                "localStorage.setItem('scopecat_host_marker', "
                + json.dumps(stage)
                + "); "
                "document.cookie = 'scopecat_host_cookie=" + stage + "; path=/'",
            )
            wait(
                lambda: cookies(first).get("scopecat_host_cookie") == stage,
                "cookie API observes actual browser cookie",
            )
            if stage == "A":
                second = webview.create_window(
                    "Scopecat storage peer", origin, hidden=True
                )
                windows.append(second)
                assert second.events.loaded.wait(30)
                assert main_call(lambda: actual(second) == actual(first))
                assert js(second, "localStorage.getItem('scopecat_host_marker')") == "A"
                assert cookies(second).get("scopecat_host_cookie") == "A"
                js(second, "localStorage.setItem('scopecat_peer_marker', 'peer')")
                assert (
                    js(first, "localStorage.getItem('scopecat_peer_marker')") == "peer"
                )
                checks.append("two native windows share actual store bidirectionally")
                (output / "A-ready").touch()
                wait(lambda: (output / "B-ready").exists(), "second host writes")
                assert js(first, "localStorage.getItem('scopecat_host_marker')") == "A"
                assert cookies(first).get("scopecat_host_cookie") == "A"
                (output / "B-clear").touch()
                wait(lambda: (output / "B-done").exists(), "second host clear")
                assert js(first, "localStorage.getItem('scopecat_host_marker')") == "A"
                assert cookies(first).get("scopecat_host_cookie") == "A"
                checks.append(
                    "overlapping same-origin host writes and clears are isolated"
                )
                observed = []
                main_call(
                    lambda: legacy.httpCookieStore().getAllCookies_(
                        lambda found: observed.append(names(found))
                    )
                )
                wait(lambda: bool(observed), "legacy cookies after private operations")
                assert observed[0].get("scopecat_default_sentinel") == "retained"
                assert (
                    main_call(lambda: names(ns.cookies())).get("scopecat_ns_sentinel")
                    == "retained"
                )
                checks.append(
                    "default and NSHTTP sentinels survive private windows and clear"
                )
            else:
                (output / "B-ready").touch()
                wait(lambda: (output / "B-clear").exists(), "peer checked isolation")
                first.clear_cookies()
                # Preserve pywebview clear_cookies semantics: all website data.
                wait(
                    lambda: (
                        js(first, "localStorage.getItem('scopecat_host_marker')")
                        is None
                        and not cookies(first)
                    ),
                    "clear applies to actual private store",
                )
                checks.append("get/clear cookie interfaces target actual private store")
                (output / "B-done").touch()
        result["status"] = "passed"
    except BaseException:
        failure.append(traceback.format_exc())
        result.update(status="failed", error=failure[-1])
    finally:
        (output / (stage + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        for window in reversed(windows):
            window.destroy()


webview.start(exercise)
if failure:
    raise RuntimeError(failure[0])
"""


class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        content = (
            self.headers.get("Cookie", "")
            if self.path == "/echo"
            else "<!doctype html><title>Scopecat storage acceptance</title>"
        ).encode()
        self.send_response(200)
        self.send_header(
            "Content-Type", "text/plain" if self.path == "/echo" else "text/html"
        )
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        _ = self.wfile.write(content)

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


def verify(app: Path, home: Path) -> None:
    require_hosted_runner(home)
    if sys.platform != "darwin":
        raise ValueError("Cocoa storage acceptance requires macOS")
    app, home = app.resolve(), home.resolve()
    reports = home / "cocoa-storage"
    reports.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "status": "failed",
        "probe_sha256": file_hash(Path(__file__)),
        "bundle_sha256": file_hash(app / "Contents/Resources/payload/bundle.json"),
    }
    processes: list[subprocess.Popen[str]] = []
    owners: dict[int, psutil.Process] = {}
    with (
        ThreadingHTTPServer(("127.0.0.1", 0), _Page) as server,
        tempfile.TemporaryDirectory(prefix="hosts-", dir=reports) as temporary,
        contextlib.ExitStack() as stack,
    ):
        origin = f"http://127.0.0.1:{server.server_port}"
        result["origin"] = origin
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def launch(stage: str) -> subprocess.Popen[str]:
            contents = Path(temporary) / stage / "Scopecat.app/Contents"
            resources = contents / "Resources"
            resources.mkdir(parents=True)
            (contents / "MacOS").mkdir()
            executable = contents / "MacOS/Scopecat"
            shutil.copy2(app / "Contents/MacOS/Scopecat", executable)
            shutil.copy2(app / "Contents/Info.plist", contents / "Info.plist")
            (resources / "python").symlink_to(
                app / "Contents/Resources/python", target_is_directory=True
            )
            (resources / "bootstrap.py").write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                f"sys.argv = ['storage', {stage!r}, {str(reports)!r}, {origin!r}]\n"
                + HOST,
                encoding="utf-8",
            )
            log = stack.enter_context(
                (reports / f"{stage}.log").open("w", encoding="utf-8")
            )
            process = subprocess.Popen(  # noqa: S603 - owned packaged host
                [str(executable), "--check-result"],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment(),
                text=True,
            )
            processes.append(process)
            with contextlib.suppress(psutil.NoSuchProcess):
                owners[process.pid] = psutil.Process(process.pid)
            return process

        def gate(name: str) -> None:
            def ready() -> bool:
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError(f"Host exited before {name}; inspect stage logs")
                return (reports / name).exists()

            wait_for(ready, name, timeout=75)

        def finish(process: subprocess.Popen[str], stage: str) -> None:
            if process.wait(timeout=90) != 0:
                raise RuntimeError(f"Storage host {stage} failed; inspect stage log")
            document = cast(
                "dict[str, object]",
                json.loads((reports / f"{stage}.json").read_bytes()),
            )
            assert document["status"] == "passed"

        try:
            first = launch("A")
            gate("A-ready")
            second = launch("B")
            finish(second, "B")
            finish(first, "A")
            finish(launch("C"), "C")
            result["status"] = "passed"
        except Exception:
            result["error"] = traceback.format_exc()
            raise
        finally:
            cleanup_errors: list[str] = []
            for process in processes:
                if process.poll() is None:
                    try:
                        terminate_validation_process_tree(
                            process, owner=owners.get(process.pid)
                        )
                    except Exception:
                        cleanup_errors.append(traceback.format_exc())
            server.shutdown()
            if cleanup_errors:
                result.update(status="failed", cleanup_errors=cleanup_errors)
            (reports / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            if cleanup_errors:
                raise RuntimeError("Cocoa storage host cleanup failed; inspect result")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
