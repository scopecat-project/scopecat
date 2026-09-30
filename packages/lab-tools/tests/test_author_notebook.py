"""Author Notebook uses user Python without starting or modifying the application."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock
from typer.testing import CliRunner

from lab_tools import author_notebook
from scopecat_server.cli import app


@pytest.fixture
def laboratory(tmp_path, monkeypatch):
    source = tmp_path / "author"
    source.mkdir()
    python = (
        source / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    python.parent.mkdir(parents=True)
    python.touch()
    home = tmp_path / "application"
    home.mkdir()
    service = SimpleNamespace(
        id="a" * 32, name="Lab", python="first-environment/python"
    )
    store = SimpleNamespace(
        home=home,
        lock=FileLock(home / "services.lock"),
        installation=lambda: service,
        source=lambda root: "source-id" if root == source else None,
    )
    monkeypatch.setattr(author_notebook, "ApplicationRuntime", lambda _: store)
    monkeypatch.setattr(
        author_notebook.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    return source, home, service


def test_launch_keeps_user_python_when_application_changes(laboratory, monkeypatch):
    source, home, service = laboratory
    monkeypatch.setenv("PYTHONPATH", "foreign-source")
    monkeypatch.setenv("PYTHONHOME", "foreign-python")
    monkeypatch.setenv("SCOPECAT_DAEMON_URL", "http://foreign-service")
    sessions = []
    python = str(author_notebook.project_python(source))

    def start(command, *, cwd, env):
        assert cwd == source
        assert command[:4] == [python, "-I", "-m", "jupyterlab"]
        assert "--no-browser" in command
        assert not {"PYTHONPATH", "PYTHONHOME", "SCOPECAT_DAEMON_URL"} & env.keys()
        path = Path(env["JUPYTER_PATH"].split(os.pathsep)[0])
        spec = json.loads((path / "kernels/scopecat-lab/kernel.json").read_text())
        assert spec["argv"][:3] == [python, "-I", "-m"]
        assert spec["env"] == {}
        sessions.append(path)

        def wait():
            with FileLock(home / "services.lock", timeout=0):
                return 0

        return SimpleNamespace(wait=wait)

    monkeypatch.setattr(author_notebook.subprocess, "Popen", start)
    assert author_notebook.launch_notebook(source, home, no_browser=True) == 0
    service.python = "replacement-environment/python"
    assert author_notebook.launch_notebook(source, home, no_browser=True) == 0
    assert sessions[0] != sessions[1]
    assert all(not path.exists() for path in sessions)
    assert not (source / ".scopecat-notebook").exists()


def test_missing_notebook_extra_does_not_launch_or_install(laboratory, monkeypatch):
    source, home, _ = laboratory
    monkeypatch.setattr(
        author_notebook.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr=""),
    )
    monkeypatch.setattr(author_notebook.subprocess, "Popen", pytest.fail)
    with pytest.raises(ValueError, match="JupyterLab/ipykernel"):
        author_notebook.launch_notebook(source, home)


def test_missing_user_python_never_falls_back_to_application(laboratory, monkeypatch):
    source, home, _ = laboratory
    author_notebook.project_python(source).unlink()
    monkeypatch.setattr(author_notebook.subprocess, "run", pytest.fail)
    monkeypatch.setattr(author_notebook.subprocess, "Popen", pytest.fail)
    with pytest.raises(ValueError, match=r"\.venv 尚未准备"):
        author_notebook.launch_notebook(source, home)


def test_pending_environment_switch_prevents_notebook_launch(tmp_path, monkeypatch):
    from lab_tools.application_runtime import ApplicationRuntime

    home = tmp_path / "application"
    home.mkdir()
    store = ApplicationRuntime(home)
    store.pending.write_text("{}")
    monkeypatch.setattr(author_notebook.subprocess, "run", pytest.fail)
    with pytest.raises(ValueError, match="环境切换尚未完成"):
        author_notebook.launch_notebook(tmp_path / "author", home)


def test_public_notebook_entry_forwards_workspace_options(monkeypatch):
    received = []
    monkeypatch.setattr(author_notebook, "main", received.extend)
    arguments = ["author folder", "--home", "application home", "--no-browser"]
    result = CliRunner().invoke(app, ["notebook", *arguments])
    assert result.exit_code == 0, result.output
    assert received == arguments


def test_default_notebook_opens_sole_registered_source(tmp_path, monkeypatch):
    from scopecat import author_workspaces

    root = tmp_path / "software laboratory"
    root.mkdir()
    python = (
        root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    python.parent.mkdir(parents=True)
    python.touch()
    (root / "scopecat.toml").write_text("[lab]\n")
    home = tmp_path / "home"
    home.mkdir()
    service = SimpleNamespace(
        id="a" * 32, root=str(root), python="installed/python", name="Software"
    )
    store = SimpleNamespace(
        home=home,
        lock=FileLock(home / "services.lock"),
        root=root,
        installation=lambda: service,
        source=lambda _path: "registered-source",
    )
    monkeypatch.setattr(
        author_workspaces,
        "local_author_workspaces",
        lambda _: [SimpleNamespace(root=root)],
    )
    monkeypatch.setattr(author_notebook, "ApplicationRuntime", lambda _: store)
    monkeypatch.setattr(
        author_notebook.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0),
    )
    calls = []

    def start(command, *, cwd, env):
        calls.append((command, cwd))
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(author_notebook.subprocess, "Popen", start)
    assert author_notebook.launch_notebook(None, home) == 0
    assert calls[0][0][:4] == [str(python), "-I", "-m", "jupyterlab"]
    assert calls[0][1] == root
