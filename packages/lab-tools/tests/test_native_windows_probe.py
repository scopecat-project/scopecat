"""Probe failure handling and real SDK gate contracts; not native UI evidence."""

import json
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import httpx2
import psutil
import pytest

from lab_tools.application_runtime import ApplicationRuntime
from scopecat.daemon.views import RunDetail, RunSummaryPage
from scopecat_server.scaffold import write_author_scaffold

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/verify_native_windows.py"
spec = spec_from_file_location("verify_native_windows", SCRIPT)
probe = module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_wait_has_a_deadline():
    with pytest.raises(TimeoutError, match="bridge unavailable"):
        probe.wait_for(lambda: False, "bridge unavailable", timeout=0)


def test_probe_nonzero_exit_preserves_diagnostic(tmp_path):
    log = tmp_path / "host.log"
    with pytest.raises(subprocess.CalledProcessError):
        probe.run_bounded(
            [sys.executable, "-c", "print('native failure', flush=True); exit(7)"],
            log,
            timeout=5,
        )
    assert "native failure" in log.read_text()


def test_probe_timeout_reaps_host_and_owned_child(tmp_path):
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


def test_cleanup_after_host_crash_uses_recorded_client_identity(tmp_path, monkeypatch):
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
    tmp_path, monkeypatch, setting, value
):
    hosted_runner(tmp_path, monkeypatch)
    monkeypatch.setenv(setting, value)
    with pytest.raises(RuntimeError, match="default website store"):
        probe.require_hosted_runner(tmp_path / "home")


def hosted_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("RUNNER_OS", "Windows")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))


def test_probe_requires_home_below_disposable_runner_temp(tmp_path, monkeypatch):
    hosted_runner(tmp_path, monkeypatch)
    probe.require_hosted_runner(tmp_path / "home")
    for home in (tmp_path, tmp_path.parent / "outside"):
        with pytest.raises(RuntimeError, match="RUNNER_TEMP"):
            probe.require_hosted_runner(home)


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_host_failure_overrides_partial_pass_and_retains_reports(
    tmp_path, monkeypatch, fail_cleanup
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
            raise subprocess.TimeoutExpired(command, timeout)
        if fail_cleanup:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(probe, "run_bounded", run)
    with pytest.raises((subprocess.TimeoutExpired, subprocess.CalledProcessError)):
        probe.verify(app, home)
    result = json.loads((home / "native-windows/result.json").read_text())
    assert result["status"] == "failed" and result["host_error"]
    assert len(calls) == 2 and "--cleanup" in calls[1]
    assert not list((home / "native-windows").glob("host-*"))
    assert (home / "native-windows/host.log").read_text() == "diagnostic"
    assert bootstrap.read_text() == "original candidate"


def test_probe_acquisition_gate_uses_real_service_and_completes(tmp_path):
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
        draft_key = probe.prepare_decision(record.base_url)
        assert draft_key.startswith("scopecat:decision:")
        probe.run_bounded(
            [sys.executable, "-I", str(authors / "notebooks/02_edit_scan.py")],
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
            assert after.snapshot.outcome.result == "succeeded"
            assert runtime.status().record == record
            assert (tmp_path / "completed").read_text() == run_id
    finally:
        if child is not None and child.poll() is None:
            probe.terminate_validation_process_tree(child, owner=owner)
        runtime.stop()
