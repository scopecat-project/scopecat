"""Reject stale GUI and invalid deliveries before touching a user environment."""

import errno
import io
import json
import os
import tarfile
from pathlib import Path

import pytest
from scripts.app_build import installer

from scopecat_app import bundle


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


def test_corrupt_wheel_blocks_install_before_environment_creation(
    delivery: Path, tmp_path: Path
):
    (delivery / "wheels/example.whl").write_bytes(b"changed")
    destination = tmp_path / "environment"
    with pytest.raises(ValueError, match="被修改"):
        installer.install_bundle(delivery, destination)
    assert not destination.exists()


def add_toolchain(delivery: Path, *, unsafe=False):
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
    delivery: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    monkeypatch.setattr(installer, "_run_install", run)
    installer.install_bundle(delivery, tmp_path / "author")
    assert all(command[0] == str(uv) for command in commands)


def test_toolchain_reuses_prepared_archive_without_supply_lookup(
    delivery: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scripts.app_build import toolchain

    add_toolchain(delivery)
    before = (delivery / bundle.MANIFEST).read_bytes()
    monkeypatch.setattr(
        toolchain.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("late runtime lookup"),
    )
    output = toolchain.build(delivery, tmp_path / "packaged")
    assert (output / bundle.MANIFEST).read_bytes() == before
    assert (delivery / bundle.MANIFEST).read_bytes() == before
    assert (output / "toolchain/python.tar").read_bytes() == (
        delivery / "toolchain/python.tar"
    ).read_bytes()


def test_toolchain_rejects_unprepared_delivery(delivery, tmp_path):
    from scripts.app_build import toolchain

    with pytest.raises(ValueError, match="rebuild the delivery"):
        toolchain.build(delivery, tmp_path / "packaged")
    assert not (tmp_path / "packaged").exists()


def test_installer_rejects_other_platform(delivery: Path, tmp_path: Path):
    path = delivery / bundle.MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    data["target"]["machine"] = "other-architecture"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ABI"):
        installer.install_bundle(delivery, tmp_path / "environment")


def test_manifest_paths_stay_inside_delivery(delivery: Path):
    path = delivery / bundle.MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"]["../outside"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="无效交付文件"):
        bundle.verify_bundle(delivery)


def test_start_checks_reused_service_gui(
    delivery: Path, monkeypatch: pytest.MonkeyPatch
):
    from datetime import UTC, datetime

    import httpx2 as httpx

    from scopecat.daemon.endpoint import DaemonEndpointRecord
    from scopecat_app.application_runtime import _check_served_gui

    record = DaemonEndpointRecord(
        project_root=delivery,
        data_root=delivery,
        deployment_root=delivery,
        pid=1,
        process_create_time=1.0,
        base_url="http://127.0.0.1:12345",
        shutdown_token="test-only-token-" * 3,
        started_at=datetime.now(UTC),
    )
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


def test_public_install_bundle_still_refuses_existing_destination(
    delivery: Path, tmp_path: Path
):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="已存在"):
        installer.install_bundle(delivery, destination)


def test_application_paths_reject_redirected_runtime_directory(tmp_path: Path):
    from scopecat_app.application_runtime import ApplicationRuntime

    home = tmp_path / "application"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Probe the actual directory-link capability before calling the product.
    try:
        (home / "runtime").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if (
            error.errno not in {errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP}
            and getattr(error, "winerror", None) != 1314
        ):
            raise
        pytest.skip(f"symlink capability unavailable: {error}")
    with pytest.raises(ValueError, match="符号链接"):
        ApplicationRuntime(home)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("problem", ["relative", "missing", "changed"])
def test_installer_receipt_never_guesses_a_replacement(
    delivery: Path, tmp_path: Path, problem
):
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
    delivery: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from scopecat_app import application_runtime, author_environment
    from scopecat_app.application_runtime import ApplicationRuntime

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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import sys

    from scopecat_app.application_runtime import ApplicationRuntime, Installation
    from scopecat_app.author_environment import (
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
    monkeypatch.setattr("scopecat_app.author_environment.check", lambda _r, _w, p: p)
    assert create_client_environment(runtime, workspace) == python
    with pytest.raises(ValueError, match="未登记作者环境资源"):
        create_client_environment(runtime, workspace, rebuild=True)
    assert python.read_bytes() == b"existing interpreter"
    assert not list(workspace.glob(".venv-retained-*"))


def test_standalone_installer_runs_without_installed_application(tmp_path: Path):
    import subprocess
    import sys

    from scripts.app_build.delivery import standalone_installer

    source = Path(__file__).resolve().parents[3]
    installer_path = tmp_path / "install.py"
    installer_path.write_text(standalone_installer(source), encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - owned generated installer
        [sys.executable, "-I", "-S", str(installer_path), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--bundle" in result.stdout
