"""Probe failure handling and real SDK gate contracts; not native UI evidence."""

import ast
import json
import os
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, runtime_checkable

import httpx2
import psutil
import pytest

from scopecat.daemon.views import RunDetail, RunSummaryPage
from scopecat_app.application_runtime import ApplicationRuntime
from scopecat_server.scaffold import write_author_scaffold

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/verify_native_windows.py"
spec = spec_from_file_location("verify_native_windows", SCRIPT)
assert spec is not None and spec.loader is not None
probe = module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("business_status", ["passed", "failed"])
@pytest.mark.parametrize("exit_fails", [False, True])
def test_probe_persists_business_result_before_normal_exit(
    tmp_path, business_status, exit_fails
):
    output = tmp_path / "result.json"
    evidence = {"status": business_status, "error": "original business evidence"}

    def close():
        saved = json.loads(output.read_text())
        assert saved["business_status"] == business_status
        assert saved["exit_status"] == "pending"
        if exit_fails:
            raise ValueError("save preflight refused")

    if exit_fails:
        with pytest.raises(ValueError, match="save preflight refused"):
            probe.finish_probe(evidence, output, close)
    else:
        probe.finish_probe(evidence, output, close)
    saved = json.loads(output.read_text())
    assert saved["error"] == "original business evidence"
    assert saved["business_status"] == business_status
    assert saved["exit_status"] == ("failed" if exit_fails else "passed")
    assert saved["status"] == ("failed" if exit_fails else business_status)


@pytest.mark.parametrize("refuse", [False, True])
def test_probe_normal_exit_uses_preflight_and_joins_request_before_destroy(
    tmp_path: Path, refuse: bool
):
    import threading
    from contextlib import contextmanager

    from scopecat_app.desktop_session import DesktopSession

    session = DesktopSession(ApplicationRuntime(tmp_path), threading.Event())
    events = []

    @contextmanager
    def preflight():
        events.append("preflight")
        if refuse:
            raise ValueError("input not saved")
        yield
        events.append("saved")

    session.close_preflight = preflight

    def destroy():
        assert session.closing.is_set()
        assert session._exit_thread is not None
        assert not session._exit_thread.is_alive()
        events.append("destroy")

    if refuse:
        with pytest.raises(ValueError, match="input not saved"):
            probe.close_probe_windows(session, SimpleNamespace(destroy=destroy))
        assert events == ["preflight"]
        assert not session.closing.is_set()
    else:
        probe.close_probe_windows(session, SimpleNamespace(destroy=destroy))
        assert events == ["preflight", "saved", "destroy"]


