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


def test_release_order() -> None:
    versions = [
        "0.3.0-alpha.1",
        "0.3.0-beta.1",
        "0.3.0-rc.1",
        "0.3.0-rc.2",
        "0.3.0",
        "0.3.1",
    ]
    assert all(version_key(a) < version_key(b) for a, b in pairwise(versions))


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


def test_future_package_tags_do_not_block_application(tmp_path: Path) -> None:
    root = repository(tmp_path)
    for tag in ("sdk-v99.0.0", "v99-invalid", "preview-123"):
        subprocess.run(["git", "tag", tag], cwd=root, check=True)
    check_candidate(root, "0.3.0-rc.2", 5)


@pytest.mark.parametrize("api_result", ["v0.4.0\n", "api-error"])
def test_remote_draft_or_api_failure_blocks_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api_result: str
) -> None:
    root = repository(tmp_path)
    (root / "release.toml").write_text('version = "0.4.0"\nbuild_number = 5\n')
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repository")
    original = subprocess.check_output

    def output(command: list[str], *, cwd: Path, text: bool) -> str:
        if command[0] != "gh":
            return str(original(command, cwd=cwd, text=text))
        assert "--paginate" in command
        if api_result == "api-error":
            raise subprocess.CalledProcessError(1, command)
        return api_result

    monkeypatch.setattr(subprocess, "check_output", output)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        release.check_publish(root, remote=True)
