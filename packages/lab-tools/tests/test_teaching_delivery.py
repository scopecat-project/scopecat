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
    data = json.loads(path.read_text())
    data["target"]["machine"] = "other-architecture"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ABI"):
        bundle.install_bundle(delivery, tmp_path / "environment")


def test_manifest_paths_stay_inside_delivery(delivery):
    path = delivery / bundle.MANIFEST
    data = json.loads(path.read_text())
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