def reset_host_function(name: str) -> ast.FunctionDef:
    script = SCRIPT.with_name("verify_native_reset.py")
    source = ast.parse(script.read_text())
    host = next(
        ast.literal_eval(node.value)
        for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "HOST" for t in node.targets)
    )
    return next(
        node
        for node in ast.parse(host).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


@pytest.mark.parametrize(
    ("connection", "loading", "expected"),
    [
        (None, False, False),
        ("Connecting", False, False),
        ("Disconnected", False, False),
        ("Ready", True, False),
        ("Ready", False, True),
    ],
)
def test_reset_readiness_requires_health_completion(connection, loading, expected):
    function = reset_host_function("workspace_ready")
    code = compile(ast.Module(body=[function], type_ignores=[]), "reset-ready", "exec")
    scope = {}
    exec(code, scope)  # noqa: S102 - repository-owned readiness predicate
    window = SimpleNamespace(
        evaluate_js=lambda _: {
            "mounted": True,
            "connection": connection,
            "loading": loading,
        }
    )
    assert bool(scope["workspace_ready"](window)) is expected


@pytest.mark.parametrize("exit_fails", [False, True])
def test_reset_exit_retains_business_result_before_wait_and_preflight(
    tmp_path, exit_fails
):
    # Execute the actual generated observer's finally block without a native GUI.
    import traceback

    observer = reset_host_function("observe")
    final = observer.body[-1]
    assert isinstance(final, ast.Try)
    body = final.finalbody
    code = compile(ast.Module(body=body, type_ignores=[]), "reset-exit", "exec")
    result = {"status": "failed", "error": "original business failure"}
    output = tmp_path / "reset.json"
    calls = []

    def wait(check, label):
        assert json.loads(output.read_text())["business_status"] == "failed"
        assert check()
        calls.append("ready")

    def exit_application(background):
        assert background is False
        calls.append("exit")
        if exit_fails:
            raise ValueError("save rejected")

    scope = {
        "home": tmp_path,
        "stage": "reset",
        "result": result,
        "json": json,
        "traceback": traceback,
        "wait": wait,
        "workspace_ready": lambda _: True,
        "window": SimpleNamespace(
            evaluate_js=lambda _: True,
            _js_api=SimpleNamespace(exit=exit_application),
        ),
    }
    if exit_fails:
        with pytest.raises(ValueError, match="save rejected"):
            exec(code, scope)  # noqa: S102 - repository-owned probe block
    else:
        exec(code, scope)  # noqa: S102 - repository-owned probe block
    saved = json.loads(output.read_text())
    assert saved["error"] == "original business failure"
    assert saved["business_status"] == saved["status"] == "failed"
    assert saved["exit_status"] == ("failed" if exit_fails else "requested")
    assert calls == ["ready", "exit"]


def test_native_bootstrap_captures_failure_without_inherited_output(tmp_path: Path):
    script = tmp_path / "failure.py"
    script.write_text(
        "import logging\n"
        "logging.error('WebView initialization failed')\n"
        "raise RuntimeError('early host failure')\n"
    )
    log = tmp_path / "python.log"
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(probe.host_bootstrap(script, [], log))
    result = subprocess.run(  # noqa: S603 - fixed disposable failure fixture
        [sys.executable, str(bootstrap)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode != 0
    assert "WebView initialization failed" in log.read_text()
    assert "RuntimeError: early host failure" in log.read_text()


def test_probe_wait_has_a_deadline():
    with pytest.raises(TimeoutError, match="bridge unavailable"):
        probe.wait_for(lambda: False, "bridge unavailable", timeout=0)


def test_probe_nonzero_exit_preserves_diagnostic(tmp_path: Path):
    log = tmp_path / "host.log"
    with pytest.raises(subprocess.CalledProcessError):
        probe.run_bounded(
            [sys.executable, "-c", "print('native failure', flush=True); exit(7)"],
            log,
            timeout=5,
        )
    assert "native failure" in log.read_text()


def test_probe_timeout_reaps_host_and_owned_child(tmp_path: Path):
    marker = tmp_path / "pids.json"
    code = """
import json, os, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
child.wait()
"""
    with pytest.raises(subprocess.TimeoutExpired):
        probe.run_bounded(
            [sys.executable, "-c", code, str(marker)],
            tmp_path / "host.log",
            timeout=2,
        )
    for pid in json.loads(marker.read_text()):
        assert not psutil.pid_exists(pid)


def test_cleanup_after_host_crash_uses_recorded_client_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reports = tmp_path / "native-windows"
    reports.mkdir()
    receipt = reports / "acquisition-process.json"
    code = """
import json, subprocess, sys, psutil
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path(sys.argv[1]).write_text(json.dumps({
    'pid': child.pid, 'created': psutil.Process(child.pid).create_time()}))
raise SystemExit(7)
"""
    with pytest.raises(subprocess.CalledProcessError):
        probe.run_bounded(
            [sys.executable, "-c", code, str(receipt)], reports / "host.log", timeout=5
        )
    identity = json.loads(receipt.read_text())
    stopped = []
    monkeypatch.setattr(
        probe,
        "ApplicationRuntime",
        lambda _: SimpleNamespace(stop=lambda: stopped.append(True)),
    )
    try:
        assert psutil.pid_exists(identity["pid"])
        # Never kill a recycled PID merely because the recorded number matches.
        receipt.write_text(json.dumps({**identity, "created": identity["created"] - 1}))
        probe.cleanup(tmp_path)
        assert psutil.pid_exists(identity["pid"])
    finally:
        receipt.write_text(json.dumps(identity))
        probe.cleanup(tmp_path)
    assert not psutil.pid_exists(identity["pid"])
    assert stopped == [True, True]


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("GITHUB_ACTIONS", "false"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("RUNNER_OS", "Linux"),
        ("RUNNER_TEMP", "relative-home"),
        ("RUNNER_TEMP", ""),
    ],
)
def test_probe_refuses_local_or_mismatched_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting, value
):
    hosted_runner(tmp_path, monkeypatch)
    monkeypatch.setenv(setting, value)
    with pytest.raises(RuntimeError, match="default website store"):
        probe.require_hosted_runner(tmp_path / "home")


def hosted_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("RUNNER_OS", "Windows")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))


