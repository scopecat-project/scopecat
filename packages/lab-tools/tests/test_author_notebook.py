"""Author Notebook entry follows the registered runtime without starting a daemon."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock
from typer.testing import CliRunner

from lab_tools import author_notebook
from lab_tools.lab_environment import environment_switch_path
from scopecat_server.cli import app


@pytest.fixture
def laboratory(tmp_path, monkeypatch):
    source = tmp_path / "author"
    source.mkdir()
    home = tmp_path / "application"
    home.mkdir()
    service = SimpleNamespace(
        id="a" * 32, name="Lab", python="first-environment/python"
    )
    store = SimpleNamespace(
        lock=FileLock(home / "services.lock"),
        for_workspace=lambda root: (service, "source-id") if root == source else None,
    )
    monkeypatch.setattr(author_notebook, "Services", lambda _: store)
    monkeypatch.setattr(
        author_notebook.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    return source, home, service


def test_launch_uses_latest_binding_and_private_kernel_directory(
    laboratory, monkeypatch
):
    source, home, service = laboratory
    monkeypatch.setenv("PYTHONPATH", "foreign-source")
    monkeypatch.setenv("PYTHONHOME", "foreign-python")
    monkeypatch.setenv("SCOPECAT_DAEMON_URL", "http://foreign-service")
    sessions = []

    def start(command, *, cwd, env):
        assert cwd == source
        assert command[:4] == [service.python, "-I", "-m", "jupyterlab"]
        assert "--no-browser" in command
        assert not {"PYTHONPATH", "PYTHONHOME", "SCOPECAT_DAEMON_URL"} & env.keys()
        path = Path(env["JUPYTER_PATH"].split(os.pathsep)[0])
        spec = json.loads((path / "kernels/scopecat-lab/kernel.json").read_text())
        assert spec["argv"][:3] == [service.python, "-I", "-m"]
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


def test_pending_environment_switch_prevents_notebook_launch(tmp_path, monkeypatch):
    import sys

    from lab_tools import services
    from scopecat.author_workspaces import LocalAuthorWorkspaces

    root = tmp_path / "laboratory"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    monkeypatch.setattr(
        services,
        "_run",
        lambda *_: {
            "root": str(root),
            "static_dir": str(tmp_path / "gui"),
            "environment": {},
            "adapter_identity": None,
            "settings_identity": None,
        },
    )
    home = tmp_path / "application"
    store = services.Services(home)
    service = store.register(root, Path(sys.executable), name="Lab")
    state = root / ".scopecat"
    state.mkdir()
    (state / "author-workspaces.json").write_text(
        LocalAuthorWorkspaces(service_root=root, items=()).model_dump_json()
    )
    marker = environment_switch_path(home, service.id)
    marker.parent.mkdir()
    marker.write_text("{}")
    monkeypatch.setattr(author_notebook.subprocess, "run", pytest.fail)
    with pytest.raises(ValueError, match="环境切换未完成"):
        author_notebook.launch_notebook(root, home)


def test_public_notebook_entry_forwards_workspace_options(monkeypatch):
    received = []
    monkeypatch.setattr(author_notebook, "main", received.extend)
    arguments = ["author folder", "--home", "application home", "--no-browser"]
    result = CliRunner().invoke(app, ["notebook", *arguments])
    assert result.exit_code == 0, result.output
    assert received == arguments
