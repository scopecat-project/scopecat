"""Acceptance owns disposable copies, not the candidate or retained reports."""

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


@pytest.mark.parametrize("keep_work", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_native_acceptance_preserves_candidate_and_reports(
    tmp_path, monkeypatch, keep_work, failure
):
    app = tmp_path / "Scopecat.app"
    app.mkdir()
    (app / "original").write_text("candidate")
    reports = tmp_path / "reports"

    def check(copied, home, installer):
        assert copied != app
        copied.rename(copied.with_name("Removed Scopecat.app"))
        home.mkdir()
        (home / "result.json").write_text('{"status": "stopped"}')
        (home / "data").mkdir()
        (home / "data/native-start.log").write_text("diagnostic")
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
