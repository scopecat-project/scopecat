"""Real WebView2 lifecycle and application-draft recovery on hosted Windows."""

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

from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.bundle import file_hash
from scopecat_server.validation_process import (  # noqa: TID251 - owned acceptance hosts
    terminate_validation_process_tree,
)
from verify_native_windows import (  # pyright: ignore[reportImplicitRelativeImport]
    environment,
    read_decision_draft,
    require_hosted_runner,
    wait_for,
)

# Original packaged launcher/backend; only the acceptance bootstrap is replaced.
HOST = r"""
import hashlib, importlib.metadata, json, sys, time, traceback
from pathlib import Path
from urllib.parse import urlencode
from verify_native_windows import require_hosted_runner, read_decision_draft

require_hosted_runner(Path(sys.argv[2]).parent)
assert sys.platform == "win32"
import webview
from webview.platforms import winforms, edgechromium
from System import Action

stage, output, origin, base_url = sys.argv[1:]
output = Path(output)
fixture = json.loads((output / "draft-fixture.json").read_text())
checks = []
result = {"stage": stage, "origin": origin, "checks": checks,
          "pywebview": importlib.metadata.version("pywebview"),
          "edgechromium_sha256": hashlib.sha256(
              Path(edgechromium.__file__).read_bytes()).hexdigest(),
          "winforms_sha256": hashlib.sha256(
              Path(winforms.__file__).read_bytes()).hexdigest()}
windows = [webview.create_window("Scopecat storage " + stage, origin, hidden=True)]
failure = []


def wait(check, label):
    end = time.monotonic() + 30
    while not check():
        if time.monotonic() > end:
            raise TimeoutError(label)
        time.sleep(0.05)


def js(window, code):
    return window.evaluate_js(code)


def cookies(window):
    return {key: morsel.value for item in window.get_cookies()
            for key, morsel in item.items()}


def actual(window):
    form = winforms.BrowserView.instances[window.uid]
    values = []
    def inspect():
        core = form.browser.webview.CoreWebView2
        values.append({"private": bool(core.Profile.IsInPrivateModeEnabled),
                       "user_data_folder": str(core.Environment.UserDataFolder),
                       "browser_version": str(core.Environment.BrowserVersionString)})
    form.Invoke(Action(inspect))
    assert values[0]["private"]
    return values[0]


def observe(window, expected, label):
    # Retain the observed values even when agreement/isolation assertions fail.
    native = cookies(window)
    document = js(window, "document.cookie")
    marker = js(window, "localStorage.getItem('scopecat_host_marker')")
    js(window, "window.__echo = null; fetch('/echo', {cache: 'no-store'})"
       ".then(r => r.text()).then(v => window.__echo = v)")
    wait(lambda: js(window, "window.__echo") is not None, "cookie echo")
    echo = js(window, "window.__echo")
    observed = {"cookies": native, "document": document,
                "http": echo, "marker": marker}
    result[label] = observed
    assert native.get("scopecat_host_cookie") == expected, observed
    for value in (document, echo):
        if expected is None:
            assert "scopecat_host_cookie=" not in value, observed
        else:
            assert "scopecat_host_cookie=" + expected in value, observed
    return observed


def exercise():
    try:
        assert result["pywebview"] == "6.2.1"
        first = windows[0]
        assert first.events.loaded.wait(30)
        result["actual_store"] = actual(first)
        assert read_decision_draft(base_url, fixture["target"]) == fixture["view"]
        result["application_draft"] = fixture["view"]
        observe(first, None, "initial")
        assert result["initial"]["marker"] is None
        checks.append("actual private WebView2 starts empty at fixed origin")
        if stage == "C":
            checks.append("same-origin restart retains no old marker or cookie")
            query = urlencode({"procedure": fixture["target"]["procedure_run_id"]})
            first.load_url(base_url + "/?" + query + "#decisions")
            selector = 'input[placeholder="name, agent id, or service id"]'
            wait(lambda: js(first, "document.querySelector(" + json.dumps(selector)
                            + ")?.value") == fixture["actor"],
                 "draft restored in restarted native UI")
            assert fixture["target"]["procedure_run_id"] in js(
                first, "document.body.textContent")
            assert read_decision_draft(base_url, fixture["target"]) == fixture["view"]
            checks.append("exact application draft and real form recover after restart")
        else:
            js(first, "localStorage.setItem('scopecat_host_marker', "
               + json.dumps(stage) + "); document.cookie = 'scopecat_host_cookie="
               + stage + "; path=/; max-age=600'")
            observe(first, stage, "seeded")
            assert result["seeded"]["marker"] == stage
            if stage == "A":
                (output / "A-ready").touch()
                wait(lambda: (output / "B-ready").exists(), "second host writes")
                observe(first, "A", "after_other_host_write")
                assert result["after_other_host_write"]["marker"] == "A"
                (output / "B-clear").touch()
                wait(lambda: (output / "B-done").exists(), "second host clear")
                observe(first, "A", "after_other_host_clear")
                assert result["after_other_host_clear"]["marker"] == "A"
                checks.append("overlapping same-origin hosts isolate writes and clear")
            else:
                (output / "B-ready").touch()
                wait(lambda: (output / "B-clear").exists(), "peer checked isolation")
                first.clear_cookies()
                wait(lambda: not cookies(first), "native cookie deletion")
                observe(first, None, "cleared")
                # WebView2 DeleteAllCookies does not delete localStorage.
                assert result["cleared"]["marker"] == "B"
                checks.append("native cookie clearing preserves WebView2 localStorage")
                (output / "B-done").touch()
        result["status"] = "passed"
    except BaseException:
        failure.append(traceback.format_exc())
        result.update(status="failed", error=failure[-1])
    finally:
        (output / (stage + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        for window in reversed(windows):
            window.destroy()


# Same default backend/private-mode selection as desktop.main; no alternate store.
webview.start(exercise)
assert result.get("status") == "passed", result
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


def finish_host(
    process: subprocess.Popen[str], stage: str, reports: Path, *, timeout: float = 90
) -> dict[str, object]:
    """A process exit is insufficient: require its complete, matching stage report."""
    if process.wait(timeout=timeout) != 0:
        raise RuntimeError(f"Storage host {stage} failed; inspect stage log")
    document = cast(
        "dict[str, object]", json.loads((reports / f"{stage}.json").read_bytes())
    )
    assert document["status"] == "passed" and document["stage"] == stage
    return document


def verify(app: Path, home: Path) -> None:
    require_hosted_runner(home)
    if sys.platform != "win32":
        raise ValueError("WebView2 storage acceptance requires Windows")
    app, home = app.resolve(), home.resolve()
    if home.is_relative_to(app):
        raise ValueError("Acceptance home must be outside the application")
    reports = home / "windows-storage"
    reports.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {
        "status": "failed",
        "probe_sha256": file_hash(Path(__file__)),
        "fixture_probe_sha256": file_hash(
            Path(__file__).with_name("verify_native_windows.py")
        ),
        "native_identity": json.loads(
            (home / "native-windows/identity.json").read_bytes()
        ),
    }
    runtime = ApplicationRuntime(home / "data")
    prior = cast(
        "dict[str, object]",
        json.loads((home / "native-windows/result.json").read_bytes()),
    )
    assert prior["status"] == "passed"
    target = cast("dict[str, str | int]", prior["draft_target"])
    storage = cast("dict[str, object]", prior["storage"])
    expected = cast("dict[str, object]", storage["draft_before"])
    draft = cast("dict[str, object]", expected["draft"])
    actor = cast("dict[str, object]", draft["input"])["actor"]
    (reports / "draft-fixture.json").write_text(
        json.dumps(
            {
                "target": target,
                "view": expected,
                "actor": actor,
            },
            indent=2,
        )
        + "\n"
    )
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

        copied = Path(temporary) / app.name
        shutil.copytree(app, copied)
        resources = copied / "resources"
        executable = copied / "Scopecat.exe"

        def launch(stage: str, base_url: str) -> subprocess.Popen[str]:
            (resources / "bootstrap.py").write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                f"sys.argv = ['storage', {stage!r}, {str(reports)!r}, "
                f"{origin!r}, {base_url!r}]\n" + HOST,
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

        try:
            before = runtime.start()
            assert read_decision_draft(before.base_url, target) == expected
            result["service_before"] = {
                "pid": before.pid,
                "created": before.process_create_time,
                "base_url": before.base_url,
            }
            first = launch("A", before.base_url)
            gate("A-ready")
            second = launch("B", before.base_url)
            second_report = finish_host(second, "B", reports)
            first_report = finish_host(first, "A", reports)
            result["host_exits"] = {"A": first.returncode, "B": second.returncode}
            stores = [
                cast("dict[str, object]", report["actual_store"])
                for report in (first_report, second_report)
            ]
            assert stores[0]["user_data_folder"] != stores[1]["user_data_folder"]
            runtime.stop()
            assert not psutil.pid_exists(before.pid) or (
                psutil.Process(before.pid).create_time() != before.process_create_time
            )
            after = runtime.start()
            result["service_after"] = {
                "pid": after.pid,
                "created": after.process_create_time,
                "base_url": after.base_url,
            }
            assert read_decision_draft(after.base_url, target) == expected
            assert (after.pid, after.process_create_time) != (
                before.pid,
                before.process_create_time,
            )
            third = launch("C", after.base_url)
            third_report = finish_host(third, "C", reports)
            result["host_exits"] = {
                "A": first.returncode,
                "B": second.returncode,
                "C": third.returncode,
            }
            restarted = cast("dict[str, object]", third_report["actual_store"])
            assert all(
                restarted["user_data_folder"] != store["user_data_folder"]
                for store in stores
            )
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
            try:
                runtime.stop()
            except Exception:
                cleanup_errors.append(traceback.format_exc())
            server.shutdown()
            if cleanup_errors:
                result.update(status="failed", cleanup_errors=cleanup_errors)
            (reports / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            if cleanup_errors:
                raise RuntimeError(
                    "WebView2 storage host cleanup failed; inspect result"
                )


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