def test_probe_requires_home_below_disposable_runner_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    hosted_runner(tmp_path, monkeypatch)
    probe.require_hosted_runner(tmp_path / "home")
    for home in (tmp_path, tmp_path.parent / "outside"):
        with pytest.raises(RuntimeError, match="RUNNER_TEMP"):
            probe.require_hosted_runner(home)


@pytest.mark.parametrize("fail_cleanup", [False, True])
@pytest.mark.parametrize("host_failure", ["exit", "timeout", "teardown"])
def test_host_failure_overrides_partial_pass_and_retains_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_cleanup, host_failure
):
    hosted_runner(tmp_path, monkeypatch)
    app, home = tmp_path / "app", tmp_path / "home"
    (app / "resources").mkdir(parents=True)
    bootstrap = app / "resources/bootstrap.py"
    bootstrap.write_text("original candidate")
    home.mkdir()
    (app / "bundle.json").write_text("{}")
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(
        probe,
        "ApplicationRuntime",
        lambda _: SimpleNamespace(
            installation=lambda: SimpleNamespace(
                delivery_root=app, python=Path(sys.executable)
            )
        ),
    )
    calls = []

    def run(command, log, *, timeout):
        calls.append(command)
        log.write_text("diagnostic")
        if len(calls) == 1:
            (home / "native-windows/result.json").write_text('{"status":"passed"}')
            if host_failure == "exit":
                raise subprocess.CalledProcessError(7, command)
            if host_failure == "teardown":
                raise RuntimeError("Validation process cleanup incomplete")
            raise subprocess.TimeoutExpired(command, timeout)
        if fail_cleanup:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(probe, "run_bounded", run)
    with pytest.raises(
        (subprocess.TimeoutExpired, subprocess.CalledProcessError, RuntimeError)
    ):
        probe.verify(app, home)
    result = json.loads((home / "native-windows/result.json").read_text())
    assert result["status"] == "failed" and result["host_error"]
    assert result["host_stopped"] is (host_failure != "teardown")
    assert result["host_exit_code"] == (7 if host_failure == "exit" else None)
    assert result["cleanup_completed"] is not fail_cleanup
    assert bool(result["cleanup_error"]) is fail_cleanup
    assert len(calls) == 2 and "--cleanup" in calls[1]
    assert not list((home / "native-windows").glob("host-*"))
    assert (home / "native-windows/host.log").read_text() == "diagnostic"
    assert bootstrap.read_text() == "original candidate"


def test_probe_acquisition_gate_uses_real_service_and_completes(tmp_path: Path):
    """Exercise the probe's fixture/API assertions on Linux without a fake GUI."""
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<p>SDK fixture only</p>")
    runtime = ApplicationRuntime(tmp_path / "data")
    runtime.configure(static_dir=gui)
    authors = tmp_path / "authors"
    write_author_scaffold(authors)
    runtime.register_source(authors, python=Path(sys.executable))
    script = tmp_path / "acquisition.py"
    script.write_text(probe.ACQUISITION)
    child = None
    owner = None
    try:
        record = runtime.start()
        target = probe.prepare_decision(record.base_url)
        assert set(target) == {"procedure_run_id", "step_key", "attempt"}
        assert target["attempt"] == 1
        probe.run_bounded(
            [
                sys.executable,
                "-I",
                "-c",
                # This SDK/service fixture seeds a run without a Notebook kernel.
                # The ordinary template is exercised by the kernel journey.
                """
import sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
_ = project.load_application()
with project.authoring() as author:
    author.refresh()
    from scopecat_lab.authored.parameters import open_parameters
    from scopecat_lab.authored.signal import signal
    prepared = author.prepare(signal(), parameters=open_parameters(author))
    prepared.run().wait(timeout=45).result()
""",
                str(authors),
            ],
            tmp_path / "initial.log",
            timeout=60,
        )
        with (tmp_path / "acquisition.log").open("w") as log:
            child = subprocess.Popen(  # noqa: S603 - fixed isolated acceptance fixture
                [sys.executable, "-I", str(script), str(authors), str(tmp_path)],
                stdout=log,
                stderr=log,
                text=True,
            )
        owner = psutil.Process(child.pid)
        probe.wait_for(
            lambda: (tmp_path / "ready").exists() or child.poll() is not None,
            "gate not reached",
        )
        assert child.poll() is None, (tmp_path / "acquisition.log").read_text()
        run_id = (tmp_path / "ready").read_text()
        with httpx2.Client(base_url=record.base_url, trust_env=False) as http:
            page = RunSummaryPage.model_validate(http.get("/api/v1/runs").json())
            assert len(page.items) == 2 and page.next_cursor is None
            before = RunDetail.model_validate(http.get(f"/api/v1/runs/{run_id}").json())
            assert before.control.state == "leased" and before.snapshot.outcome is None
            assert (tmp_path / "ingests.json").read_text() == "1"
            (tmp_path / "release").touch()
            assert child.wait(timeout=45) == 0
            after = RunDetail.model_validate(http.get(f"/api/v1/runs/{run_id}").json())
            assert after.snapshot.outcome is not None
            assert after.snapshot.outcome.result == "succeeded"
            assert runtime.status().record == record
            assert (tmp_path / "completed").read_text() == run_id
    finally:
        if child is not None and child.poll() is None:
            probe.terminate_validation_process_tree(child, owner=owner)
        runtime.stop()


