"""Release preparation rejects reused identities before changing source files."""

# ruff: noqa: S603, S607 -- isolated local Git fixture
import importlib.util
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/release.py"
spec = importlib.util.spec_from_file_location("release_prepare", SCRIPT)
assert spec is not None and spec.loader is not None
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
check_candidate = release.check_candidate
release_notes = release.release_notes
version_key = release.version_key


def repository(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "tag.gpgsign", "false"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "plain commit",
        ],
        check=True,
    )
    (tmp_path / "release.toml").write_text('version = "0.3.0-rc.1"\nbuild_number = 4\n')
    return tmp_path


def test_candidate_requires_increasing_version_and_build(tmp_path: Path) -> None:
    root = repository(tmp_path)
    check_candidate(root, "0.3.0-rc.2", 5)
    for version, build in [("0.3.0-rc.1", 5), ("0.2.9", 5), ("0.3.0", 4)]:
        with pytest.raises(ValueError, match="increase"):
            check_candidate(root, version, build)
    subprocess.run(["git", "tag", "v0.4.0"], cwd=root, check=True)
    with pytest.raises(ValueError, match="existing tag"):
        check_candidate(root, "0.4.0", 5)


def test_release_order_and_current_notes(tmp_path: Path) -> None:
    versions = [
        "0.3.0-alpha.1",
        "0.3.0-beta.1",
        "0.3.0-rc.1",
        "0.3.0-rc.2",
        "0.3.0",
        "0.3.1",
    ]
    assert all(version_key(a) < version_key(b) for a, b in pairwise(versions))
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 0.3.0 (2026-10-04)\n\nNew user behavior.\n\n## 0.2.0\nOld.\n"
    )
    assert release_notes(tmp_path, "0.3.0") == "New user behavior.\n"
    with pytest.raises(ValueError, match="changelog"):
        release_notes(tmp_path, "0.4.0")


def test_publication_rejects_older_version_and_native_build(tmp_path: Path) -> None:
    root = repository(tmp_path)
    subprocess.run(["git", "add", "release.toml"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "release",
        ],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "tag", "v0.3.0-rc.1"], cwd=root, check=True)
    for version, build in [("0.3.0-beta.1", 5), ("0.3.0-rc.1", 5), ("0.4.0", 4)]:
        (root / "release.toml").write_text(
            f'version = "{version}"\nbuild_number = {build}\n'
        )
        with pytest.raises(ValueError, match="existing tag"):
            release.check_publish(root)
    (root / "release.toml").write_text('version = "0.4.0"\nbuild_number = 5\n')
    release.check_publish(root)
