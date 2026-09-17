"""Portable launcher admission and kernel checks without starting services."""

import json
import sys

import pytest

from lab_tools import project as cli


def test_tools_check_preserves_store_and_detects_same_version_change(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "environment_identity", lambda: {"runtime": "original"})
    root = tmp_path / "course"
    cli.create_project(root)
    store = root / ".scopecat"
    store.mkdir()
    data = store / "evidence"
    data.write_bytes(b"retained")
    assert cli.check_project(root) == root
    monkeypatch.setattr(cli, "environment_identity", lambda: {"runtime": "modified"})
    with pytest.raises(ValueError, match="不迁移旧库"):
        cli.notebook_command(root)
    assert data.read_bytes() == b"retained"
    assert not (root / ".scopecat-notebook").exists()


def test_installed_notebook_uses_project_python_without_source_injection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "environment_identity", dict)
    root = tmp_path / "course"
    cli.create_project(root)
    python = (
        root
        / ".venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    python.parent.mkdir(parents=True)
    python.touch()
    command, env = cli.notebook_command(root)
    spec = json.loads(
        (root / ".scopecat-notebook/kernels/scopecat-lab/kernel.json").read_text(
            encoding="utf-8"
        )
    )
    assert command[:3] == [str(python), "-m", "jupyterlab"]
    assert spec["argv"][0] == str(python)
    assert "PYTHONPATH" not in spec["env"]
    assert str(root / ".scopecat-notebook") in env["JUPYTER_PATH"]
    with pytest.raises(FileExistsError):
        cli.create_project(root)


def test_portable_entry_refuses_legacy_metadata(tmp_path):
    (tmp_path / cli.METADATA).write_text(
        json.dumps({"format": 2, "kind": "teaching"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="原入口和环境"):
        cli.check_project(tmp_path)


def test_prepare_does_not_overwrite_existing_environment(tmp_path, monkeypatch):
    from lab_tools import environment

    monkeypatch.setattr(environment, "check_project", lambda path: path)
    monkeypatch.setattr(environment, "environment_identity", dict)
    monkeypatch.setattr(environment, "gui_directory", lambda *_args: tmp_path / "gui")
    monkeypatch.setattr(environment.shutil, "which", lambda _name: "uv")
    existing = tmp_path / ".venv"
    existing.mkdir()
    marker = existing / "keep"
    marker.write_bytes(b"user environment")
    with pytest.raises(FileExistsError):
        environment.prepare_project(tmp_path)
    assert marker.read_bytes() == b"user environment"


def test_course_rejects_wrong_kernel_before_author_import(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "environment_identity", dict)
    root = tmp_path / "course"
    cli.create_project(root)
    monkeypatch.chdir(root / "notebooks")
    for name in ("start", "reopen"):
        notebook = json.loads((root / f"notebooks/{name}.ipynb").read_text())
        first_code = next(
            cell for cell in notebook["cells"] if cell["cell_type"] == "code"
        )
        with pytest.raises(RuntimeError, match="Select Kernel"):
            exec("".join(first_code["source"]), {})  # noqa: S102 - execute the shipped notebook guard
    assert not (root / ".scopecat").exists()