@runtime_checkable
class WindowsStorageProbe(Protocol):
    """Contract exercised by the freshly loaded native storage script."""

    HOST: str

    def finish_host(
        self,
        process: subprocess.Popen[str],
        stage: str,
        reports: Path,
        *,
        timeout: float = 90,
    ) -> dict[str, object]: ...

    def require_observation(
        self, observed: dict[str, object], cookie: str | None, marker: str | None
    ) -> None: ...

    def host_bootstrap(
        self, stage: str, reports: Path, origin: str, base_url: str
    ) -> str: ...

    def verify(self, app: Path, home: Path) -> None: ...


@pytest.fixture
def windows_storage_probe(monkeypatch: pytest.MonkeyPatch) -> WindowsStorageProbe:
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    storage_spec = spec_from_file_location(
        "verify_windows_storage", SCRIPT.with_name("verify_windows_storage.py")
    )
    assert storage_spec is not None and storage_spec.loader is not None
    storage = module_from_spec(storage_spec)
    storage_spec.loader.exec_module(storage)
    assert isinstance(storage, WindowsStorageProbe)
    return storage


def test_windows_storage_embedded_host_compiles(
    windows_storage_probe: WindowsStorageProbe,
):
    compile(windows_storage_probe.HOST, "windows-storage-host", "exec")


@pytest.mark.parametrize(
    ("exit_code", "report", "error"),
    [
        (7, {"stage": "A", "status": "passed"}, RuntimeError),
        (0, None, FileNotFoundError),
        (0, {"stage": "A", "status": "failed"}, AssertionError),
        (0, {"stage": "B", "status": "passed"}, AssertionError),
    ],
)
def test_windows_storage_rejects_incomplete_host_evidence(
    tmp_path: Path, windows_storage_probe: WindowsStorageProbe, exit_code, report, error
):
    """Failure-report validation only; these child processes are not WebViews."""
    if report is not None:
        (tmp_path / "A.json").write_text(json.dumps(report))
    with (
        subprocess.Popen(  # noqa: S603 - bounded diagnostic child
            [sys.executable, "-c", f"raise SystemExit({exit_code})"], text=True
        ) as process,
        pytest.raises(error),
    ):
        windows_storage_probe.finish_host(process, "A", tmp_path, timeout=5)


def test_windows_storage_host_wait_is_bounded(
    tmp_path: Path, windows_storage_probe: WindowsStorageProbe
):
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], text=True
    ) as process:
        owner = psutil.Process(process.pid)
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                windows_storage_probe.finish_host(process, "A", tmp_path, timeout=0.1)
        finally:
            probe.terminate_validation_process_tree(process, owner=owner)
        assert process.poll() is not None


@pytest.mark.parametrize("surface", ["cookies", "document", "http", "marker"])
def test_windows_storage_cookie_or_marker_loss_fails(
    windows_storage_probe: WindowsStorageProbe, surface
):
    """Validate rejection of recorded loss, not native browser behavior."""
    observed: dict[str, object] = {
        "cookies": {"scopecat_host_cookie": "C"},
        "document": "scopecat_host_cookie=C",
        "http": "scopecat_host_cookie=C",
        "marker": "C",
    }
    windows_storage_probe.require_observation(observed, "C", "C")
    observed[surface] = {} if surface == "cookies" else ""
    with pytest.raises(AssertionError):
        windows_storage_probe.require_observation(observed, "C", "C")


