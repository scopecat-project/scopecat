"""Reject stale GUI and invalid deliveries before touching a user environment."""

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from lab_tools import bundle


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


def test_gui_assets_must_match_delivery(delivery: Path):
    bundle.verify_bundle(delivery)
    (delivery / "gui/index.html").write_text("<html>stale GUI</html>")
    with pytest.raises(ValueError, match="被修改"):
        bundle.verify_bundle(delivery)


def test_corrupt_wheel_blocks_install_before_environment_creation(delivery, tmp_path):
    (delivery / "wheels/example.whl").write_bytes(b"changed")
    destination = tmp_path / "environment"
    with pytest.raises(ValueError, match="被修改"):
        bundle.install_bundle(delivery, destination)
    assert not destination.exists()


def add_toolchain(delivery, *, unsafe=False):
    directory = delivery / "toolchain"
    directory.mkdir()
    with tarfile.open(directory / "python.tar", "w") as archive:
        name = (
            "../escaped"
            if unsafe
            else ("python.exe" if os.name == "nt" else "bin/python3")
        )
        member = tarfile.TarInfo(name)
        member.size = 6
        archive.addfile(member, io.BytesIO(b"python"))
    (directory / ("uv.exe" if os.name == "nt" else "uv")).write_bytes(b"uv")
    path = delivery / bundle.MANIFEST
    document = json.loads(path.read_text())
    document["files"].update(bundle.inventory(delivery, ("toolchain",)))
    path.write_text(json.dumps(document))


def test_wheel_delivery_uses_installed_uv_without_host_path(
    delivery, tmp_path, monkeypatch
):
    prefix = tmp_path / "application"
    uv = prefix / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv")
    uv.parent.mkdir(parents=True)
    uv.touch()
    commands = []

    def run(command):
        commands.append(command)
        if command[1] == "venv":
            Path(command[-1]).mkdir()

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(bundle.sys, "prefix", str(prefix))
    monkeypatch.setattr(bundle, "_run_install", run)
    bundle.install_bundle(delivery, tmp_path / "author")
    assert all(command[0] == str(uv) for command in commands)


def test_toolchain_build_ignores_managed_aliases_and_preserves_input(
    delivery, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from lab_tools import toolchain

    before = (delivery / bundle.MANIFEST).read_bytes()
    commands = []
    relative = "python.exe" if os.name == "nt" else "bin/python3"

    def run(command, **_kwargs):
        commands.append(command)
        if command[1:3] == ["python", "install"]:
            managed = Path(command[command.index("--install-dir") + 1])
            root = managed / "cpython-exact-version"
            python = root / relative
            python.parent.mkdir(parents=True)
            python.write_bytes(b"portable python")
            if os.name != "nt":
                (managed / "cpython-minor-alias").symlink_to(
                    root, target_is_directory=True
                )
            return SimpleNamespace(returncode=0)
        portable = Path(command[0]).parents[0 if os.name == "nt" else 1]
        return SimpleNamespace(
            stdout=json.dumps([toolchain.platform.python_version(), str(portable)])
        )

    monkeypatch.setattr(toolchain.subprocess, "run", run)
    output = toolchain.build(delivery, tmp_path / "packaged")
    assert (delivery / bundle.MANIFEST).read_bytes() == before
    assert not (delivery / "toolchain").exists()
    assert bundle.verify_bundle(output)["files"]["toolchain/python.tar"]
    assert (output / "toolchain/LICENSE-MIT").is_file()
    assert "--no-bin" in commands[0] and "--no-registry" in commands[0]
    with tarfile.open(output / "toolchain/python.tar") as archive:
        assert all(not member.issym() for member in archive.getmembers())


def test_installer_rejects_other_platform(delivery, tmp_path):
    path = delivery / bundle.MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    data["target"]["machine"] = "other-architecture"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ABI"):
        bundle.install_bundle(delivery, tmp_path / "environment")


def test_manifest_paths_stay_inside_delivery(delivery):
    path = delivery / bundle.MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"]["../outside"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="无效交付文件"):
        bundle.verify_bundle(delivery)


def test_start_checks_reused_service_gui(delivery, monkeypatch):
    from types import SimpleNamespace

    import httpx2 as httpx

    from lab_tools.application_runtime import _check_served_gui

    record = SimpleNamespace(base_url="http://127.0.0.1:12345")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(404),
    )
    with pytest.raises(ValueError, match="服务保持运行"):
        _check_served_gui(record, delivery / "gui")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            200, content=(delivery / "gui/index.html").read_bytes()
        ),
    )
    _check_served_gui(record, delivery / "gui")


