"""Reject stale GUI and invalid deliveries before touching a user environment."""

import json
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


def test_gui_requires_current_runtime_and_unchanged_assets(delivery: Path):
    assert bundle.gui_directory(delivery, {"scopecat": "current"}) == delivery / "gui"
    with pytest.raises(ValueError, match="运行时代码不匹配"):
        bundle.gui_directory(delivery, {"scopecat": "other-build-same-version"})
    (delivery / "gui/index.html").write_text("<html>stale GUI</html>")
    with pytest.raises(ValueError, match="被修改"):
        bundle.gui_directory(delivery, {"scopecat": "current"})


def test_corrupt_wheel_blocks_install_before_environment_creation(delivery, tmp_path):
    (delivery / "wheels/example.whl").write_bytes(b"changed")
    destination = tmp_path / "environment"
    with pytest.raises(ValueError, match="被修改"):
        bundle.install_bundle(delivery, destination)
    assert not destination.exists()
    # Starting an installed GUI does not reread all third-party wheels.
    assert bundle.gui_directory(delivery, {"scopecat": "current"}) == delivery / "gui"


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

    from lab_tools.cli import check_served_gui
    from scopecat.daemon import endpoint

    monkeypatch.setattr(
        endpoint,
        "read_daemon_endpoint_record",
        lambda _project: SimpleNamespace(base_url="http://127.0.0.1:12345"),
    )
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(404),
    )
    with pytest.raises(ValueError, match="服务保持运行"):
        check_served_gui(delivery, delivery / "gui")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            200, content=(delivery / "gui/index.html").read_bytes()
        ),
    )
    check_served_gui(delivery, delivery / "gui")


@pytest.fixture
def fake_runtime(monkeypatch):
    """Exercise publication without downloading or installing dependencies."""
    installs = []

    def install(source, environment):
        installs.append(environment)
        environment.mkdir()
        (environment / bundle.RECEIPT).write_text(
            json.dumps(
                {
                    "bundle": str(source),
                    "manifest_sha256": bundle.file_hash(source / bundle.MANIFEST),
                }
            )
        )
        return environment

    monkeypatch.setattr(bundle, "install_bundle", install)
    monkeypatch.setattr(bundle.subprocess, "run", lambda *_args, **_kwargs: None)
    return installs


def test_interrupted_copy_is_not_published_and_retry_retains_it(
    delivery, tmp_path, monkeypatch, fake_runtime
):
    home = tmp_path / "home"
    copytree = bundle.shutil.copytree

    def fail_copy(source, destination, **kwargs):
        destination.mkdir()
        (destination / "partial").write_text("interrupted copy")
        raise OSError("copy interrupted")

    monkeypatch.setattr(bundle.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="copy interrupted"):
        bundle.install_home(delivery, home)
    assert not list(home.glob("releases/*/bundle"))
    assert not (home / "lab.py").exists()
    monkeypatch.setattr(bundle.shutil, "copytree", copytree)
    assert bundle.install_home(delivery, home).is_file()
    assert len(fake_runtime) == 1
    assert len(list(home.glob("releases/*/bundle-staging-*/partial"))) == 1


def test_interrupted_runtime_retries_at_final_path_preserving_failed_attempt(
    delivery, tmp_path, monkeypatch, fake_runtime
):
    install = bundle.install_bundle

    def fail_install(source, destination):
        destination.mkdir()
        (destination / "partial").write_text("incomplete runtime")
        raise OSError("install interrupted")

    home = tmp_path / "home"
    monkeypatch.setattr(bundle, "install_bundle", fail_install)
    with pytest.raises(OSError, match="install interrupted"):
        bundle.install_home(delivery, home)
    monkeypatch.setattr(bundle, "install_bundle", install)
    bundle.install_home(delivery, home)
    assert fake_runtime[0].name == "runtime"
    assert len(list(home.glob("releases/*/runtime-failed-*/partial"))) == 1
    bundle.install_home(delivery, home)
    assert len(fake_runtime) == 1


