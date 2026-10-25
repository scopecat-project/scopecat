"""Build supply must be exact, relocatable, and prepared before dependency work."""

import json
import os
import platform
import subprocess
import tarfile
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.app_build import toolchain


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "_cache_root", lambda _: tmp_path / "cache")
    (tmp_path / ".python-version").write_text(platform.python_version() + "\n")
    (tmp_path / "uv.lock").write_text(
        f'[[package]]\nname="uv"\nversion="{version("uv")}"\n'
    )
    return tmp_path


@pytest.mark.parametrize("selected", ["3.15", "3.15.0rc2", "3.14.0", "3.15.0t"])
def test_baseline_rejects_drift(repository, selected, monkeypatch):
    monkeypatch.setattr(toolchain.platform, "python_version", lambda: "3.15.0")
    (repository / ".python-version").write_text(selected)
    with pytest.raises(ValueError, match=r"exact stable|Build Python drift"):
        toolchain.baseline(repository)


def test_uv_drift_fails_before_supply(repository, monkeypatch):
    (repository / "uv.lock").write_text('[[package]]\nname="uv"\nversion="0.0.1"\n')
    monkeypatch.setattr(
        toolchain.subprocess, "run", lambda *_a, **_k: pytest.fail("supply lookup")
    )
    with (
        pytest.raises(ValueError, match="Build uv drift"),
        toolchain.prepare(repository, repository / "toolchain"),
    ):
        pytest.fail("prepared mismatched uv")


def test_missing_supply_is_specific_and_early(repository, monkeypatch):
    monkeypatch.setattr(
        toolchain.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout="[]")
    )
    with (
        pytest.raises(ValueError, match=r"supply unavailable.*uv=.*found 0"),
        toolchain.prepare(repository, repository / "toolchain"),
    ):
        pytest.fail("prepared missing supply")
    assert not (repository / "toolchain").exists()


@pytest.mark.parametrize(
    "failure", [None, "download", "relocation", "variant", "target"]
)
def test_prepare_archives_clean_base_and_retains_supply(
    repository, monkeypatch, failure
):
    commands = []
    selected = {
        "key": "cpython-exact-test",
        "implementation": "cpython",
        "os": {"darwin": "macos", "win32": "windows"}.get(
            toolchain.target_identity()["platform"],
            toolchain.target_identity()["platform"],
        ),
        "arch": {"arm64": "aarch64", "amd64": "x86_64"}.get(
            platform.machine().lower(), platform.machine().lower()
        ),
        "libc": "gnu" if os.name != "nt" and platform.system() == "Linux" else "none",
        "version": platform.python_version(),
        "variant": "freethreaded" if failure == "variant" else "default",
        "url": "https://example.invalid/20261013/exact-python.tar.gz",
    }
    if failure == "target":
        selected["arch"] = "wrong-architecture"
    relative = "python.exe" if os.name == "nt" else "bin/python3"

    def run(command, **_kwargs):
        commands.append(command)
        if command[1:3] == ["python", "list"]:
            return SimpleNamespace(stdout=json.dumps([selected]))
        if command[1:3] == ["python", "install"]:
            if failure == "download":
                raise subprocess.CalledProcessError(1, command)
            managed = Path(command[command.index("--install-dir") + 1])
            root = managed / selected["key"]
            python = root / relative
            python.parent.mkdir(parents=True)
            python.write_bytes(b"clean portable python")
            (root / "BUILD").write_text("20261013")
            if os.name != "nt":
                (managed / "cpython-minor-alias").symlink_to(
                    root, target_is_directory=True
                )
            return SimpleNamespace(returncode=0)
        portable = Path(command[0]).parents[0 if os.name == "nt" else 1]
        return SimpleNamespace(
            stdout=json.dumps(
                [
                    "3.14.0" if failure == "relocation" else platform.python_version(),
                    str(portable),
                    "cpython",
                    False,
                    toolchain.target_identity()["platform"],
                    toolchain.target_identity()["machine"],
                ]
            )
        )

    monkeypatch.setattr(toolchain.subprocess, "run", run)
    output = repository / "toolchain"
    if failure:
        with (
            pytest.raises(
                ValueError,
                match={
                    "download": "download failed.*source=",
                    "relocation": "relocation/identity",
                    "variant": "version/variant",
                    "target": "changed target",
                }[failure],
            ),
            toolchain.prepare(repository, output),
        ):
            pytest.fail("invalid runtime accepted")
        return
    with toolchain.prepare(repository, output) as python:
        assert python.read_bytes() == b"clean portable python"
        # Dependency work must not mutate the pristine packaged base.
        (python.parent / "build-only-marker").write_text("dependency work")
    assert not python.exists()
    metadata = json.loads((output / "runtime.json").read_text())
    assert metadata["distribution"] == selected
    assert metadata["distribution_build"] == "20261013"
    assert metadata["archive_sha256"] == toolchain.file_hash(output / "python.tar")
    assert "--no-bin" in commands[1] and "--no-registry" in commands[1]
    with tarfile.open(output / "python.tar") as archive:
        assert all(not item.issym() for item in archive.getmembers())
        assert not any(
            "build-only-marker" in item.name for item in archive.getmembers()
        )

    with toolchain.prepare(repository, repository / "again") as reused:
        assert reused.read_bytes() == b"clean portable python"
    assert sum(command[1:3] == ["python", "install"] for command in commands) == 1
    assert (repository / "again/python.tar").read_bytes() == (
        output / "python.tar"
    ).read_bytes()

    cached = next((repository / "cache").glob("*/python.tar"))
    cached.write_bytes(b"corrupt cached runtime")
    with toolchain.prepare(repository, repository / "repaired") as repaired:
        assert repaired.read_bytes() == b"clean portable python"
    assert sum(command[1:3] == ["python", "install"] for command in commands) == 2


@pytest.mark.parametrize("count", [0, 2])
def test_catalog_check_rejects_nonunique_supply_without_install(
    repository, monkeypatch, count
):
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        assert command[1:3] == ["python", "list"]
        return SimpleNamespace(stdout=json.dumps([{}] * count))

    monkeypatch.setattr(toolchain.subprocess, "run", run)
    with pytest.raises(
        ValueError, match=f"expected one managed distribution, found {count}"
    ):
        toolchain.select_runtime(repository)
    assert len(commands) == 1
