"""Explicit deployment registration never imports into the management runtime."""

import runpy
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from lab_tools import application, services
from lab_tools.host_operations import Command, launch
from scopecat_server.cli import app


def test_registration_preserves_interpreter_and_rejects_live_rebinding(
    tmp_path, monkeypatch
):
    root = tmp_path / "实验代码"
    root.mkdir()
    python = tmp_path / "env/python"
    python.parent.mkdir()
    if sys.platform == "win32":
        python.touch()
    else:
        python.symlink_to(sys.executable)
    received = []

    def probe(executable, request):
        received.append((executable, request))
        return {
            "root": str(root),
            "static_dir": str(tmp_path / "gui"),
            "environment": {"prefix": str(python.parent)},
        }

    monkeypatch.setattr(services, "_run", probe)
    store = services.Services(tmp_path / "home")
    first = store.register(root, python, name="实验台")
    assert first.python == str(python)
    if sys.platform != "win32":
        assert first.python != str(python.resolve())
    assert store.register(root, python, name="实验台") == first
    assert received[0][1]["action"] == "probe"
    assert len(store.list()) == 1
    monkeypatch.setattr(services, "open_project", lambda _: None)
    monkeypatch.setattr(
        services, "inspect_daemon", lambda _: SimpleNamespace(state="running")
    )
    with pytest.raises(ValueError, match="显式停止"):
        store.register(root, python, name="换环境")
    assert store.get(first.id) == first


def test_http_command_only_accepts_registered_identity(tmp_path):
    with pytest.raises(ValidationError):
        Command(action="service_start", service="a" * 32, path="/arbitrary")
    with pytest.raises(ValueError, match="未找到"):
        launch(tmp_path, None, Command(action="service_start", service="a" * 32))
    with pytest.raises(ValueError, match="编号"):
        launch(
            tmp_path,
            None,
            Command(action="service_start", service="a" * 32, reset=True),
        )


def test_public_app_forwards_local_registration_options(monkeypatch):
    received = []
    monkeypatch.setattr(application, "main", received.extend)
    args = ["a folder", "--python", "env/python", "--static-dir", "gui"]
    result = CliRunner().invoke(app, ["app", *args])
    assert result.exit_code == 0, result.output
    assert received == args


def test_concurrent_registration_and_pending_start_keep_one_binding(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    from lab_tools.host_operations import Operations

    python = tmp_path / "python"
    python.touch()
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(
        services,
        "_run",
        lambda *_: {
            "root": str(root),
            "static_dir": str(tmp_path / "gui"),
            "environment": {"prefix": "env"},
        },
    )
    home = tmp_path / "home"
    store = services.Services(home)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(
            pool.map(lambda _: store.register(root, python, name="lab"), range(2))
        )
    assert records[0] == records[1]
    assert store.get(records[0].id) == records[0]
    Operations(home).begin(Command(action="service_start", service=records[0].id))
    with pytest.raises(ValueError, match="管理操作"):
        store.register(root, python, name="changed")
    assert store.get(records[0].id).name == "lab"


def test_installed_launcher_routes_workbench_and_explicit_teaching(
    tmp_path, monkeypatch
):
    from lab_tools import bundle

    source = tmp_path / "delivery"
    source.mkdir()
    monkeypatch.setattr(bundle, "verify_bundle", lambda _: None)
    monkeypatch.setattr(bundle, "file_hash", lambda _: "a" * 64)

    def install(_bundle, environment):
        environment.mkdir()
        return environment

    monkeypatch.setattr(bundle, "install_bundle", install)
    checks = []
    monkeypatch.setattr(bundle.subprocess, "run", lambda args, **_: checks.append(args))
    launcher = bundle.install_home(source, tmp_path / "home")
    assert checks[0][-3:] == ["-m", "lab_tools.application", "--help"]
    calls = []
    monkeypatch.setattr(bundle.subprocess, "call", lambda args: calls.append(args) or 0)
    for arguments in ([], ["teach", "compute", "--verify"]):
        monkeypatch.setattr(sys, "argv", [str(launcher), *arguments])
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(launcher), run_name="__main__")
        assert error.value.code == 0
    assert calls[0][1:3] == ["-m", "lab_tools.application"]
    assert calls[1][1:3] == ["-m", "lab_tools.sandbox"]
    assert calls[1][-2:] == ["compute", "--verify"]
