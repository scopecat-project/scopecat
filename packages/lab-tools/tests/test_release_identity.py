"""Public versions preserve internal package bases and platform constraints."""

import pytest
from packaging.version import Version

from lab_tools.release_identity import (
    ReleaseIdentity,
    read_release,
    read_source_identity,
)


@pytest.mark.parametrize(
    ("public", "python"),
    [
        ("0.3.0", "0.3.0"),
        ("0.3.0-alpha.2", "0.3.0a2"),
        ("0.3.0-beta.1", "0.3.0b1"),
        ("0.3.0-rc.4", "0.3.0rc4"),
    ],
)
def test_public_version_conversions(public, python):
    identity = ReleaseIdentity(public, 9)
    identity.require_release()
    assert identity.pep440 == python
    assert identity.short_version == "0.3.0"
    assert identity.windows_version == "0.3.0.9"
    wheel = identity.python_package_version("0.1.0", "a" * 40, "123")
    assert Version(wheel).is_devrelease
    assert wheel == f"0.1.0.dev123+scopecat.{python}.g{'a' * 12}"
    assert identity.javascript_package_version("0.1.0", "a" * 40).startswith(
        "0.1.0-scopecat.0.3.0"
    )


@pytest.mark.parametrize(
    "version", ["01.2.3", "0.3", "0.3.0-rc.0", "0.3.0+foo", "0.3.0-dev.1", "65536.0.0"]
)
def test_invalid_public_versions(version):
    with pytest.raises(ValueError, match="Public version"):
        ReleaseIdentity(version, 1)


def test_placeholder_cannot_be_released(tmp_path):
    path = tmp_path / "release.toml"
    path.write_text('version="0.0.0"\nbuild_number=0\n')
    assert read_release(path).build_number == 0
    with pytest.raises(ValueError, match="explicit version"):
        read_release(path, require_release=True)


@pytest.mark.parametrize("number", [True, -1, 65536])
def test_invalid_native_build(number):
    with pytest.raises(ValueError, match="build_number"):
        ReleaseIdentity("0.3.0", number)


def test_native_build_number_respects_macos_single_field_limit() -> None:
    assert ReleaseIdentity("0.3.0", 9999).windows_version == "0.3.0.9999"
    with pytest.raises(ValueError, match=r"0\.\.9999"):
        ReleaseIdentity("0.3.0", 10000)


def test_historical_source_without_release_file_only_allows_preview(tmp_path):
    missing = tmp_path / "release.toml"
    assert read_source_identity(missing) == ReleaseIdentity("0.0.0", 0)
    with pytest.raises(FileNotFoundError):
        read_source_identity(missing, release=True)
    with pytest.raises(FileNotFoundError):
        read_release(missing)
    missing.write_text('version="0.0.0"\nbuild_number=0\n')
    with pytest.raises(ValueError, match="explicit version"):
        read_source_identity(missing, release=True)


def test_preview_build_archives_historical_commit_without_release_file(
    tmp_path, monkeypatch
):
    import importlib.util
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts/build_preview.py"
    spec = importlib.util.spec_from_file_location("historical_preview_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    repository = tmp_path / "repo"
    ui = repository / "apps/scopecat-ui"
    (ui / "dist").mkdir(parents=True)
    (ui / "package.json").write_text('{"version":"0.1.0"}')
    (ui / "dist/index.html").write_text("historical GUI")
    for arguments in (
        ["init"],
        ["add", "."],
        [
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "historical source",
        ],
    ):
        subprocess.run(  # noqa: S603 - fixed test-only git arguments
            ["git", *arguments],  # noqa: S607 - standard maintainer tool
            cwd=repository,
            check=True,
            capture_output=True,
        )
    original_run = module.subprocess.run

    def run(command, **kwargs):
        if command[0] == "pnpm":
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, **kwargs)

    monkeypatch.setattr(module, "PACKAGES", ())
    monkeypatch.setattr(module.subprocess, "run", run)
    manifest = module.build(repository, tmp_path / "preview")
    metadata = json.loads(manifest.read_text())
    assert metadata["release_version"] == "0.0.0"
    assert metadata["channel"] == "preview"
    assert "scopecat-ui.zip" in metadata["files"]
    with pytest.raises(FileNotFoundError):
        module.build(repository, tmp_path / "release", release=True)