def test_storage_bootstrap_imports_build_evidence_after_relocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_storage_probe: WindowsStorageProbe,
):
    monkeypatch.setattr(
        windows_storage_probe,
        "HOST",
        "from scripts.app_build.windows_dependency import WINFORMS_SHA256\n"
        "from scripts.app_build.cocoa_dependency import RESULT_SHA256\n"
        "assert len(WINFORMS_SHA256) == len(RESULT_SHA256) == 64\n"
        "Path(sys.argv[2], 'imported').touch()\n",
    )
    script = tmp_path / "Relocated application" / "resources" / "bootstrap.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        windows_storage_probe.host_bootstrap("A", tmp_path, "unused", "unused")
    )
    subprocess.run(  # noqa: S603 - isolated build-evidence import check
        [sys.executable, "-I", "-B", str(script)], cwd=tmp_path, check=True
    )
    assert (tmp_path / "imported").exists()


def test_windows_storage_bootstrap_retains_early_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_storage_probe: WindowsStorageProbe,
):
    # Exercise only bootstrap diagnostics in a real child; no native result is mocked.
    monkeypatch.setattr(
        windows_storage_probe, "HOST", "raise ImportError('early host fixture')"
    )
    script = tmp_path / "bootstrap.py"
    script.write_text(
        windows_storage_probe.host_bootstrap("A", tmp_path, "unused", "unused")
    )
    process = subprocess.run(  # noqa: S603 - diagnostic child only
        [sys.executable, "-I", "-B", str(script)], check=False
    )
    assert process.returncode != 0
    report = json.loads((tmp_path / "A.json").read_text())
    assert report["stage"] == "A" and report["status"] == "failed"
    assert "early host fixture" in report["bootstrap_error"]
    assert "early host fixture" in (tmp_path / "A.log").read_text()


def test_windows_storage_aggregate_retains_setup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_storage_probe: WindowsStorageProbe,
):
    hosted_runner(tmp_path, monkeypatch)

    def fail_setup(*args):
        raise RuntimeError("fixture setup failed")

    monkeypatch.setattr(windows_storage_probe, "_verify", fail_setup)
    home = tmp_path / "home"
    with pytest.raises(RuntimeError, match="fixture setup failed"):
        windows_storage_probe.verify(tmp_path / "app", home)
    result = json.loads((home / "windows-storage/result.json").read_text())
    assert result["status"] == "failed" and "fixture setup failed" in result["error"]