def test_receipt_mismatch_does_not_move_completed_runtime_or_select_it(
    delivery, tmp_path, fake_runtime
):
    home = tmp_path / "home"
    launcher = bundle.install_home(delivery, home)
    old = launcher.read_bytes()
    receipt = fake_runtime[0] / bundle.RECEIPT
    receipt.write_text(json.dumps({"bundle": "wrong", "manifest_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="交付记录"):
        bundle.install_home(delivery, home)
    assert launcher.read_bytes() == old
    assert not list(home.glob("releases/*/runtime-failed-*"))


@pytest.mark.parametrize("failure", ["entry", "cmd", "selection"])
def test_failed_validation_or_publication_keeps_old_selection(
    delivery, tmp_path, monkeypatch, fake_runtime, failure
):
    home = tmp_path / "home"
    launcher = bundle.install_home(delivery, home)
    old = launcher.read_bytes()
    manifest = delivery / bundle.MANIFEST
    document = json.loads(manifest.read_text())
    document["sources"] = {"revision": "next"}
    manifest.write_text(json.dumps(document))
    replace = bundle.os.replace

    def fail_replace(source, destination):
        if destination.name == ("lab.cmd" if failure == "cmd" else "lab.py"):
            raise OSError("publish failed")
        return replace(source, destination)

    def fail_check(*args, **kwargs):
        raise bundle.subprocess.CalledProcessError(1, "installed --help")

    if failure == "entry":
        monkeypatch.setattr(bundle.subprocess, "run", fail_check)
        expected = bundle.subprocess.CalledProcessError
    else:
        monkeypatch.setattr(bundle.os, "replace", fail_replace)
        expected = OSError
    with pytest.raises(expected):
        bundle.install_home(delivery, home)
    assert launcher.read_bytes() == old
    assert len(fake_runtime) == 2
    assert all(environment.is_dir() for environment in fake_runtime)
    monkeypatch.setattr(bundle.os, "replace", replace)
    monkeypatch.setattr(bundle.subprocess, "run", lambda *_args, **_kwargs: None)
    bundle.install_home(delivery, home)
    assert launcher.read_bytes() != old
    assert len(fake_runtime) == 2


def test_concurrent_installations_reuse_one_complete_runtime(
    delivery, tmp_path, monkeypatch, fake_runtime
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    home = tmp_path / "home"
    install = bundle.install_bundle
    installing = Event()
    finish = Event()

    def slow_install(source, environment):
        result = install(source, environment)
        installing.set()
        assert finish.wait(5)
        return result

    monkeypatch.setattr(bundle, "install_bundle", slow_install)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bundle.install_home, delivery, home)
        assert installing.wait(5)
        second = pool.submit(bundle.install_home, delivery, home)
        try:
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
            assert not (home / "lab.py").exists()
        finally:
            finish.set()
        assert first.result() == second.result() == home / "lab.py"
    assert len(fake_runtime) == 1
    assert not list(home.glob("releases/*/runtime-failed-*"))


@pytest.mark.parametrize("destination", ["releases", "lab.py", ".install.lock"])
def test_managed_symlink_destinations_rejected(
    delivery, tmp_path, fake_runtime, destination
):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / destination).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        bundle.install_home(delivery, home)
    assert list(outside.iterdir()) == []


def test_installed_launchers_select_notebook_and_quote_shell_paths(
    delivery, tmp_path, fake_runtime, monkeypatch
):
    import runpy
    import shlex
    import sys

    home = tmp_path / "实验室's application"
    launcher = bundle.install_home(delivery, home)
    calls = []
    monkeypatch.setattr(
        bundle.subprocess, "call", lambda command: calls.append(command) or 0
    )
    monkeypatch.setattr(sys, "argv", [str(launcher), "notebook", "--no-browser"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(launcher), run_name="__main__")
    assert exited.value.code == 0
    assert calls[0][1:] == [
        "-m",
        "lab_tools.author_notebook",
        "--home",
        str(home),
        "--no-browser",
    ]
    script = (home / "Scopecat.command").read_text()
    assert shlex.quote("./" + Path(calls[0][0]).relative_to(home).as_posix()) in script
    assert (home / "Scopecat.command").stat().st_mode & 0o111
    assert (home / "Notebook.command").stat().st_mode & 0o111
    assert 'notebook "$@"' in (home / "Notebook.command").read_text()
    assert '--manage "$@"' in (home / "Manage.command").read_text()
    assert (home / "Manage.command").stat().st_mode & 0o111


def test_public_install_bundle_still_refuses_existing_destination(delivery, tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="已存在"):
        bundle.install_bundle(delivery, destination)


def test_receipt_publication_interruption_is_retryable(delivery, tmp_path, monkeypatch):
    home = tmp_path / "home"
    replace = bundle.os.replace
    installs = []

    def run(args, **kwargs):
        if args[1] == "venv":
            destination = Path(args[-1])
            installs.append(destination)
            destination.mkdir()

    def fail_receipt(source, destination):
        if destination.name == bundle.RECEIPT:
            raise OSError("receipt publication interrupted")
        return replace(source, destination)

    monkeypatch.setattr(bundle.shutil, "which", lambda _: "fake-uv")
    monkeypatch.setattr(bundle.subprocess, "run", run)
    monkeypatch.setattr(bundle.os, "replace", fail_receipt)
    with pytest.raises(OSError, match="receipt publication interrupted"):
        bundle.install_home(delivery, home)
    assert not (installs[0] / bundle.RECEIPT).exists()
    assert not (home / "lab.py").exists()
    monkeypatch.setattr(bundle.os, "replace", replace)
    bundle.install_home(delivery, home)
    assert installs == [installs[0]] * 2
    assert (installs[0] / bundle.RECEIPT).is_file()
    assert len(list(home.glob("releases/*/runtime-failed-*"))) == 1
