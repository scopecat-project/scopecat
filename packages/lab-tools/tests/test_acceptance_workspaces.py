"""Acceptance owns disposable copies, not the candidate or retained reports."""

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
