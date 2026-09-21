"""Owned environment retries retain evidence and never take over a foreign venv."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock

from lab_tools import bundle, lab_environment
from lab_tools.bundle import install_bundle as offline_install


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


def test_failed_spawn_clears_launch_intent_and_allows_owned_retry(
    project, delivery, tmp_path, monkeypatch, installer
):
    home = tmp_path / "home"

    def spawn_failed(*_args, **_kwargs):
        raise OSError("installer could not start")

    with monkeypatch.context() as failed:
        failed.setattr(bundle, "install_bundle", offline_install)
        failed.setattr(bundle.shutil, "which", lambda _name: "uv")
        failed.setattr(bundle.subprocess, "Popen", spawn_failed)
        with pytest.raises(OSError, match="installer could not start"):
            lab_environment.prepare_environment(project, delivery, home)
    attempt_file = next((home / "environment-attempts").glob("*.json"))
    attempt = json.loads(attempt_file.read_text())
    assert attempt["launching"] is False
    assert attempt["pid"] is None
    prepared = lab_environment.prepare_environment(project, delivery, home)
    assert prepared.python.is_file()
    assert len(list(project.glob(".venv-failed-*"))) == 1
    assert installer == [project / ".venv"]


@pytest.fixture
def registered_lab(project, tmp_path, monkeypatch):
    from lab_tools import services
    from scopecat.author_workspaces import LocalAuthorWorkspace, LocalAuthorWorkspaces

    old = tmp_path / "old-environment"
    old.mkdir()
    python = old / "python"
    python.touch()
    (old / "keep").write_text("original environment")

    def probe(executable, request):
        return {
            "root": str(project),
            "static_dir": request["static_dir"],
            "environment": {"prefix": str(Path(executable).parent)},
            "adapter_identity": None,
            "settings_identity": None,
        }

    monkeypatch.setattr(services, "_run", probe)
    store = services.Services(tmp_path / "home")
    service = store.register(project, python, name="Lab", static_dir=tmp_path / "gui")
    store.remember(service.id)
    data = project / ".scopecat"
    data.mkdir()
    (data / "evidence").write_text("retained scientific data")
    registry = data / "author-workspaces.json"
    registry.write_text(
        LocalAuthorWorkspaces(
            service_root=project,
            items=(
                LocalAuthorWorkspace(
                    id="author", name="Author", root=tmp_path / "author", python=python
                ),
            ),
        ).model_dump_json()
    )
    return store, service, registry


def test_delivery_update_preserves_scientific_and_source_identity(
    registered_lab,
    delivery,
    installer,
):
    from scopecat.author_workspaces import LocalAuthorWorkspaces

    store, original, registry = registered_lab
    before = LocalAuthorWorkspaces.model_validate_json(registry.read_bytes())
    updated = store.update_environment(original.id, delivery, operation_id="test")
    assert updated.id == original.id and updated.root == original.root
    assert store.preferred() == updated
    assert Path(updated.python).is_relative_to(
        store.database.parent.parent / "laboratory-environments"
    )
    after = LocalAuthorWorkspaces.model_validate_json(registry.read_bytes())
    assert after.items[0].id == before.items[0].id
    assert after.items[0].root == before.items[0].root
    assert after.items[0].python == Path(updated.python)
    assert (registry.parent / "evidence").read_text() == "retained scientific data"
    assert (Path(original.python).parent / "keep").read_text() == "original environment"
    assert (
        store.update_environment(original.id, delivery, operation_id="retry") == updated
    )
    assert len(installer) == 1


def test_update_qualification_failure_preserves_registration_and_can_retry(
    registered_lab,
    delivery,
    installer,
    monkeypatch,
):
    from lab_tools import services

    store, original, registry = registered_lab
    before = registry.read_bytes()
    probe = services._run

    def failed_probe(*_):
        raise ValueError("missing adapter")

    monkeypatch.setattr(services, "_run", failed_probe)
    with pytest.raises(ValueError, match="missing adapter"):
        store.update_environment(original.id, delivery, operation_id="test")
    assert store.get(original.id) == original and registry.read_bytes() == before
    monkeypatch.setattr(services, "_run", probe)
    store.update_environment(original.id, delivery, operation_id="retry")
    assert len(installer) == 1


def test_interrupted_switch_blocks_start_and_same_delivery_completes_it(
    registered_lab,
    delivery,
    installer,
    monkeypatch,
):
    store, original, registry = registered_lab
    save = store._save

    def interrupted_save(*_):
        raise OSError("interrupted switch")

    monkeypatch.setattr(store, "_save", interrupted_save)
    with pytest.raises(OSError, match="interrupted switch"):
        store.update_environment(original.id, delivery, operation_id="test")
    assert store.get(original.id) == original
    assert lab_environment.environment_switch_path(
        store.database.parent.parent, original.id
    ).is_file()
    with pytest.raises(ValueError, match="环境切换未完成"):
        store.start(original.id)
    monkeypatch.setattr(store, "_save", save)
    updated = store.update_environment(original.id, delivery, operation_id="retry")
    assert updated.python != original.python
    assert json.loads(registry.read_text())["items"][0]["python"] == updated.python
    assert not lab_environment.environment_switch_path(
        store.database.parent.parent, original.id
    ).exists()
    assert len(installer) == 1


def test_update_activation_keeps_both_runtime_locks(
    project, delivery, tmp_path, installer
):
    from filelock import Timeout

    def activate(_prepared):
        for name in ("deployment.lock", "daemon.lock"):
            with (
                pytest.raises(Timeout),
                FileLock(project / ".scopecat" / name, timeout=0),
            ):
                pass

    lab_environment.prepare_environment(
        project, delivery, tmp_path / "home", activate=activate
    )


def test_pending_switch_rejects_a_different_delivery(
    registered_lab, delivery, installer
):
    store, original, registry = registered_lab
    before = registry.read_bytes()
    marker = lab_environment.environment_switch_path(
        store.database.parent.parent, original.id
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps({"root": original.root, "delivery": "previous artifact"})
    )
    with pytest.raises(ValueError, match="上次的同一交付目录"):
        store.update_environment(original.id, delivery, operation_id="test")
    assert store.get(original.id) == original and registry.read_bytes() == before
    assert installer == []
