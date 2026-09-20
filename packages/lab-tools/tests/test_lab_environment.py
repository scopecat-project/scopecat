"""Owned environment retries retain evidence and never take over a foreign venv."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock

from lab_tools import bundle, lab_environment


@pytest.fixture
def delivery(tmp_path: Path) -> Path:
    root = tmp_path / "delivery"
    (root / "gui").mkdir(parents=True)
    (root / "wheels").mkdir()
    (root / "gui/index.html").write_text("<html>current GUI</html>")
    (root / "wheels/example.whl").write_bytes(b"wheel fixture")
    (root / "requirements.lock").write_text("example==1\n")
    (root / "dependencies.lock").write_text("example==1\n")
    (root / "build.lock").write_text("version = 1\n")
    (root / "install.py").write_text("# installer fixture\n")
    files = bundle.inventory(root, ("gui", "wheels"))
    files.update(
        {
            name: bundle.file_hash(root / name)
            for name in (
                "requirements.lock",
                "dependencies.lock",
                "build.lock",
                "install.py",
            )
        }
    )
    (root / bundle.MANIFEST).write_text(
        json.dumps(
            {
                "format": 1,
                "target": bundle.target_identity(),
                "sources": {},
                "runtime": {"scopecat": "current"},
                "files": files,
            }
        )
    )
    return root


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "experiment"
    root.mkdir()
    (root / "scopecat.toml").write_text("[lab]\n")
    return root


@pytest.fixture
def installer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        lab_environment.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )

    def install(source, destination, *, ownership_token, on_process):
        calls.append(destination)
        destination.mkdir()
        (destination / bundle.OWNERSHIP).write_text(ownership_token)
        python = destination / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        python.parent.mkdir()
        python.touch()
        (destination / bundle.RECEIPT).write_text(
            json.dumps(
                {
                    "bundle": str(source),
                    "manifest_sha256": bundle.file_hash(source / bundle.MANIFEST),
                }
            )
        )
        return destination

    monkeypatch.setattr(bundle, "install_bundle", install)
    return calls


def test_completed_environment_reuses_exact_receipt_without_selecting_launcher(
    project, delivery, tmp_path, installer
):
    home = tmp_path / "home"
    first = lab_environment.prepare_environment(project, delivery, home)
    second = lab_environment.prepare_environment(project, delivery, home)
    assert first == second
    assert first.python.parent.parent == project / ".venv"
    assert first.gui.is_relative_to(home / "releases")
    assert installer == [project / ".venv"]
    assert not (home / "lab.py").exists()
    assert not (home / "lab.cmd").exists()


def test_foreign_environment_is_never_moved(project, delivery, tmp_path, installer):
    environment = project / ".venv"
    environment.mkdir()
    evidence = environment / "keep"
    evidence.write_text("external environment")
    with pytest.raises(ValueError, match="不属于本应用"):
        lab_environment.prepare_environment(project, delivery, tmp_path / "home")
    assert evidence.read_text() == "external environment"
    assert installer == []
    assert not list(project.glob(".venv-failed-*"))


def test_interrupted_owned_environment_is_retained_and_recreated_at_final_path(
    project, delivery, tmp_path, monkeypatch, installer
):
    install = bundle.install_bundle

    def interrupted(source, destination, *, ownership_token, on_process):
        destination.mkdir()
        (destination / bundle.OWNERSHIP).write_text(ownership_token)
        (destination / "partial").write_text("retained unfinished install")
        raise OSError("interrupted")

    monkeypatch.setattr(bundle, "install_bundle", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        lab_environment.prepare_environment(project, delivery, tmp_path / "home")
    monkeypatch.setattr(bundle, "install_bundle", install)
    prepared = lab_environment.prepare_environment(project, delivery, tmp_path / "home")
    assert prepared.python.parent.parent == project / ".venv"
    failed = list(project.glob(".venv-failed-*/partial"))
    assert len(failed) == 1
    assert failed[0].read_text() == "retained unfinished install"
    assert installer == [project / ".venv"]


def test_different_bundle_cannot_upgrade_completed_environment(
    project, delivery, tmp_path, installer
):
    home = tmp_path / "home"
    lab_environment.prepare_environment(project, delivery, home)
    manifest = delivery / bundle.MANIFEST
    content = json.loads(manifest.read_text())
    content["sources"]["revision"] = "next"
    manifest.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="交付记录与当前产物不同"):
        lab_environment.prepare_environment(project, delivery, home)
    assert len(installer) == 1
    assert not list(project.glob(".venv-failed-*"))


@pytest.mark.parametrize("name", ["deployment.lock", "daemon.lock"])
def test_active_runtime_lock_blocks_preparation(
    project, delivery, tmp_path, installer, name
):
    state = project / ".scopecat"
    state.mkdir()
    with FileLock(state / name), pytest.raises(ValueError, match="停止服务"):
        lab_environment.prepare_environment(project, delivery, tmp_path / "home")
    assert not (project / ".venv").exists()
    assert installer == []


def test_live_recorded_install_child_blocks_retry(
    project, delivery, tmp_path, monkeypatch, installer
):
    def unfinished(source, destination, *, ownership_token, on_process):
        destination.mkdir()
        (destination / bundle.OWNERSHIP).write_text(ownership_token)
        on_process(os.getpid())
        raise OSError("parent interrupted")

    monkeypatch.setattr(bundle, "install_bundle", unfinished)
    home = tmp_path / "home"
    with pytest.raises(OSError, match="parent interrupted"):
        lab_environment.prepare_environment(project, delivery, home)
    with pytest.raises(ValueError, match="子进程仍在运行"):
        lab_environment.prepare_environment(project, delivery, home)
    assert not list(project.glob(".venv-failed-*"))


def test_completed_environment_mismatch_is_retained(
    project, delivery, tmp_path, installer, monkeypatch
):
    home = tmp_path / "home"
    lab_environment.prepare_environment(project, delivery, home)
    monkeypatch.setattr(
        lab_environment.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stderr="framework changed"
        ),
    )
    with pytest.raises(ValueError, match="原环境保留"):
        lab_environment.prepare_environment(project, delivery, home)
    assert len(installer) == 1
    assert (project / ".venv" / bundle.RECEIPT).is_file()
    assert not list(project.glob(".venv-failed-*"))