def test_public_install_bundle_still_refuses_existing_destination(delivery, tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="已存在"):
        bundle.install_bundle(delivery, destination)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs privileges")
def test_application_paths_reject_redirected_runtime_directory(tmp_path):
    from lab_tools.application_runtime import ApplicationRuntime

    home = tmp_path / "application"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "runtime").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        ApplicationRuntime(home)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("problem", ["relative", "missing", "changed"])
def test_installer_receipt_never_guesses_a_replacement(delivery, tmp_path, problem):
    prefix = tmp_path / "environment"
    prefix.mkdir()
    root = delivery if problem != "relative" else Path("../delivery")
    digest = bundle.file_hash(delivery / bundle.MANIFEST)
    (prefix / bundle.RECEIPT).write_text(
        json.dumps(
            {
                "bundle": str(root),
                "manifest_sha256": digest,
            }
        )
    )
    if problem == "missing":
        delivery.rename(tmp_path / "moved-delivery")
    elif problem == "changed":
        (delivery / bundle.MANIFEST).write_text("{}")
    with pytest.raises(
        ValueError,
        match={
            "relative": "绝对路径",
            "missing": "资源不存在",
            "changed": "安装记录不同",
        }[problem],
    ):
        bundle.installed_bundle(prefix)


def test_delivery_resources_are_independent_of_gui_location(
    delivery, tmp_path, monkeypatch
):
    from lab_tools import application_runtime, author_environment
    from lab_tools.application_runtime import ApplicationRuntime

    prefix = tmp_path / "target-environment"
    prefix.mkdir()
    digest = bundle.file_hash(delivery / bundle.MANIFEST)
    (prefix / bundle.RECEIPT).write_text(
        json.dumps(
            {
                "bundle": str(delivery),
                "manifest_sha256": digest,
            }
        )
    )
    gui = tmp_path / "separate-workbench"
    gui.mkdir()
    (gui / "index.html").write_text("GUI elsewhere")
    requests = []

    def probe(_python, request):
        requests.append(request)
        return {
            "static_dir": str(gui),
            "environment": {"prefix": str(prefix)},
            "settings_identity": None,
            "adapter_identity": None,
        }

    monkeypatch.setattr(application_runtime, "runtime_command", probe)
    runtime = ApplicationRuntime(tmp_path / "home")
    selected = runtime.configure(static_dir=gui)
    assert selected.delivery_root == delivery
    assert selected.delivery_manifest_sha256 == digest
    assert author_environment._bundle(runtime) == delivery
    assert "delivery_root" not in requests[0]
    manifest = delivery / bundle.MANIFEST
    original = manifest.read_bytes()
    manifest.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="安装记录不同"):
        runtime.qualify(selected.python, gui, delivery_root=delivery)
    manifest.write_bytes(original)
    # No installation receipt in the calling/source environment is needed when
    # the native host explicitly supplies its current payload.
    (prefix / bundle.RECEIPT).unlink()
    candidate = runtime.qualify(selected.python, gui, delivery_root=delivery)
    runtime.select(candidate)
    assert runtime.installation() == selected
    # Merely qualifying the GUI must not scan dependency wheels.
    (delivery / "wheels/example.whl").write_bytes(b"modified")
    assert runtime.qualify(selected.python, gui, delivery_root=delivery) == selected
    with pytest.raises(ValueError, match="被修改"):
        author_environment._bundle(runtime)
    (delivery / bundle.MANIFEST).write_text("{}")
    with pytest.raises(ValueError, match="已登记交付清单不同"):
        author_environment._bundle(runtime)


def test_existing_author_environment_needs_no_delivery_and_failed_rebuild_keeps_it(
    tmp_path,
    monkeypatch,
):
    import sys

    from lab_tools.application_runtime import ApplicationRuntime, Installation
    from lab_tools.author_environment import (
        create_client_environment,
        environment_python,
    )

    runtime = ApplicationRuntime(tmp_path / "home")
    runtime.selection.parent.mkdir(parents=True)
    runtime.selection.write_text(
        Installation(
            python=Path(sys.executable),
            static_dir=tmp_path / "gui",
            environment={},
        ).model_dump_json()
    )
    workspace = tmp_path / "authors"
    python = environment_python(workspace / ".venv")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"existing interpreter")
    monkeypatch.setattr("lab_tools.author_environment.check", lambda _r, _w, p: p)
    assert create_client_environment(runtime, workspace) == python
    with pytest.raises(ValueError, match="未登记作者环境资源"):
        create_client_environment(runtime, workspace, rebuild=True)
    assert python.read_bytes() == b"existing interpreter"
    assert not list(workspace.glob(".venv-retained-*"))
