"""Acceptance owns disposable copies, not the candidate or retained reports."""

import re
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[3] / "scripts" / f"{name}.py"
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_macos_download = load_script("verify_macos_download")
verify_native_application = load_script("verify_native_application")
verify_native_replacement = load_script("verify_native_replacement")


def assert_reports_uploaded(workflow, relatives):
    """Check actual retained files against this workflow's always-upload step."""
    step = workflow.split("      - name: Retain native acceptance logs\n", 1)[1]
    step = re.split(r"(?m)^ {2}\S|^ {6}- ", step, maxsplit=1)[0]
    assert "        if: always()\n" in step
    paths = {
        Path(line.strip().removeprefix("${{ runner.temp }}/"))
        for line in step.splitlines()
        if line.strip().startswith("${{ runner.temp }}/")
    }
    for relative in relatives:
        assert any(relative.is_relative_to(path) for path in paths), relative


@pytest.mark.parametrize(
    "relative",
    ["windows-storage/", "reset-recovery/", "data/desktop/desktop.log"],
)
def test_native_upload_contract_rejects_missing_reports(relative):
    workflow = (
        Path(__file__).resolve().parents[3] / ".github/workflows/acceptance.yml"
    ).read_text()
    omitted = workflow.replace(
        f"            ${{{{ runner.temp }}}}/native-acceptance/{relative}\n", ""
    )
    assert omitted != workflow
    with pytest.raises(AssertionError):
        assert_reports_uploaded(
            omitted, [Path("native-acceptance") / relative / "C.json"]
        )


@pytest.mark.parametrize("failure", [False, True])
def test_every_native_report_survives_cleanup_and_is_uploaded(
    tmp_path, monkeypatch, failure
):
    # Independent inventory of each probe's diagnostics, including early failures.
    relatives = [
        "data/desktop/desktop.log",
        "native-windows/python.log",
        "native-windows/sequence.json",
        *[
            f"reset-recovery/{name}"
            for name in (
                "result.json",
                "reset.json",
                "reopen.json",
                "reset.log",
                "reopen.log",
                "reset-python.log",
                "reopen-python.log",
                "cleanup.log",
                "desktop.log",
                "daemon.log",
            )
        ],
    ]
    app, reports = tmp_path / "app", tmp_path / "reports"
    app.mkdir()

    def check(copied, home, installer, *, native_windows):
        for relative in relatives:
            path = home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        if failure:
            raise RuntimeError("probe failed")

    monkeypatch.setattr(verify_native_application, "_verify", check)
    if failure:
        with pytest.raises(RuntimeError, match="probe failed"):
            verify_native_application.verify(app, reports)
    else:
        verify_native_application.verify(app, reports)
    assert not list(reports.glob("work-*"))
    for relative in relatives:
        assert (reports / relative).read_text() == relative
    workflow = (
        Path(__file__).resolve().parents[3] / ".github/workflows/acceptance.yml"
    ).read_text()
    assert_reports_uploaded(
        workflow, [Path("native-acceptance") / p for p in relatives]
    )