def test_window_diagnostics_retains_initialization_failure_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exercise subscriptions/retention only; fake native objects do not qualify UI."""
    import logging

    from webview.event import Event

    path = tmp_path / "window-diagnostics.jsonl"
    diagnostics = probe.WindowDiagnostics(path)
    window = SimpleNamespace()
    names = (
        "initialized",
        "before_show",
        "shown",
        "before_load",
        "loaded",
        "_pywebviewready",
    )
    window.events = SimpleNamespace(**{name: Event(window, True) for name in names})
    control = SimpleNamespace(
        CoreWebView2=None,
        CoreWebView2InitializationCompleted=Event(window, True),
        NavigationCompleted=Event(window, True),
    )
    window.native = SimpleNamespace(browser=SimpleNamespace(webview=control))
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(probe, "browser_processes", lambda: {"processes": []})
    monkeypatch.setattr(
        probe,
        "import_module",
        lambda _: SimpleNamespace(
            CoreWebView2Environment=SimpleNamespace(
                GetAvailableBrowserVersionString=lambda _: "123.4"
            )
        ),
    )
    diagnostics.attach(window)
    window.events.initialized.set("edgechromium")
    window.events.before_show.set()
    window.events.shown.set()
    control.CoreWebView2InitializationCompleted.set(
        None,
        SimpleNamespace(
            IsSuccess=False,
            InitializationException=SimpleNamespace(
                HResult=-2147467260, Message="token=secret"
            ),
        ),
    )
    control.CoreWebView2 = SimpleNamespace(
        BrowserProcessId=42, Environment=SimpleNamespace(BrowserVersionString="123.4")
    )
    control.NavigationCompleted.set(
        None, SimpleNamespace(IsSuccess=False, WebErrorStatus="ConnectionAborted")
    )
    diagnostics.snapshot(window, "before_destroy")
    before = path.read_text()
    entries = [json.loads(line) for line in before.splitlines()]
    assert (
        next(e for e in entries if e["event"] == "core.at_hook")["core_initialized"]
        is False
    )
    assert (
        next(e for e in entries if e["event"] == "runtime")["available_runtime_version"]
        == "123.4"
    )
    failure = next(
        e for e in entries if e["event"] == "CoreWebView2InitializationCompleted"
    )
    assert failure["success"] is False and failure["hresult"] == -2147467260
    navigation = next(e for e in entries if e["event"] == "NavigationCompleted")
    assert navigation["success"] is False
    assert navigation["web_error_status"] == "ConnectionAborted"
    core = next(e for e in entries if e["event"] == "core.after_navigation")
    assert core["browser_pid"] == 42 and core["runtime_version"] == "123.4"
    snapshot = next(e for e in entries if e["event"] == "before_destroy")
    assert snapshot["shown"] is True and snapshot["_pywebviewready"] is False
    assert all(e["utc"].endswith("+00:00") and e["monotonic"] > 0 for e in entries)
    # A late upstream error remains ordered after the saved pre-destroy snapshot.
    diagnostics.emit(
        logging.LogRecord(
            "pywebview",
            logging.ERROR,
            "",
            0,
            "controller 0x80004004 token=secret https://host/?token=secret",
            (),
            None,
        )
    )
    assert path.read_text().startswith(before)
    assert json.loads(path.read_text().splitlines()[-1])["hresults"] == ["0x80004004"]
    assert "secret" not in path.read_text()


def test_window_diagnostics_unavailable_does_not_replace_acceptance_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "diagnostics.jsonl"
    diagnostics = probe.WindowDiagnostics(path)

    def unavailable():
        raise RuntimeError("token=secret")

    monkeypatch.setattr(probe, "browser_processes", unavailable)
    with pytest.raises(ValueError, match="original acceptance failure"):
        try:
            raise ValueError("original acceptance failure")
        finally:
            diagnostics.snapshot(SimpleNamespace(), "before_destroy")
            diagnostics.record("before_destroy.result", status="failed", checks=[])
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert entries[0]["unavailable"] == "AttributeError"
    assert entries[1]["unavailable"] == "RuntimeError"
    assert entries[2]["status"] == "failed"
    assert "secret" not in path.read_text()
    # An unwritable diagnostic target must also leave the caller running.
    probe.WindowDiagnostics(tmp_path).record("cannot_write")


@pytest.mark.parametrize("fail", [False, True])
def test_native_author_probe_runs_ipython_cells_and_propagates_errors(tmp_path, fail):
    from scripts.verify_native_application import AUTHOR_NOTEBOOK_CHECK

    foreign = tmp_path / "foreign-build-venv"
    site = foreign / (
        "Lib/site-packages"
        if sys.platform == "win32"
        else (
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
    )
    site.mkdir(parents=True)
    (site / "foreign_virtualenv_marker.py").write_text("# must not be imported")
    notebooks = tmp_path / "notebooks"
    notebooks.mkdir()
    (notebooks / "02_edit_scan.py").write_text(
        "from IPython import get_ipython\n"
        "assert get_ipython() is not None\n"
        "from importlib.util import find_spec\n"
        "assert find_spec('foreign_virtualenv_marker') is None\n"
        "# %% Next cell sees the same notebook namespace.\n"
        "from types import SimpleNamespace\n"
        "run = SimpleNamespace(id='retained-run')\n"
        + ("raise ValueError('notebook execution failed')\n" if fail else "")
    )
    result = subprocess.run(  # noqa: S603 - owned acceptance probe and fixture
        [sys.executable, "-I", "-c", AUTHOR_NOTEBOOK_CHECK, str(tmp_path)],
        env=dict(os.environ, VIRTUAL_ENV=str(foreign)),
        capture_output=True,
        text=True,
        check=False,
    )
    receipt = tmp_path / "saved-run.json"
    if fail:
        assert result.returncode != 0
        assert "notebook execution failed" in result.stdout + result.stderr
        assert not receipt.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(receipt.read_text()) == {"run_id": "retained-run"}
