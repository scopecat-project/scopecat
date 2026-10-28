"""Coordinated wheel identities preserve platform constraints."""

from pathlib import Path

import pytest
from packaging.version import Version
from scripts.app_build.release_identity import (
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
    wheel = identity.python_package_version("a" * 40, "123")
    assert identity.python_package_version("a" * 40, "123", release=True) == python
    assert Version(wheel).is_devrelease
    assert wheel == f"{python}.dev123+g{'a' * 40}"
    assert identity.javascript_package_version() == public


@pytest.mark.parametrize(
    "version", ["01.2.3", "0.3", "0.3.0-rc.0", "0.3.0+foo", "0.3.0-dev.1", "65536.0.0"]
)
def test_invalid_public_versions(version):
    with pytest.raises(ValueError, match="Public version"):
        ReleaseIdentity(version, 1)


def test_placeholder_cannot_be_released(tmp_path: Path):
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


def test_historical_source_without_release_file_only_allows_preview(tmp_path: Path):
    missing = tmp_path / "release.toml"
    assert read_source_identity(missing) == ReleaseIdentity("0.0.0", 0)
    with pytest.raises(FileNotFoundError):
        read_source_identity(missing, release=True)
    with pytest.raises(FileNotFoundError):
        read_release(missing)
    missing.write_text('version="0.0.0"\nbuild_number=0\n')
    with pytest.raises(ValueError, match="explicit version"):
        read_source_identity(missing, release=True)


@pytest.mark.parametrize(
    "public,release",
    [(None, False), ("0.3.0", False), ("0.3.0", True), ("0.3.0-rc.1", True)],
)
def test_build_ui_version_and_independent_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public: str | None, release: bool
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
    if public is not None:
        (repository / "release.toml").write_text(
            f'version="{public}"\nbuild_number=9\n'
        )
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

    built_versions = []

    def run(command, **kwargs):
        if command[0] == "pnpm":
            if command[1:] == ["run", "build"]:
                built_versions.append(
                    json.loads((kwargs["cwd"] / "package.json").read_text())["version"]
                )
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, **kwargs)

    # This fixture isolates historical release identity and builds no wheels.
    # Portable runtime supply is covered by test_build_runtime and native CI.
    from contextlib import nullcontext

    monkeypatch.setattr(module, "prepare", lambda *_: nullcontext(Path(sys.executable)))
    monkeypatch.setattr(module, "PACKAGES", ())
    monkeypatch.setattr(module.subprocess, "run", run)
    manifest = module.build(repository, tmp_path / "preview", release=release)
    metadata = json.loads(manifest.read_text())
    assert metadata["release_version"] == (public or "0.0.0")
    assert metadata["channel"] == ("release" if release else "preview")
    assert built_versions == [metadata["ui_version"]]
    if release:
        assert metadata["ui_version"] == public
    else:
        assert metadata["ui_version"].startswith("0.1.0-dev.")
        assert metadata["ui_version"].endswith(f".g{metadata['commit'][:12]}")
    assert "scopecat-ui.zip" in metadata["files"]
    import zipfile

    with zipfile.ZipFile(manifest.parent / "scopecat-ui.zip") as archive:
        assert json.loads(archive.read("build-info.json")) == {
            "source_commit": metadata["commit"],
            "ui_version": metadata["ui_version"],
        }
    if public is None:
        with pytest.raises(FileNotFoundError):
            module.build(repository, tmp_path / "release", release=True)


@pytest.mark.parametrize(
    "platform,machine,expected",
    [
        ("darwin", "arm64", "macos-arm64.dmg"),
        ("darwin", "x86_64", "macos-x86_64.dmg"),
        ("win32", "AMD64", "windows-x86_64.exe"),
        ("win32", "aarch64", "windows-arm64.exe"),
    ],
)
def test_installer_names_use_qualified_target(platform, machine, expected):
    from scripts.app_build.release_identity import installer_filename

    assert (
        installer_filename(
            ReleaseIdentity("0.3.0-rc.1", 9), {"platform": platform, "machine": machine}
        )
        == f"Scopecat-0.3.0-rc.1-{expected}"
    )


def test_installer_names_reject_unknown_target():
    from scripts.app_build.release_identity import installer_filename

    with pytest.raises(ValueError, match="Unsupported"):
        installer_filename(
            ReleaseIdentity("0.3.0", 9), {"platform": "win32", "machine": "unknown"}
        )