@pytest.mark.parametrize("keep_work", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_replacement_reclaims_packages_and_environment(
    tmp_path, monkeypatch, keep_work, failure
):
    previous, current = tmp_path / "previous", tmp_path / "current"
    previous.mkdir()
    current.mkdir()
    reports = tmp_path / "reports"

    def check(old, new, home):
        assert (old, new) == (previous, current)
        home.mkdir()
        (home / "retired-package").mkdir()
        (home / "authors").mkdir()
        (home / "before.json").write_text("{}")
        if failure:
            raise RuntimeError("replacement failed")
        (home / "replacement.json").write_text("{}")

    monkeypatch.setattr(verify_native_replacement, "_verify", check)
    if failure:
        with pytest.raises(RuntimeError, match="replacement failed"):
            verify_native_replacement.verify(
                previous, current, reports, keep_work=keep_work
            )
    else:
        verify_native_replacement.verify(
            previous, current, reports, keep_work=keep_work
        )
    assert previous.is_dir() and current.is_dir()
    assert (reports / "before.json").is_file()
    assert (reports / "replacement.json").exists() is not failure
    assert bool(list(reports.glob("work-*"))) is keep_work


@pytest.mark.parametrize("keep_work", [False, True])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("storage_reports", ["cocoa-storage", "windows-storage"])
def test_native_acceptance_preserves_candidate_and_reports(
    tmp_path, monkeypatch, keep_work, failure, storage_reports
):
    app = tmp_path / "Scopecat.app"
    app.mkdir()
    (app / "original").write_text("candidate")
    reports = tmp_path / "reports"

    def check(copied, home, installer, *, native_windows):
        assert not native_windows
        assert copied != app
        copied.rename(copied.with_name("Removed Scopecat.app"))
        home.mkdir()
        (home / "result.json").write_text('{"status": "stopped"}')
        (home / "data").mkdir()
        (home / "data/native-start.log").write_text("diagnostic")
        (home / "native-windows").mkdir()
        (home / "native-windows/result.json").write_text('{"status": "failed"}')
        (home / storage_reports).mkdir()
        for name in (
            "result.json",
            "draft-fixture.json",
            "A.json",
            "B.json",
            "C.json",
            "A.log",
            "B.log",
            "C.log",
        ):
            (home / storage_reports / name).write_text("storage diagnostic")
        (home / "environment").mkdir()
        if failure:
            raise RuntimeError("acceptance failed")

    monkeypatch.setattr(verify_native_application, "_verify", check)
    if failure:
        with pytest.raises(RuntimeError, match="acceptance failed"):
            verify_native_application.verify(app, reports, keep_work=keep_work)
    else:
        verify_native_application.verify(app, reports, keep_work=keep_work)
    assert (app / "original").read_text() == "candidate"
    assert (reports / "result.json").is_file()
    assert (reports / "data/native-start.log").read_text() == "diagnostic"
    assert (
        reports / "native-windows/result.json"
    ).read_text() == '{"status": "failed"}'
    assert {path.name for path in (reports / storage_reports).iterdir()} == {
        "result.json",
        "draft-fixture.json",
        "A.json",
        "B.json",
        "C.json",
        "A.log",
        "B.log",
        "C.log",
    }
    assert bool(list(reports.glob("work-*"))) is keep_work
    retained = list((reports / storage_reports).iterdir())
    assert all(path.read_text() == "storage diagnostic" for path in retained)
    workflow = (
        Path(__file__).resolve().parents[3] / ".github/workflows/acceptance.yml"
    ).read_text()
    assert_reports_uploaded(
        workflow,
        [Path("native-acceptance") / path.relative_to(reports) for path in retained],
    )


def test_acceptance_rejects_reports_inside_candidate(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    with pytest.raises(ValueError, match="outside"):
        verify_native_application.verify(app, app / "reports")
    assert not list(app.iterdir())


def test_download_failure_cleans_disposable_app_but_keeps_report(tmp_path, monkeypatch):
    def check(installer: Path, home: Path):
        home.mkdir()
        (home / "Scopecat.app").mkdir()
        (home / "report.json").write_text("{}")
        raise RuntimeError("signature check failed")

    monkeypatch.setattr(verify_macos_download, "_verify", check)
    reports = tmp_path / "reports"
    with pytest.raises(RuntimeError, match="signature"):
        verify_macos_download.verify(tmp_path / "installer.dmg", reports)
    assert list(reports.iterdir()) == [reports / "report.json"]


def test_native_window_opt_in_refuses_local_before_copying(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    app, reports = tmp_path / "app", tmp_path / "reports"
    with pytest.raises(subprocess.CalledProcessError):
        verify_native_application.verify(app, reports, native_windows=True)
    assert not reports.exists()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_reset_failure_keeps_report_and_owned_logs(
    tmp_path, monkeypatch, cleanup_fails
):
    import json
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    probe = load_script("verify_native_reset")
    monkeypatch.setattr(probe, "require_hosted_runner", lambda _: None)
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(
        probe,
        "ApplicationRuntime",
        lambda _: SimpleNamespace(
            installation=lambda: SimpleNamespace(python=Path("unused-python"))
        ),
    )
    app, home = tmp_path / "app", tmp_path / "home"
    (app / "resources").mkdir(parents=True)
    home.mkdir()
    reports = home / "reset-recovery"
    calls = []

    def run(command, log, *, timeout):
        calls.append(command)
        log.write_text("host diagnostic")
        if len(calls) == 1:
            anchor = reports / "fixture/data"
            (anchor / "desktop").mkdir(parents=True)
            (anchor / "desktop/desktop.log").write_text("desktop failure")
            candidate = anchor / "runtime/.scopecat"
            candidate.mkdir(parents=True)
            (candidate / "daemon.log").write_text("candidate failure")
            raise RuntimeError("original probe failure")
        if cleanup_fails:
            raise RuntimeError("cleanup failure")

    monkeypatch.setattr(probe, "run_bounded", run)
    with pytest.raises(RuntimeError, match="original probe failure"):
        probe.verify(app, home)
    report = json.loads((reports / "result.json").read_text())
    assert report["status"] == "failed" and report["stages"] == {}
    assert "original probe failure" in report["error"]
    assert bool(report["cleanup_error"]) is cleanup_fails
    assert (reports / "desktop.log").read_text() == "desktop failure"
    assert (reports / "daemon.log").read_text() == "candidate failure"


@pytest.mark.parametrize(
    ("window_code", "reset_code"), [(0, 0), (7, 0), (0, 8), (7, 8)]
)
def test_native_sequence_collects_independent_results(
    tmp_path, monkeypatch, window_code, reset_code
):
    import json

    reports = tmp_path / "native-windows"
    reports.mkdir()
    calls = []

    def run(command, *, check):
        calls.append(Path(command[1]).name)
        if len(calls) == 1:
            (reports / "result.json").write_text(
                json.dumps(
                    {
                        "host_stopped": True,
                        "cleanup_completed": True,
                        "status": "failed" if window_code else "passed",
                    }
                )
            )
            code = window_code
        else:
            assert json.loads((reports / "result.json").read_text())[
                "cleanup_completed"
            ]
            code = reset_code
        if code:
            raise subprocess.CalledProcessError(code, command)

    monkeypatch.setattr(verify_native_application.subprocess, "run", run)
    if window_code or reset_code:
        with pytest.raises(ExceptionGroup) as error:
            verify_native_application.verify_recovery_probes(tmp_path / "app", tmp_path)
        assert [e.returncode for e in error.value.exceptions] == [
            c for c in (window_code, reset_code) if c
        ]
    else:
        verify_native_application.verify_recovery_probes(tmp_path / "app", tmp_path)
    assert calls == ["verify_native_windows.py", "verify_native_reset.py"]
    sequence = json.loads((reports / "sequence.json").read_text())
    assert sequence["windows"]["exit_code"] == window_code
    assert sequence["reset"]["exit_code"] == reset_code
    assert bool(sequence["windows"].get("error")) is bool(window_code)
    assert bool(sequence["reset"].get("error")) is bool(reset_code)


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {},
        {"host_stopped": False, "cleanup_completed": True},
        {"host_stopped": True, "cleanup_completed": False},
    ],
)
def test_native_sequence_refuses_unverified_cleanup(tmp_path, monkeypatch, receipt):
    import json

    reports = tmp_path / "native-windows"
    reports.mkdir()
    calls = []

    def run(command, *, check):
        calls.append(command)
        if receipt is not None:
            (reports / "result.json").write_text(json.dumps(receipt))
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(verify_native_application.subprocess, "run", run)
    with pytest.raises(ExceptionGroup):
        verify_native_application.verify_recovery_probes(tmp_path / "app", tmp_path)
    assert len(calls) == 1
    sequence = json.loads((reports / "sequence.json").read_text())
    assert sequence["windows"]["exit_code"] == 7
    assert sequence["reset"]["status"] == "not-run"
    assert "cleanup" in sequence["reset"]["reason"]
